#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verbesserter Sitemap Scraper mit:
- Robusterer Fehlerbehandlung
- Retry-Logik
- Besserer URL-Deduplizierung
- Parallelem Scraping (optional)
- Detailliertem Logging
"""

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import sys
import threading
import time
from html import unescape
from pathlib import Path
from typing import List, Optional, Tuple, Set, Union
from urllib.parse import urlparse, unquote, urljoin, parse_qs
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

try:
    from markdownify import markdownify
except ImportError:
    markdownify = None

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None


DEFAULT_TIMEOUT = 30  # erhöht von 15
OUTPUT_DIR = Path(__file__).parent / "output_markdown"
SUMMARY_PATH = Path(__file__).parent / "summary.json"
MAX_RETRIES = 3
RETRY_DELAY = 2  # Sekunden
MAX_WORKERS = 5  # für paralleles Scraping


class SitemapScraper:
    def __init__(
        self,
        max_workers: int = MAX_WORKERS,
        verbose: bool = True,
        output_dir: Union[str, Path] = OUTPUT_DIR,
        summary_path: Union[str, Path] = SUMMARY_PATH,
    ):
        self.max_workers = max_workers
        self.verbose = verbose
        self.output_dir = Path(output_dir)
        self.summary_path = Path(summary_path)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def should_skip_url(self, url: str) -> bool:
        """Überspringt aktuell englische Varianten wie /en/... oder en.example.com."""
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").strip().lower()
            if host.startswith("en."):
                return True

            path = (parsed.path or "").strip("/")
            if not path:
                return False

            first_segment = path.split("/", 1)[0].strip().lower()
            if first_segment in {"en", "en-us", "en-gb", "english"}:
                return True
        except Exception:
            return False
        return False
        
    def log(self, msg: str, level: str = "info") -> None:
        """Einfaches Logging"""
        if self.verbose:
            prefix = {
                "info": "[INFO]",
                "warn": "[WARN]",
                "error": "[ERROR]",
                "success": "[OK]"
            }.get(level, "[LOG]")
            print(f"{prefix} {msg}")

    def fetch_with_retry(self, url: str, max_retries: int = MAX_RETRIES) -> Optional[requests.Response]:
        """Fetch mit automatischen Retries"""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                if attempt < max_retries - 1:
                    self.log(f"Retry {attempt + 1}/{max_retries} für {url}: {exc}", "warn")
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    self.log(f"Fehlgeschlagen nach {max_retries} Versuchen: {url} - {exc}", "error")
                    return None
        return None

    def fetch_sitemap_urls(self, sitemap_url: str) -> List[str]:
        """
        Lädt alle URLs aus dem Sitemap (inkl. nested sitemaps).
        Verbesserte Version mit besserer Fehlerbehandlung.
        """
        seen_pages: Set[str] = set()
        visited_sitemaps: Set[str] = set()
        ordered_urls: List[str] = []
        skipped_english = 0

        def _normalize_url(url: str) -> str:
            """Normalisiere URL (entfernt nur fragments, behält alles andere)"""
            parsed = urlparse(url)
            # Behalte trailing slashes und queries bei - entferne nur Fragment
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                normalized += f"?{parsed.query}"
            return normalized

        def _walk(url: str, depth: int = 0) -> None:
            nonlocal skipped_english
            if depth > 10:  # Schutz vor unendlicher Rekursion
                self.log(f"Max depth erreicht für {url}", "warn")
                return
                
            normalized = _normalize_url(url)
            if normalized in visited_sitemaps:
                return
            visited_sitemaps.add(normalized)

            self.log(f"Lade Sitemap (Tiefe {depth}): {url}")
            resp = self.fetch_with_retry(url)
            if not resp:
                return

            content_bytes = resp.content
            # Unterstütze gz-Sitemaps
            if resp.headers.get("Content-Type", "").lower().startswith("application/x-gzip") or url.lower().endswith(".gz"):
                try:
                    content_bytes = gzip.decompress(content_bytes)
                    self.log(f"Entpacke .gz Sitemap: {url}")
                except Exception as exc:
                    self.log(f"Konnte .gz nicht entpacken ({url}): {exc}", "error")
                    return

            # Fallback: Text-Sitemap (.txt)
            if url.lower().endswith(".txt"):
                try:
                    text = content_bytes.decode("utf-8", errors="ignore")
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            if self.should_skip_url(line):
                                skipped_english += 1
                                continue
                            ordered_urls.append(line)
                    return
                except Exception as exc:
                    self.log(f"Konnte Text-Sitemap nicht lesen ({url}): {exc}", "error")
                    return

            try:
                # Parse XML mit besserer Fehlerbehandlung
                tree = ET.fromstring(content_bytes)
            except ET.ParseError as exc:
                self.log(f"XML Parse Error für {url}: {exc}", "error")
                return

            # Namespace-aware parsing
            ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            
            # Prüfe auf Sitemap Index
            sitemap_locs = tree.findall('.//sm:sitemap/sm:loc', ns) or tree.findall('.//{*}sitemap/{*}loc')
            if sitemap_locs:
                self.log(f"Sitemap Index gefunden mit {len(sitemap_locs)} child sitemaps")
                for loc_el in sitemap_locs:
                    if loc_el.text:
                        child_url = loc_el.text.strip()
                        _walk(child_url, depth + 1)
                return

            # Normale Sitemap mit URLs
            url_locs = tree.findall('.//sm:url/sm:loc', ns) or tree.findall('.//{*}url/{*}loc')
            if not url_locs:
                # Fallback: alle <loc> tags
                url_locs = tree.findall('.//{*}loc')
            
            new_count = 0
            skipped_here = 0
            duplicates = []
            for loc_el in url_locs:
                if loc_el.text:
                    page_url = loc_el.text.strip()
                    if self.should_skip_url(page_url):
                        skipped_here += 1
                        skipped_english += 1
                        continue
                    normalized_page = _normalize_url(page_url)
                    
                    if normalized_page not in seen_pages:
                        seen_pages.add(normalized_page)
                        ordered_urls.append(page_url)
                        new_count += 1
                    else:
                        duplicates.append(page_url)
            
            if duplicates and self.verbose:
                self.log(f"Duplikate übersprungen: {len(duplicates)}", "warn")
                for dup in duplicates[:5]:  # Zeige erste 5
                    self.log(f"  - {dup}", "warn")
                if len(duplicates) > 5:
                    self.log(f"  ... und {len(duplicates) - 5} weitere", "warn")
            
            self.log(
                f"Gefunden: {len(url_locs)} URLs ({new_count} neu, {len(ordered_urls)} gesamt, "
                f"{skipped_here} EN übersprungen)",
                "success",
            )

        _walk(sitemap_url)
        self.log(f"Sitemap-Scan abgeschlossen: {len(ordered_urls)} eindeutige URLs gefunden", "success")
        if skipped_english:
            self.log(f"Englische URLs übersprungen: {skipped_english}", "info")
        return ordered_urls

    def safe_slug_from_url(self, url: str) -> str:
        """Erstellt einen stabilen Dateinamen aus der URL (ohne laufende -1/-2 Suffixe)."""
        parsed = urlparse(url)
        path = unquote(parsed.path)
        
        if not path or path == "/":
            slug = "index"
        else:
            # Nutze den gesamten Pfad für bessere Eindeutigkeit.
            parts = [p for p in path.rstrip("/").split("/") if p]
            slug = "-".join(parts) if parts else "index"
        
        # Bereinige den Slug
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-")[:120]
        slug = slug or "index"

        # Query-Parameter unterscheiden Seiten ebenfalls.
        if parsed.query:
            query_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", parsed.query).strip("-")[:40]
            if query_slug:
                slug = f"{slug}-q-{query_slug}"
        
        return slug

    def _url_key(self, url: str) -> str:
        """Normalisierte URL als stabiler Schlüssel für Mapping/Hashes."""
        parsed = urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            key += f"?{parsed.query}"
        return key

    def _build_slug_map(self, urls: List[str]) -> dict:
        """Erstellt ein stabiles URL->Slug Mapping."""
        base_to_urls = {}
        for url in urls:
            base = self.safe_slug_from_url(url)
            base_to_urls.setdefault(base, []).append(url)

        slug_map = {}
        for base, group_urls in base_to_urls.items():
            if len(group_urls) == 1:
                slug_map[group_urls[0]] = base
                continue

            # Bei Kollisionen: stabiler Hash-Suffix statt fortlaufender Nummer.
            for url in group_urls:
                url_hash = hashlib.sha1(self._url_key(url).encode("utf-8")).hexdigest()[:8]
                slug_map[url] = f"{base}-{url_hash}"
        return slug_map

    def _load_previous_scraped_files(self) -> Set[str]:
        """Lädt Dateinamen aus der letzten Summary, um stale Dateien zu entfernen."""
        if not self.summary_path.exists():
            return set()
        try:
            data = json.loads(self.summary_path.read_text(encoding="utf-8"))
        except Exception:
            return set()

        files = set()
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    file_name = entry.get("file")
                    if isinstance(file_name, str) and file_name.strip():
                        files.add(file_name.strip())
        return files

    def _cleanup_stale_files(self, target_files: Set[str]) -> None:
        """Entfernt alte Scrape-Dateien, die in der neuen Runde nicht mehr erzeugt werden."""
        previous_files = self._load_previous_scraped_files()
        stale_files = previous_files - target_files
        if not stale_files:
            return

        for stale_name in sorted(stale_files):
            stale_path = self.output_dir / stale_name
            try:
                if stale_path.exists() and stale_path.is_file():
                    stale_path.unlink()
                    self.log(f"Entferne alte Datei: {stale_name}", "info")
            except Exception as exc:
                self.log(f"Konnte alte Datei nicht entfernen ({stale_name}): {exc}", "warn")

    def extract_main_html(self, soup: BeautifulSoup) -> str:
        """Extrahiert den Hauptinhalt der Seite"""
        # Entferne störende Elemente
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        
        # Suche nach Hauptinhalt
        for selector in ["main", "article", '[role="main"]', ".content", "#content"]:
            found = soup.select_one(selector)
            if found and found.get_text(strip=True):
                return str(found)
        
        # Fallback: größter Container
        body = soup.body or soup
        candidates = body.find_all(["div", "section", "article"], recursive=True)
        
        best = body
        best_len = len(body.get_text(" ", strip=True))
        
        for cand in candidates:
            text = cand.get_text(" ", strip=True)
            if len(text) > best_len:
                best, best_len = cand, len(text)
        
        return str(best) if best else str(soup)

    def html_to_markdown(self, html: str) -> str:
        """Konvertiert HTML zu Markdown"""
        if markdownify:
            try:
                md = markdownify(html, heading_style="ATX", strip=['script', 'style'])
                return md.strip()
            except Exception as exc:
                self.log(f"Markdownify fehlgeschlagen: {exc}", "warn")
        
        # Fallback
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(" ", strip=True)

    def is_pdf_candidate(self, url: str, content_type: str) -> bool:
        """Erkennt PDF-Ressourcen per URL oder Content-Type."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if path.endswith(".pdf"):
            return True
        ctype = (content_type or "").lower()
        if ctype.startswith("application/pdf") or ctype.startswith("application/x-pdf"):
            return True
        return "application/pdf" in ctype

    def normalize_pdf_text_block(self, text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
        normalized = normalized.replace("\u2022", "\n- ").replace("§", "\n- ")
        normalized = re.sub(
            r"(?<!\n)(?P<label>(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9/&() +.-]{2,40}|[A-ZÄÖÜ0-9/&() +.-]{2,40}):)",
            lambda match: "\n" + match.group("label"),
            normalized,
        )
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        normalized = re.sub(r"\n[ \t]+", "\n", normalized)
        normalized = re.sub(r"[ \t]{2,}", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def pdf_to_markdown(self, pdf_bytes: bytes) -> str:
        """Extrahiert Text aus PDF und gibt Markdown-kompatiblen Text zurück."""
        if PdfReader is None:
            raise RuntimeError("PyPDF2 ist nicht installiert.")

        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""
            text = self.normalize_pdf_text_block(text)
            if text:
                parts.append(text)

        content = "\n\n".join(parts).strip()
        if not content:
            raise ValueError("Konnte keinen Text aus dem PDF extrahieren.")
        return content

    def _split_link_header(self, header_value: str) -> List[Tuple[str, str]]:
        """Parst HTTP-Link Header in (url, rel)-Paare."""
        items: List[Tuple[str, str]] = []
        for part in (header_value or "").split(","):
            piece = part.strip()
            match = re.match(r"<([^>]+)>\s*;\s*rel=\"([^\"]+)\"", piece)
            if not match:
                continue
            items.append((match.group(1).strip(), match.group(2).strip()))
        return items

    def _extract_shortlink_post_id(self, link_header: str, fallback_url: str) -> Optional[int]:
        """Ermittelt eine WordPress-Post-ID aus Link-Header oder URL-Query."""
        for linked_url, rel in self._split_link_header(link_header):
            if rel != "shortlink":
                continue
            parsed = urlparse(linked_url)
            post_vals = parse_qs(parsed.query).get("p") or []
            for value in post_vals:
                try:
                    return int(value)
                except ValueError:
                    continue

        parsed_fallback = urlparse(fallback_url or "")
        post_vals = parse_qs(parsed_fallback.query).get("p") or []
        for value in post_vals:
            try:
                return int(value)
            except ValueError:
                continue
        return None

    def _extract_wp_api_root(self, link_header: str, fallback_url: str) -> Optional[str]:
        """Ermittelt den WordPress-REST-Root-Endpunkt."""
        for linked_url, rel in self._split_link_header(link_header):
            if rel == "https://api.w.org/":
                return linked_url.rstrip("/")

        parsed = urlparse(fallback_url or "")
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/wp-json"
        return None

    def _safe_get_json(self, url: str) -> Optional[dict]:
        """GET JSON ohne Retry-Lärm bei 404/403."""
        try:
            resp = self.session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception:
            return None

    def _fetch_wordpress_rendered_html(self, response_url: str, link_header: str) -> Optional[str]:
        """
        Holt gerenderten Inhalt aus WordPress REST API.
        Hilft bei Download-Seiten, die als HTML leer ausgeliefert werden.
        """
        post_id = self._extract_shortlink_post_id(link_header, response_url)
        api_root = self._extract_wp_api_root(link_header, response_url)
        if not post_id or not api_root:
            return None

        types_payload = self._safe_get_json(f"{api_root}/wp/v2/types")
        rest_bases: List[str] = []
        if isinstance(types_payload, dict):
            for item in types_payload.values():
                if isinstance(item, dict):
                    rest_base = (item.get("rest_base") or "").strip()
                    if rest_base and rest_base not in rest_bases:
                        rest_bases.append(rest_base)

        # Fallback-Reihenfolge, falls /types nicht funktioniert
        for fallback_base in ("posts", "pages", "downloads"):
            if fallback_base not in rest_bases:
                rest_bases.append(fallback_base)

        for rest_base in rest_bases:
            payload = self._safe_get_json(f"{api_root}/wp/v2/{rest_base}/{post_id}")
            if not isinstance(payload, dict):
                continue
            content = payload.get("content")
            if isinstance(content, dict):
                rendered = unescape((content.get("rendered") or "").strip())
                if rendered:
                    return rendered
        return None

    def _extract_pdf_links_from_soup(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Sammelt PDF-Links aus HTML."""
        links: List[str] = []
        seen: Set[str] = set()
        pdf_pattern = re.compile(r"\.pdf(?:$|[?#])", re.IGNORECASE)

        def _register(raw_link: str, hint: str = "") -> None:
            raw_link = (raw_link or "").strip()
            if not raw_link:
                return
            abs_url = urljoin(base_url, raw_link)
            parsed = urlparse(abs_url)
            if parsed.scheme not in {"http", "https"}:
                return
            lower_url = abs_url.lower()
            hint_has_pdf = "pdf" in (hint or "").lower()
            looks_like_download = (
                "/download/" in lower_url
                or "/downloads/" in lower_url
                or "download=" in lower_url
            )
            if not pdf_pattern.search(abs_url) and not hint_has_pdf and not looks_like_download:
                return
            norm = self._url_key(abs_url)
            if norm in seen:
                return
            seen.add(norm)
            links.append(abs_url)

        for tag in soup.find_all("a"):
            _register(
                tag.get("href") or "",
                hint=f"{tag.get_text(' ', strip=True)} {tag.get('type') or ''} {tag.get('class') or ''}",
            )
        for tag in soup.find_all("iframe"):
            _register(tag.get("src") or "", hint=f"{tag.get('type') or ''} {tag.get('class') or ''}")
        for tag in soup.find_all("embed"):
            _register(tag.get("src") or "", hint=f"{tag.get('type') or ''} {tag.get('class') or ''}")
        for tag in soup.find_all("object"):
            _register(tag.get("data") or "", hint=f"{tag.get('type') or ''} {tag.get('class') or ''}")
        return links

    def _extract_pdf_markdown_from_links(self, pdf_links: List[str]) -> str:
        """Lädt PDF-Links und extrahiert deren Text."""
        chunks: List[str] = []
        for idx, pdf_url in enumerate(pdf_links[:5], start=1):
            resp = self.fetch_with_retry(pdf_url)
            if not resp:
                continue
            final_pdf_url = (resp.url or pdf_url).strip() or pdf_url
            ctype = resp.headers.get("Content-Type", "")
            if not self.is_pdf_candidate(final_pdf_url, ctype):
                continue
            try:
                body = self.pdf_to_markdown(resp.content)
            except Exception as exc:
                self.log(f"PDF Parse error für {pdf_url}: {exc}", "warn")
                continue
            label = Path(urlparse(final_pdf_url).path).name or f"document-{idx}.pdf"
            chunks.append(f"## PDF {label}\n\n{body}")
        return "\n\n".join(chunks).strip()

    def is_html_candidate(self, url: str, content_type: str) -> bool:
        """Filtert typische Nicht-HTML-Ressourcen aus."""
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        non_html_suffixes = (
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
            ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
            ".mp4", ".mov", ".avi", ".webm", ".mp3", ".wav",
            ".woff", ".woff2", ".ttf", ".otf", ".eot",
            ".css", ".js", ".json", ".xml",
        )
        if any(path.endswith(sfx) for sfx in non_html_suffixes):
            return False

        ctype = (content_type or "").lower()
        if not ctype:
            return True
        if ctype.startswith("text/html"):
            return True
        if ctype.startswith("application/xhtml"):
            return True
        if ctype.startswith("text/"):
            return True
        if "html" in ctype:
            return True
        return False

    def scrape_url(self, url: str) -> Tuple[str, str, str, str]:
        """
        Scraped eine einzelne URL
        Returns: (markdown, title, status, final_url)
        """
        resp = self.fetch_with_retry(url)
        if not resp:
            return "", "", "fetch_failed", url
        
        if resp.status_code >= 400:
            return "", "", f"http_{resp.status_code}", url

        final_url = (resp.url or url).strip() or url
        content_type = resp.headers.get("Content-Type", "")
        if self.is_pdf_candidate(final_url, content_type):
            try:
                markdown = self.pdf_to_markdown(resp.content)
                pdf_name = Path(urlparse(final_url).path).name or Path(urlparse(url).path).name or "document.pdf"
                return markdown, f"PDF {pdf_name}", "ok", final_url
            except Exception as exc:
                self.log(f"PDF Parse error für {url}: {exc}", "error")
                return "", "", f"pdf_parse_failed: {exc}", final_url

        if not self.is_html_candidate(final_url, content_type):
            return "", "", "skipped_non_html", final_url

        try:
            soup = BeautifulSoup(resp.content, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            canonical = soup.select_one("link[rel='canonical']")
            if canonical and canonical.get("href"):
                try:
                    final_url = urljoin(final_url, canonical.get("href").strip())
                except Exception:
                    pass

            # Einige WordPress-Download-Seiten liefern im HTML-Response keinen Body.
            # Dann versuchen wir den gerenderten Inhalt über die REST API zu holen.
            if not (soup.get_text(" ", strip=True) or "").strip():
                wp_html = self._fetch_wordpress_rendered_html(
                    response_url=final_url,
                    link_header=resp.headers.get("Link", ""),
                )
                if wp_html:
                    self.log(f"WordPress REST-Fallback genutzt für: {final_url}", "info")
                    soup = BeautifulSoup(wp_html, "html.parser")
                    if not title:
                        title = soup.title.string.strip() if soup.title and soup.title.string else ""
            
            main_html = self.extract_main_html(soup)
            markdown = self.html_to_markdown(main_html)
            
            if not markdown.strip():
                markdown = soup.get_text(" ", strip=True)

            if len(markdown.strip()) < 30:
                pdf_links = self._extract_pdf_links_from_soup(soup, final_url)
                if pdf_links:
                    self.log(f"PDF-Link-Fallback ({len(pdf_links)} Links) für: {final_url}", "info")
                    pdf_markdown = self._extract_pdf_markdown_from_links(pdf_links)
                    if pdf_markdown:
                        merged = markdown.strip()
                        if merged:
                            markdown = f"{merged}\n\n{pdf_markdown}"
                        else:
                            markdown = pdf_markdown
                        if not title:
                            title = f"PDF Links von {Path(urlparse(final_url).path).name or 'download-page'}"

            if not markdown.strip():
                return "", "", "empty_content", final_url
            
            return markdown, title, "ok", final_url
        except Exception as exc:
            self.log(f"Parse error für {url}: {exc}", "error")
            return "", "", f"parse_failed: {exc}", final_url

    def write_markdown_file(self, filename: Path, requested_url: str, final_url: str, title: str, body: str) -> None:
        """Schreibt Markdown-Datei mit Frontmatter"""
        scraped_at = dt.datetime.now().isoformat()
        frontmatter = "\n".join([
            "---",
            f"requested_url: {requested_url}",
            f"url: {final_url}",
            f"title: {title or 'N/A'}",
            f"scraped_at: {scraped_at}",
            "---",
            "",
        ])
        filename.write_text(frontmatter + body, encoding="utf-8")

    def process_urls(self, urls: List[str], parallel: bool = True, cleanup_stale: bool = False) -> None:
        """Verarbeitet alle URLs (parallel oder sequenziell)"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        summary = []
        seen_urls: Set[str] = set()
        
        # Dedupliziere URLs
        unique_urls = []
        skipped_english = 0
        for url in urls:
            if self.should_skip_url(url):
                skipped_english += 1
                continue
            normalized = self._url_key(url)
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                unique_urls.append(url)

        if skipped_english:
            self.log(f"Überspringe {skipped_english} englische URLs (/en)", "info")

        slug_map = self._build_slug_map(unique_urls)
        if cleanup_stale:
            target_files = {f"{slug}.md" for slug in slug_map.values()}
            self._cleanup_stale_files(target_files)
        else:
            self.log(
                "Inkrementeller Modus: bestehende Markdown-Dateien bleiben erhalten "
                "(kein Entfernen von alten Dateien).",
                "info",
            )
        final_url_seen = {}
        final_url_seen_lock = threading.Lock()
        
        self.log(f"Starte Scraping von {len(unique_urls)} URLs...")
        
        def process_single_url(url: str) -> dict:
            """Verarbeitet eine einzelne URL"""
            try:
                markdown, title, status, final_url = self.scrape_url(url)
                slug = slug_map[url]
                out_path = self.output_dir / f"{slug}.md"
                
                result = {"url": url, "final_url": final_url, "status": status, "file": None}
                
                if status == "ok":
                    final_key = self._url_key(final_url)
                    with final_url_seen_lock:
                        first_url = final_url_seen.get(final_key)
                        if not first_url:
                            final_url_seen[final_key] = url
                        elif first_url != url:
                            result["status"] = f"duplicate_final_url (wie {first_url})"
                            self.log(f"↺ {url} → {final_url} (bereits über {first_url})", "warn")
                            return result
                    try:
                        self.write_markdown_file(out_path, url, final_url, title, markdown)
                        result["file"] = str(out_path.name)
                        self.log(f"✓ {out_path.name}", "success")
                    except Exception as exc:
                        result["status"] = f"write_failed: {exc}"
                        self.log(f"✗ {url}: {exc}", "error")
                else:
                    self.log(f"✗ {url}: {status}", "error")

                return result
            except Exception as exc:
                self.log(f"✗ Unerwarteter Fehler bei {url}: {exc}", "error")
                return {"url": url, "status": f"unexpected_error: {exc}", "file": None}
        
        # Verarbeite URLs
        if parallel and self.max_workers > 1:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(process_single_url, url): url for url in unique_urls}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        summary.append(future.result())
                    except Exception as exc:
                        self.log(f"✗ Future-Fehler bei {url}: {exc}", "error")
                        summary.append({"url": url, "status": f"future_failed: {exc}", "file": None})
        else:
            for url in unique_urls:
                summary.append(process_single_url(url))
        
        # Schreibe Summary
        self.summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        
        # Statistiken
        success_count = sum(1 for s in summary if s["status"] == "ok")
        self.log(f"Fertig! {success_count}/{len(summary)} URLs erfolgreich gescrapet", "success")
        self.log(f"Summary: {self.summary_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verbesserter Sitemap Scraper mit Retry-Logik und parallelem Scraping"
    )
    parser.add_argument("sitemap_url", help="URL zur sitemap.xml")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, 
                       help=f"Anzahl paralleler Worker (default: {MAX_WORKERS})")
    parser.add_argument("--sequential", action="store_true",
                       help="Sequenzielles Scraping (kein Parallelismus)")
    parser.add_argument("--quiet", action="store_true",
                       help="Weniger Output")
    parser.add_argument(
        "--cleanup-stale",
        action="store_true",
        help="Entfernt alte Dateien, die im aktuellen Run nicht mehr enthalten sind (Vollabgleich).",
    )
    
    args = parser.parse_args()
    
    scraper = SitemapScraper(
        max_workers=args.workers,
        verbose=not args.quiet
    )
    
    try:
        urls = scraper.fetch_sitemap_urls(args.sitemap_url)
        if not urls:
            print("Keine URLs gefunden!", file=sys.stderr)
            return 1
        
        scraper.process_urls(
            urls,
            parallel=not args.sequential,
            cleanup_stale=args.cleanup_stale,
        )
        return 0
        
    except KeyboardInterrupt:
        print("\n[ABBRUCH] Durch Benutzer unterbrochen")
        return 130
    except Exception as exc:
        print(f"[FATAL] Fehler: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


# Komfort-Funktionen für Import im Backend
def fetch_sitemap_urls(sitemap_url: str, max_workers: int = MAX_WORKERS, verbose: bool = True) -> List[str]:
    scraper = SitemapScraper(max_workers=max_workers, verbose=verbose)
    return scraper.fetch_sitemap_urls(sitemap_url)


def process_urls(
    urls: List[str],
    *,
    output_dir: Union[str, Path] = OUTPUT_DIR,
    summary_path: Union[str, Path] = SUMMARY_PATH,
    max_workers: int = MAX_WORKERS,
    parallel: bool = True,
    cleanup_stale: bool = False,
    verbose: bool = True,
) -> None:
    scraper = SitemapScraper(
        max_workers=max_workers,
        verbose=verbose,
        output_dir=output_dir,
        summary_path=summary_path,
    )
    scraper.process_urls(urls, parallel=parallel, cleanup_stale=cleanup_stale)
