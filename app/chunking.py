import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


TARGET_CHUNK_TOKENS = 500
MIN_CHUNK_TOKENS = 400
MAX_CHUNK_TOKENS = 600
OVERLAP_RATIO = 0.15
SEGMENT_MAX_TOKENS = max(100, int(TARGET_CHUNK_TOKENS * OVERLAP_RATIO * 1.5))

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
SETEXT_HEADING_RE = re.compile(r"^(=+|-+)\s*$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])")
WORD_RE = re.compile(r"\S+")
HEADING_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9]+")
INLINE_LABEL_RE = re.compile(
    r"(?<!\n)(?P<label>(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9/&() +.-]{2,40}|[A-ZÄÖÜ0-9/&() +.-]{2,40}):)"
)


@dataclass(frozen=True)
class StructuredChunk:
    text: str
    metadata: Dict[str, str]

    @property
    def title(self) -> str:
        return self.metadata["title"]

    @property
    def section(self) -> str:
        return self.metadata["section"]


@dataclass(frozen=True)
class Section:
    title: str
    content: str


def estimate_tokens(text: str) -> int:
    clean = (text or "").strip()
    if not clean:
        return 0
    return max(1, math.ceil(len(clean) / 4))


def format_chunk_text(title: str, section: str, content: str) -> str:
    body = (content or "").strip()
    return f"Document: {title}\nSection: {section}\n{body}"


def chunk_markdown_document(
    text: str,
    title: str,
    metadata: Optional[Dict[str, str]] = None,
) -> List[StructuredChunk]:
    base_title = (title or "").strip() or "Untitled Document"
    base_meta = dict(metadata or {})
    sections = split_markdown_sections(text, default_section=base_title)

    chunks: List[StructuredChunk] = []
    for section_index, section in enumerate(sections, start=1):
        section_title = section.title.strip() or base_title
        for chunk_index, chunk_body in enumerate(split_section_content(section.content), start=1):
            chunk_meta = dict(base_meta)
            chunk_meta["title"] = base_title
            chunk_meta["section"] = section_title
            chunk_meta["section_index"] = str(section_index)
            chunk_meta["chunk_index_in_section"] = str(chunk_index)
            chunks.append(
                StructuredChunk(
                    text=format_chunk_text(base_title, section_title, chunk_body),
                    metadata=chunk_meta,
                )
            )
    return chunks


def split_markdown_sections(text: str, default_section: str) -> List[Section]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    lines = normalized.split("\n")
    sections: List[Section] = []
    current_section = default_section.strip() or "General"
    buffer: List[str] = []

    def flush() -> None:
        content = _clean_section_content("\n".join(buffer))
        if content:
            sections.append(Section(title=current_section, content=content))

    i = 0
    while i < len(lines):
        heading = _extract_heading(lines, i)
        if heading:
            _, heading_text, consume = heading
            flush()
            current_section = heading_text
            buffer = []
            i += consume
            continue

        buffer.append(lines[i])
        i += 1

    flush()
    return sections


def split_section_content(content: str) -> List[str]:
    clean = _clean_section_content(content)
    if not clean:
        return []

    if estimate_tokens(clean) <= MAX_CHUNK_TOKENS:
        return [clean]

    segments = _split_into_segments(clean)
    if not segments:
        return [clean]

    chunks: List[str] = []
    start = 0
    while start < len(segments):
        end = start
        best_end = start + 1
        while end < len(segments):
            candidate = "\n\n".join(segments[start : end + 1]).strip()
            if estimate_tokens(candidate) > MAX_CHUNK_TOKENS and end > start:
                break
            best_end = end + 1
            if estimate_tokens(candidate) >= TARGET_CHUNK_TOKENS:
                if end + 1 >= len(segments):
                    break
                next_candidate = "\n\n".join(segments[start : end + 2]).strip()
                if estimate_tokens(next_candidate) > MAX_CHUNK_TOKENS:
                    break
            end += 1

        chunk_text = "\n\n".join(segments[start:best_end]).strip()
        if chunk_text:
            chunks.append(chunk_text)

        if best_end >= len(segments):
            break

        next_start = _compute_overlap_start(segments, start, best_end)
        start = next_start if next_start > start else best_end

    return chunks or [clean]


def _normalize_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    normalized = normalized.replace("\u2022", "\n- ").replace("§", "\n- ")
    normalized = _insert_inline_heading_breaks(normalized)
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _insert_inline_heading_breaks(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        label = match.group("label")
        if label.lower().startswith(("http:", "https:")):
            return label
        alpha_count = sum(1 for char in label if char.isalpha())
        if alpha_count < 4:
            return label
        return "\n" + label

    return INLINE_LABEL_RE.sub(repl, text)


def _extract_heading(lines: Sequence[str], index: int) -> Optional[Tuple[int, str, int]]:
    line = lines[index].strip()
    if not line:
        return None

    markdown_heading = HEADING_RE.match(line)
    if markdown_heading:
        level = len(markdown_heading.group(1))
        text = _normalize_heading_text(markdown_heading.group(2))
        return (level, text, 1) if text else None

    if index + 1 < len(lines) and SETEXT_HEADING_RE.match(lines[index + 1].strip()):
        underline = lines[index + 1].strip()
        level = 1 if underline.startswith("=") else 2
        text = _normalize_heading_text(line)
        return (level, text, 2) if text else None

    previous_line = lines[index - 1].strip() if index > 0 else ""
    next_line = _next_nonempty_line(lines, index + 1)
    if _looks_like_heading_line(line, previous_line, next_line):
        text = _normalize_heading_text(line)
        return (2, text, 1) if text else None

    return None


def _looks_like_heading_line(line: str, previous_line: str, next_line: str) -> bool:
    stripped = line.strip()
    if not stripped or previous_line:
        return False
    if len(stripped) > 80 or len(WORD_RE.findall(stripped)) > 8:
        return False
    if stripped.startswith(("- ", "* ", "+ ")):
        return False
    if re.search(r"https?://|www\.", stripped.lower()):
        return False
    if stripped.endswith((".", "!", "?")):
        return False

    words = HEADING_WORD_RE.findall(stripped.strip(":"))
    if not words:
        return False

    if stripped.endswith(":"):
        alpha_count = sum(1 for char in stripped if char.isalpha())
        return alpha_count >= 4

    if stripped.isupper() and any(ch.isalpha() for ch in stripped):
        return True

    if not next_line or len(HEADING_WORD_RE.findall(next_line)) < 4:
        return False

    capitalized_words = sum(1 for word in words if word[0].isupper() or word.isupper())
    return capitalized_words == len(words)


def _normalize_heading_text(text: str) -> str:
    heading = re.sub(r"\s+", " ", (text or "").strip()).strip(": ").strip()
    return heading


def _clean_section_content(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    cleaned: List[str] = []
    blank_open = False
    for line in lines:
        if not line:
            if cleaned and not blank_open:
                cleaned.append("")
            blank_open = True
            continue
        cleaned.append(line)
        blank_open = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _next_nonempty_line(lines: Sequence[str], index: int) -> str:
    for pos in range(index, len(lines)):
        stripped = lines[pos].strip()
        if stripped:
            return stripped
    return ""


def _split_into_segments(text: str) -> List[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    segments: List[str] = []
    for paragraph in paragraphs:
        if estimate_tokens(paragraph) <= SEGMENT_MAX_TOKENS:
            segments.append(paragraph)
            continue
        segments.extend(_split_oversized_paragraph(paragraph, max_tokens=SEGMENT_MAX_TOKENS))
    return [segment for segment in segments if segment.strip()]


def _split_oversized_paragraph(text: str, max_tokens: int) -> List[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        units = lines
    else:
        units = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
        if len(units) <= 1:
            units = [part.strip() for part in re.split(r"(?<=,)\s+", text) if part.strip()]

    segments: List[str] = []
    buffer: List[str] = []

    def flush() -> None:
        chunk = " ".join(buffer).strip()
        if chunk:
            segments.append(chunk)

    for unit in units:
        if estimate_tokens(unit) > max_tokens:
            flush()
            buffer = []
            segments.extend(_split_long_unit_by_words(unit, max_tokens=max_tokens))
            continue

        candidate = " ".join(buffer + [unit]).strip()
        if buffer and estimate_tokens(candidate) > max_tokens:
            flush()
            buffer = [unit]
            continue

        buffer.append(unit)

    flush()
    return segments


def _split_long_unit_by_words(text: str, max_tokens: int) -> List[str]:
    words = text.split()
    if not words:
        return []

    parts: List[str] = []
    buffer: List[str] = []
    for word in words:
        candidate = " ".join(buffer + [word]).strip()
        if buffer and estimate_tokens(candidate) > max_tokens:
            parts.append(" ".join(buffer).strip())
            buffer = [word]
            continue
        buffer.append(word)

    if buffer:
        parts.append(" ".join(buffer).strip())
    return parts


def _compute_overlap_start(segments: Sequence[str], start: int, end: int) -> int:
    if end - start <= 1:
        return end

    target_overlap_tokens = max(1, int(TARGET_CHUNK_TOKENS * OVERLAP_RATIO))
    overlap_segments: List[str] = []
    next_start = end

    for index in range(end - 1, start, -1):
        overlap_segments.insert(0, segments[index])
        overlap_tokens = estimate_tokens("\n\n".join(overlap_segments))
        next_start = index
        if overlap_tokens >= target_overlap_tokens:
            break

    return next_start if next_start > start else start + 1
