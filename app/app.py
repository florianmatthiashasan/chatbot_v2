import io
import json
import os
import re
import shutil
import threading
import time
import traceback
import zipfile
import requests
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Workaround für FAISS / OpenMP unter macOS
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from flask import Flask, Response, jsonify, redirect, render_template, request
from langchain_community.vectorstores import FAISS
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from PyPDF2 import PdfReader
from supabase import Client, create_client

import build_index
import scraper as sitemap_scraper

BASE_DIR = Path(__file__).resolve().parent
EMBED_MODEL = "text-embedding-3-large"
DEFAULT_MODEL = os.environ.get("DEFAULT_CHAT_MODEL", "gpt-4o-mini")
try:
    DEFAULT_RETRIEVER_K = int(os.environ.get("DEFAULT_RETRIEVER_K", "6"))
except Exception:
    DEFAULT_RETRIEVER_K = 6
try:
    RAG_FETCH_MULTIPLIER = max(2, int(os.environ.get("RAG_FETCH_MULTIPLIER", "5")))
except Exception:
    RAG_FETCH_MULTIPLIER = 5
try:
    RAG_MIN_FETCH_K = max(8, int(os.environ.get("RAG_MIN_FETCH_K", "24")))
except Exception:
    RAG_MIN_FETCH_K = 24
try:
    RAG_MIN_CONTEXT_DOCS = max(4, int(os.environ.get("RAG_MIN_CONTEXT_DOCS", "10")))
except Exception:
    RAG_MIN_CONTEXT_DOCS = 10
try:
    RAG_MAX_DOC_CHARS = max(600, int(os.environ.get("RAG_MAX_DOC_CHARS", "1800")))
except Exception:
    RAG_MAX_DOC_CHARS = 1800
try:
    RAG_MAX_CONTEXT_CHARS = max(5000, int(os.environ.get("RAG_MAX_CONTEXT_CHARS", "22000")))
except Exception:
    RAG_MAX_CONTEXT_CHARS = 22000
try:
    CHAT_HISTORY_LIMIT = max(8, int(os.environ.get("CHAT_HISTORY_LIMIT", "16")))
except Exception:
    CHAT_HISTORY_LIMIT = 16
DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein Assistent, der NUR auf Basis des folgenden Website-Kontexts antwortet. "
    "Wenn etwas nicht im Kontext steht, sage ehrlich, dass du es nicht weißt. "
    "Antworte präzise und auf Deutsch. "
    "Verwende Zitate aus dem Kontext, um deine Antworten zu untermauern. "
    "Wenn du eine Quelle angibst, QUELLENANGABEN:\n"
    "- Gib Quellen als direkte Links an, z.B. https://example.com\n"
    "- NICHT als Markdown-Links mit eckigen Klammern [Text](URL)\n"
    "- Vermeide doppelte Links - jede URL nur einmal nennen\n"
    "- Bei mehreren Links zu derselben Seite: nur einmal aufführen"
)
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or os.environ.get("SUPPORT_EMAIL") or os.environ.get("CONTACT_MAIL")
CONTACT_PHONE = os.environ.get("CONTACT_PHONE") or os.environ.get("SUPPORT_PHONE")
CONTACT_URL = os.environ.get("CONTACT_URL")
SCHEDULE_STORE = BASE_DIR / "schedules.json"
DEFAULT_PRICING = {
    "gpt-4o-mini": {"input": 0.000150, "output": 0.000600},  # USD per 1k tokens
    "gpt-4o": {"input": 0.0025, "output": 0.0100},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
}
DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein Assistent, der NUR auf Basis des folgenden Website-Kontexts antwortet. "
    "Wenn etwas nicht im Kontext steht, sage ehrlich, dass du es nicht weißt. "
    "Antworte präzise und auf Deutsch. "
    "Verwende Zitate aus dem Kontext, um deine Antworten zu untermauern. "
    "Wenn du eine Quelle angibst, QUELLENANGABEN:\n"
    "- Gib Quellen als direkte Links an, z.B. https://example.com\n"
    "- NICHT als Markdown-Links mit eckigen Klammern [Text](URL)\n"
    "- Vermeide doppelte Links - jede URL nur einmal nennen\n"
    "- Bei mehreren Links zu derselben Seite: nur einmal aufführen"
)
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or os.environ.get("SUPPORT_EMAIL") or os.environ.get("CONTACT_MAIL")
CONTACT_PHONE = os.environ.get("CONTACT_PHONE") or os.environ.get("SUPPORT_PHONE")
CONTACT_URL = os.environ.get("CONTACT_URL")


def load_env_from_file() -> None:
    """Load .env key/value pairs if not already present in environment."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()
    except Exception as exc:
        print("Konnte .env nicht laden:", exc)
def load_env_from_file() -> None:
    """Load .env key/value pairs if not already present in environment."""
    env_paths = []
    env_file = os.environ.get("ENV_FILE")
    if env_file:
        env_paths.append(Path(env_file))
    env_paths.extend([Path("/etc/chatbot.env"), BASE_DIR / ".env"])

    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
        except Exception as exc:
            print(f"Konnte {env_path} nicht laden:", exc)

load_env_from_file()


@dataclass
class BotContext:
    customer_id: str
    customer_slug: str
    bot_id: str
    bot_slug: str
    docs_dir: Path
    faiss_dir: Path
    summary_path: Path
    faq_path: Path
    upload_dir: Path
    model: str
    retriever_k: int
    prompt_path: Path


# Caches & State
qa_cache: Dict[str, Any] = {}
qa_cache_lock = threading.Lock()
_run_locks: Dict[str, threading.Lock] = {}
_run_state: Dict[str, Dict[str, Any]] = {}
_supabase_client: Optional[Client] = None
_supabase_public_client: Optional[Client] = None
_chat_stats_cache: Dict[str, Any] = {}
_chat_stats_cache_lock = threading.Lock()
_schedule_lock = threading.Lock()
_schedules: Dict[str, Dict[str, Any]] = {}
_scheduler_started = False


def _supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "")


def _supabase_service_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        url = _supabase_url()
        key = _supabase_service_key()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL oder SUPABASE_SERVICE_ROLE_KEY fehlt.")
        _supabase_client = create_client(url, key)
    return _supabase_client


def _get_supabase_public_client() -> Client:
    global _supabase_public_client
    if _supabase_public_client is None:
        url = _supabase_url()
        anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        if not url or not anon_key:
            raise RuntimeError("SUPABASE_URL oder SUPABASE_ANON_KEY fehlt.")
        _supabase_public_client = create_client(url, anon_key)
    return _supabase_public_client


def _require_user_id() -> str:
    """Liest Supabase-User aus Bearer-Token. Fallback: DEV_USER_ID für lokale Tests."""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()

    if token:
        try:
            user_resp = _get_supabase_client().auth.get_user(token)
            if user_resp and user_resp.user and user_resp.user.id:
                return user_resp.user.id
        except Exception as exc:
            print("Supabase Auth get_user fehlgeschlagen:", exc)

    dev_uid = os.environ.get("DEV_USER_ID")
    if dev_uid:
        return dev_uid.strip()

    raise PermissionError("Kein gültiges Supabase-Token übermittelt.")


def _slugify(value: str, default: str = "item") -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or default


def _extract_bot_slug(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if payload and isinstance(payload, dict):
        slug = payload.get("bot_slug") or payload.get("bot")
        if slug:
            return str(slug).strip()
    slug = request.args.get("bot_slug") or request.form.get("bot_slug") or request.headers.get("X-Bot-Slug")
    return slug.strip() if slug else None


def _extract_customer_id(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if payload and isinstance(payload, dict):
        customer_id = payload.get("customer_id") or payload.get("customer")
        if customer_id:
            return str(customer_id).strip()
    customer_id = (
        request.args.get("customer_id")
        or request.form.get("customer_id")
        or request.headers.get("X-Customer-Id")
    )
    return customer_id.strip() if customer_id else None


def _normalize_chat_lang(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None

    token = raw.split(",", 1)[0].split(";", 1)[0].strip()
    token = token.replace("_", "-").split("-", 1)[0]
    mapping = {
        "de": "de",
        "deutsch": "de",
        "german": "de",
        "en": "en",
        "english": "en",
    }
    return mapping.get(token)


# ---------------------------------------------------------------------------
# Sprache der Nutzernachricht erkennen
# Die Antwort muss der Sprache der Frage folgen, nicht der Sprache der Website.
# Das Modell alleine entscheidet das unzuverlaessig, sobald Verlauf und Kontext
# in einer anderen Sprache stehen - deshalb wird die Sprache hier bestimmt und
# dem Modell vorgegeben.
# ---------------------------------------------------------------------------

LANG_NAMES = {
    "de": "German (Deutsch)",
    "en": "English",
    "tr": "Turkish (Türkçe)",
    "ar": "Arabic (العربية)",
    "fr": "French (Français)",
    "es": "Spanish (Español)",
    "it": "Italian (Italiano)",
    "nl": "Dutch (Nederlands)",
    "pt": "Portuguese (Português)",
    "pl": "Polish (Polski)",
    "ru": "Russian (Русский)",
    "el": "Greek (Ελληνικά)",
    "he": "Hebrew (עברית)",
    "uk": "Ukrainian (Українська)",
    "zh": "Chinese (中文)",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "hi": "Hindi (हिन्दी)",
}

# Schriftsysteme sind eindeutig - wer arabisch schreibt, will arabisch lesen.
_SCRIPT_PATTERNS = [
    ("ar", re.compile(r"[؀-ۿݐ-ݿ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("zh", re.compile(r"[一-鿿]")),
]

# Haeufige Funktionswoerter je Sprache. Kurze Nachrichten entscheidet oft ein
# einziges Wort ("merhaba", "danke"), deshalb sind Gruesse mit aufgenommen.
_LANG_STOPWORDS = {
    "de": {
        "der", "die", "das", "und", "ist", "sind", "ich", "du", "ihr", "wir", "nicht", "wie", "was",
        "wo", "wann", "warum", "kann", "koennen", "können", "habt", "haben", "hat", "mit", "von",
        "für", "fuer", "auf", "eine", "einen", "mehr", "gibt", "es", "bitte", "danke", "hallo",
        "guten", "tag", "servus", "moin", "preis", "preise", "kosten", "zimmer", "oeffnungszeiten",
    },
    "en": {
        "the", "is", "are", "how", "what", "where", "when", "why", "can", "you", "your", "we", "do",
        "does", "have", "has", "please", "thanks", "thank", "yes", "no", "with", "for", "about",
        "more", "there", "hi", "hello", "hey", "price", "prices", "cost", "opening", "hours", "i",
    },
    "tr": {
        "bir", "ve", "için", "icin", "nasıl", "nasil", "var", "yok", "ne", "nerede", "zaman",
        "merhaba", "selam", "teşekkür", "tesekkur", "evet", "hayır", "hayir", "ile", "daha", "çok",
        "cok", "mı", "mi", "mu", "mü", "fiyat", "fiyatlar", "oda", "saat",
    },
    "fr": {
        "le", "la", "les", "des", "une", "est", "vous", "je", "nous", "comment", "quel", "quelle",
        "pour", "avec", "merci", "oui", "non", "plus", "bonjour", "salut", "prix", "ouvert",
    },
    "es": {
        "el", "los", "las", "una", "es", "usted", "como", "cómo", "cual", "cuál", "para", "con",
        "gracias", "sí", "hola", "más", "mas", "donde", "dónde", "precio", "precios", "habitación",
    },
    "it": {
        "il", "lo", "gli", "le", "una", "come", "quale", "per", "con", "grazie", "sì", "più",
        "piu", "dove", "ciao", "buongiorno", "prezzo", "prezzi", "camera", "sono",
    },
    "nl": {
        "het", "een", "hoe", "wat", "waar", "kan", "jij", "jullie", "met", "voor", "dank",
        "bedankt", "ja", "nee", "meer", "hallo", "prijs", "prijzen", "kamer", "openingstijden",
    },
    "pt": {
        "os", "as", "uma", "é", "como", "qual", "para", "com", "obrigado", "obrigada", "sim",
        "não", "nao", "mais", "onde", "olá", "ola", "preço", "preco", "quarto", "horário",
    },
    "pl": {
        "jak", "co", "gdzie", "czy", "nie", "tak", "dla", "jest", "są", "sa", "dziękuję",
        "dziekuje", "cześć", "czesc", "dzień", "dobry", "cena", "ceny", "pokój", "pokoj",
    },
}

# Diakritika als Zusatzsignal - "ı" und "ğ" gibt es praktisch nur im Tuerkischen.
_LANG_HINT_CHARS = {
    "tr": "ışğİ",
    "de": "äöüß",
    "fr": "éèêàçù",
    "es": "ñ¿¡",
    "pt": "ãõ",
    "pl": "ąćęłńśźż",
    "it": "àòè",
}


def _detect_message_lang(text: str) -> str:
    """ISO-Code der Nachrichtensprache. Leerer String = nicht sicher erkennbar."""
    raw = (text or "").strip()
    if not raw:
        return ""

    letters = [ch for ch in raw if ch.isalpha()]
    if letters:
        for code, pattern in _SCRIPT_PATTERNS:
            hits = sum(1 for ch in letters if pattern.match(ch))
            if hits and hits / len(letters) >= 0.3:
                return code

    lowered = raw.lower()
    tokens = set(re.findall(r"[a-zA-Zäöüßàáâãçèéêëìíîïñòóôõùúûüışğđłżźćęąń]+", lowered))
    if not tokens:
        return ""

    scores: Dict[str, float] = {}
    for code, words in _LANG_STOPWORDS.items():
        score = float(len(tokens & words))
        for char in _LANG_HINT_CHARS.get(code, ""):
            if char in lowered:
                score += 1.5
        if score:
            scores[code] = score

    if not scores:
        return ""
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_code, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    # Bei Gleichstand lieber nichts sagen als falsch raten.
    if best_score < 1 or best_score - runner_up < 0.5:
        return ""
    return best_code


# Kurzer Hinweis, wenn der Index nichts hergibt - in der Sprache der Frage.
NO_INFO_TEXTS = {
    "de": "Dazu habe ich leider keine Informationen im Index gefunden.",
    "en": "I could not find relevant information for this question.",
    "tr": "Bu konuda dizinde uygun bir bilgi bulamadım.",
    "ar": "لم أجد معلومات مناسبة حول هذا الموضوع.",
    "fr": "Je n'ai pas trouvé d'informations à ce sujet.",
    "es": "No he encontrado información sobre esto.",
    "it": "Non ho trovato informazioni al riguardo.",
    "nl": "Ik heb hierover geen informatie gevonden.",
    "pt": "Não encontrei informações sobre isso.",
    "pl": "Nie znalazłem informacji na ten temat.",
    "ru": "К сожалению, я не нашёл информации по этому вопросу.",
    "el": "Δεν βρήκα σχετικές πληροφορίες.",
    "he": "לא מצאתי מידע בנושא הזה.",
    "uk": "На жаль, я не знайшов інформації з цього питання.",
    "zh": "抱歉，我没有找到相关信息。",
    "ja": "申し訳ありませんが、該当する情報が見つかりませんでした。",
    "ko": "죄송합니다. 관련 정보를 찾지 못했습니다.",
    "hi": "मुझे इस बारे में जानकारी नहीं मिली।",
}


def _lang_display_name(code: str) -> str:
    key = (code or "").strip().lower()
    return LANG_NAMES.get(key, key.upper() or "the user's language")


def _extract_chat_lang(payload: Optional[Dict[str, Any]]) -> str:
    if payload and isinstance(payload, dict):
        for key in ("lang", "locale", "language"):
            normalized = _normalize_chat_lang(payload.get(key))
            if normalized:
                return normalized

    for key in ("lang", "locale", "language"):
        normalized = _normalize_chat_lang(request.args.get(key) or request.form.get(key))
        if normalized:
            return normalized

    normalized = _normalize_chat_lang(
        request.headers.get("X-Lang")
        or request.headers.get("X-Locale")
        or request.headers.get("Accept-Language")
    )
    if normalized:
        return normalized

    return "de"


def _ensure_path(path_val: Any) -> Path:
    path = Path(path_val)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _fetch_customer_profile(user_id: str) -> Dict[str, Any]:
    resp = (
        _get_supabase_client()
        .table("customer_profiles")
        .select("id, slug, display_name")
        .eq("id", user_id)
        .single()
        .execute()
    )
    data = resp.data if hasattr(resp, "data") else None
    if not data:
        raise PermissionError("Kein customer_profile für den Nutzer gefunden.")
    return data


def _fetch_chatbot(user_id: str, bot_slug: str) -> Dict[str, Any]:
    resp = (
        _get_supabase_client()
        .table("chatbots")
        .select(
            "id, customer_id, slug, base_path, output_markdown_path, faiss_path, model, retriever_k"
        )
        .eq("customer_id", user_id)
        .eq("slug", bot_slug)
        .single()
        .execute()
    )
    data = resp.data if hasattr(resp, "data") else None
    if not data:
        raise PermissionError("Chatbot nicht gefunden oder gehört nicht zu diesem Nutzer.")
    return data


def _fetch_chatbot_public(bot_slug: str, customer_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch chatbot by slug (and optional customer_id) - for public access without user authentication."""
    try:
        query = (
            _get_supabase_client()
            .table("chatbots")
            .select(
                "id, customer_id, slug, base_path, output_markdown_path, faiss_path, model, retriever_k"
            )
            .eq("slug", bot_slug)
        )
        if customer_id:
            query = query.eq("customer_id", customer_id)
        resp = query.limit(1).execute()
        data = resp.data if hasattr(resp, "data") else None
        if data and len(data) > 0:
            return data[0]
        if customer_id:
            raise ValueError(f"Chatbot mit slug '{bot_slug}' für customer_id '{customer_id}' nicht gefunden.")
        raise ValueError(f"Chatbot mit slug '{bot_slug}' nicht gefunden.")
    except Exception as exc:
        if "nicht gefunden" in str(exc):
            raise
        print(f"Fehler beim Laden des Chatbots '{bot_slug}':", exc)
        raise ValueError(f"Chatbot mit slug '{bot_slug}' nicht gefunden.")


def _ensure_profile(user_id: str, email: Optional[str] = None) -> Dict[str, Any]:
    try:
        profile = (
            _get_supabase_client()
            .table("customer_profiles")
            .select("id, slug, display_name")
            .eq("id", user_id)
            .single()
            .execute()
        ).data
        if profile:
            return profile
    except Exception:
        profile = None

    slug = _slugify(email.split("@")[0]) if email else _slugify(user_id[:8])
    try:
        insert_resp = (
            _get_supabase_client()
            .table("customer_profiles")
            .insert({"id": user_id, "slug": slug, "email": email or None, "display_name": email or slug})
            .execute()
        )
        if hasattr(insert_resp, "data") and insert_resp.data:
            return insert_resp.data[0]
    except Exception as exc:
        print("Konnte customer_profile nicht erstellen:", exc)

    try:
        profile = (
            _get_supabase_client()
            .table("customer_profiles")
            .select("id, slug, display_name")
            .eq("id", user_id)
            .single()
            .execute()
        ).data
        if profile:
            return profile
    except Exception as exc:
        print("Konnte customer_profile nicht nachladen:", exc)

    return {"id": user_id, "slug": slug}


def _ensure_default_bot(user_id: str, customer_slug: str) -> Dict[str, Any]:
    """
    Ensure the user has a default chatbot.
    MIGRATION FIX:
    If a bot with slug='default' exists, rename it to 'default-{customer_slug}' (unique).
    Return the (possibly renamed) bot.
    """
    # 1. Determine the target unique slug for this customer
    # Fallback if customer_slug is empty/broken
    safe_slug = customer_slug if customer_slug and len(customer_slug) > 2 else f"user-{user_id[:8]}"
    unique_slug = f"default-{safe_slug}"

    sb = _get_supabase_client()

    try:
        # 2. Check if we already have the UNIQUE bot
        existing_unique = (
            sb.table("chatbots")
            .select("id, slug, base_path, output_markdown_path, faiss_path, model, retriever_k")
            .eq("customer_id", user_id)
            .eq("slug", unique_slug)
            .limit(1)
            .execute()
        ).data

        if existing_unique:
            return existing_unique[0]

        # 3. Check if we have the OLD 'default' bot to migrate
        existing_legacy = (
            sb.table("chatbots")
            .select("id, slug, base_path, output_markdown_path, faiss_path, model, retriever_k")
            .eq("customer_id", user_id)
            .eq("slug", "default")
            .limit(1)
            .execute()
        ).data

        if existing_legacy:
            legacy_bot = existing_legacy[0]
            bot_id = legacy_bot["id"]
            print(f"Migrating bot {bot_id} (default) -> {unique_slug} ...")
            
            # Update slug in DB
            updated = (
                sb.table("chatbots")
                .update({"slug": unique_slug})
                .eq("id", bot_id)
                .execute()
            )
            if updated.data:
                return updated.data[0]
            # Fallback if update returned nothing (shouldn't happen)
            legacy_bot["slug"] = unique_slug
            return legacy_bot

    except Exception as e:
        print("Fehler beim Laden/Migrieren des Default-Bots:", e)
        pass

    # 4. Create NEW unique bot if function hasn't returned yet
    bot_slug = unique_slug
    base_path_val = f"kunden/{safe_slug}/{bot_slug}/"
    
    payload = {
        "customer_id": user_id,
        "slug": bot_slug,
        "name": "Standard-Bot",
        "base_path": base_path_val,
        "output_markdown_path": base_path_val + "output_markdown",
        "faiss_path": base_path_val + "faiss_index",
        "model": DEFAULT_MODEL,
        "retriever_k": DEFAULT_RETRIEVER_K,
        "description": "Standard-Bot",
    }
    
    try:
        created = sb.table("chatbots").insert(payload).execute()
        return created.data[0] if hasattr(created, "data") and created.data else payload
    except Exception as exc:
        print("Fehler beim Erstellen des Bots:", exc)
        # Fallback to payload, though it won't have an ID
        return payload


def _contact_hint(lang: str = "de") -> str:
    lang_key = _normalize_chat_lang(lang) or "de"
    parts = []
    if CONTACT_EMAIL:
        if lang_key == "en":
            parts.append(f"by email: {CONTACT_EMAIL}")
        else:
            parts.append(f"per E-Mail: {CONTACT_EMAIL}")
    if CONTACT_PHONE:
        if lang_key == "en":
            parts.append(f"by phone: {CONTACT_PHONE}")
        else:
            parts.append(f"telefonisch: {CONTACT_PHONE}")
    if CONTACT_URL:
        parts.append(f"online: {CONTACT_URL}")
    if parts:
        return " or ".join(parts) if lang_key == "en" else " oder ".join(parts)
    return "by email to our team." if lang_key == "en" else "per E-Mail an unser Team."


_SOURCES_HEADING_RE = re.compile(r"^\s*(Quellen|Quelle|Sources|Source)\s*:\s*(.*)$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_GENERIC_UNKNOWN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"das\s+wei(?:ß|ss)\s+ich\s+(?:leider\s+)?(?:auf\s+basis\s+[^.!?]{0,80})?nicht",
        r"(?:das|dies|es)\s+wei(?:ß|ss)\s+ich\s+(?:leider\s+)?nicht",
        r"ich\s+wei(?:ß|ss)\s+(?:es|das|dies)?\s*(?:leider\s+)?nicht",
        r"dazu\s+(?:habe\s+ich|liegen\s+mir)\s+(?:leider\s+)?keine\s+(?:weiteren\s+)?(?:informationen|angaben)"
        r"(?:\s+(?:vor|im\s+[^.!?]{0,60}))?",
        r"(?:hierzu|dazu)\s+(?:gibt\s+es|liegen)\s+(?:leider\s+)?keine\s+(?:weiteren\s+)?(?:informationen|angaben)"
        r"(?:\s+[^.!?]{0,60})?",
        r"i\s+(?:do\s+not|don'?t)\s+know\s+(?:that\s+)?(?:based\s+on\s+[^.!?]{0,80})?",
        r"(?:this|that)\s+(?:information\s+)?is\s+not\s+(?:available|included|contained)"
        r"\s+in\s+the\s+(?:provided\s+)?(?:website\s+)?context",
    )
]
_CONTACT_HINT_RE = re.compile(
    r"kontakt|wend(?:e|en)\s+dich|wenden\s+sie|meld(?:e|en)\s+(?:dich|sie)|erreich|schreib|"
    r"e-?mail|telefon|contact|reach\s+out",
    re.IGNORECASE,
)


def _split_sources_tail(text: str) -> Tuple[str, str]:
    """Trennt einen abschliessenden Quellenblock vom eigentlichen Antworttext."""
    lines = text.split("\n")
    for idx in range(len(lines) - 1, -1, -1):
        if not _SOURCES_HEADING_RE.match(lines[idx]):
            continue
        tail = "\n".join(lines[idx:]).strip()
        if not re.search(r"https?://|www\.", tail, re.IGNORECASE):
            return text, ""
        return "\n".join(lines[:idx]).rstrip(), tail
    return text, ""


def _is_generic_unknown_sentence(sentence: str) -> bool:
    """True, wenn der Satz nur der generische 'weiss ich nicht'-Hinweis ist."""
    cleaned = re.sub(r"[*_`#>]+", " ", sentence)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip("„“\"»«'‚‘ -")
    if not cleaned or len(cleaned) > 200:
        return False
    core = cleaned.rstrip(".!?…").strip().strip("„“\"»«'")
    if not core:
        return False
    return any(pattern.fullmatch(core) for pattern in _GENERIC_UNKNOWN_PATTERNS)


def _looks_like_contact_hint(sentence: str) -> bool:
    stripped = sentence.strip()
    return bool(stripped) and len(stripped) <= 220 and bool(_CONTACT_HINT_RE.search(stripped))


def _strip_redundant_unknown_notice(text: str) -> str:
    """Entfernt den generischen 'weiss ich nicht'-Hinweis, wenn trotzdem geantwortet wurde.

    Besteht die Antwort im Kern nur aus diesem Hinweis, bleibt sie unveraendert.
    """
    if not text or not text.strip():
        return text

    body, sources_tail = _split_sources_tail(text)
    removed = False
    cleaned_paragraphs: List[str] = []

    for paragraph in re.split(r"\n\s*\n", body):
        kept_lines: List[str] = []
        for line in paragraph.split("\n"):
            if not line.strip():
                continue
            indent = line[: len(line) - len(line.lstrip())]
            kept_sentences: List[str] = []
            previous_dropped = False
            for sentence in _SENTENCE_SPLIT_RE.split(line.strip()):
                if _is_generic_unknown_sentence(sentence):
                    removed = True
                    previous_dropped = True
                    continue
                if previous_dropped and _looks_like_contact_hint(sentence):
                    removed = True
                    continue
                previous_dropped = False
                kept_sentences.append(sentence)
            if kept_sentences:
                kept_lines.append(indent + " ".join(kept_sentences))
        joined = "\n".join(kept_lines).strip()
        if joined:
            cleaned_paragraphs.append(joined)

    if not removed:
        return text

    cleaned_body = "\n\n".join(cleaned_paragraphs).strip()
    if len(re.sub(r"\s+", " ", cleaned_body)) < 40:
        # Antwort bestand praktisch nur aus dem Hinweis -> Original behalten.
        return text

    if sources_tail:
        cleaned_body = f"{cleaned_body}\n\n{sources_tail}"
    return cleaned_body.strip()


def _read_prompt(ctx: BotContext) -> str:
    if ctx.prompt_path.exists():
        try:
            return ctx.prompt_path.read_text(encoding="utf-8").strip() or DEFAULT_SYSTEM_PROMPT
        except Exception as exc:
            print("Konnte prompt.txt nicht lesen:", exc)
    return DEFAULT_SYSTEM_PROMPT


def _write_prompt(ctx: BotContext, prompt_text: str) -> str:
    text = (prompt_text or "").strip() or DEFAULT_SYSTEM_PROMPT
    ctx.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.prompt_path.write_text(text + "\n", encoding="utf-8")
    _invalidate_bot_cache(ctx)
    return text


# ---------------------------------------------------------------------------
# Widget-Inhalte pro Bot (widget_config.json neben prompt.txt)
# Begruessungs-Popup + Schnellaktionen der Startansicht.
# Keine Texte fest im Code: die Defaults sind neutral und pro Bot ueberschreibbar.
# ---------------------------------------------------------------------------

WIDGET_LANGS = ("de", "en")

DEFAULT_GREETING_TEXT = {
    "de": "Hallo! Wie darf ich dir weiterhelfen?",
    "en": "Hi! How can I help you?",
}

DEFAULT_TOPICS_LABEL = {
    "de": "Beliebte Themen",
    "en": "Popular topics",
}

# Button-Texte der Antwort-Aktionen. Neutrale Defaults, pro Bot ueberschreibbar.
DEFAULT_ACTION_LABELS = {
    "de": {
        "view": "Ansehen",
        "inquiry": "Anfrage senden",
        "details": "Mehr Details",
        "next_step": "Nächster Schritt",
        "contact": "Kontakt aufnehmen",
    },
    "en": {
        "view": "View",
        "inquiry": "Send inquiry",
        "details": "More details",
        "next_step": "Next step",
        "contact": "Get in touch",
    },
}

# Neutrale Ersatzbezeichnung, wenn ein Kartentitel als Betreff untauglich ist.
DEFAULT_ACTION_SUBJECT = {"de": "dieses Angebot", "en": "this offer"}


def _widget_config_path(ctx: BotContext) -> Path:
    """widget_config.json liegt im Bot-Basisordner - wie prompt.txt."""
    return ctx.prompt_path.parent / "widget_config.json"


def _default_widget_config() -> Dict[str, Any]:
    return {
        "greeting": {
            "enabled": True,
            "text": {lang: "" for lang in WIDGET_LANGS},
            "delay_ms": 1200,
        },
        "topics_label": {lang: "" for lang in WIDGET_LANGS},
        "topics": [],
        # Kontaktweg und Button-Texte pro Bot - damit nichts davon im Code steht.
        "contact": {"url": "", "email": "", "phone": ""},
        "action_labels": {lang: {} for lang in WIDGET_LANGS},
    }


def _widget_lang_map(value: Any) -> Dict[str, str]:
    """Akzeptiert {"de": "...", "en": "..."} oder einen Text fuer alle Sprachen."""
    result = {lang: "" for lang in WIDGET_LANGS}
    if isinstance(value, dict):
        for lang in WIDGET_LANGS:
            raw = value.get(lang)
            if raw is not None:
                result[lang] = str(raw).strip()
    elif isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            result = {lang: cleaned for lang in WIDGET_LANGS}
    return result


def _normalize_widget_config(raw: Any) -> Dict[str, Any]:
    config = _default_widget_config()
    if not isinstance(raw, dict):
        return config

    greeting_raw = raw.get("greeting") if isinstance(raw.get("greeting"), dict) else {}
    try:
        delay_ms = int(greeting_raw.get("delay_ms", 1200))
    except Exception:
        delay_ms = 1200
    config["greeting"] = {
        "enabled": _as_bool(greeting_raw.get("enabled"), True),
        "text": _widget_lang_map(greeting_raw.get("text")),
        "delay_ms": max(0, min(delay_ms, 20000)),
    }

    config["topics_label"] = _widget_lang_map(raw.get("topics_label"))

    topics: List[Dict[str, Any]] = []
    for item in raw.get("topics") or []:
        if not isinstance(item, dict):
            continue
        label = _widget_lang_map(item.get("label"))
        question = _widget_lang_map(item.get("question"))
        url = str(item.get("url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://", "mailto:", "tel:", "/")):
            url = ""
        if not any(label.values()) and not any(question.values()):
            continue
        topics.append(
            {
                "label": label,
                "question": question,
                "url": url,
                "highlight": _as_bool(item.get("highlight"), False),
            }
        )
    config["topics"] = topics[:10]

    contact_raw = raw.get("contact") if isinstance(raw.get("contact"), dict) else {}
    contact_url = str(contact_raw.get("url") or "").strip()
    if contact_url and not _is_safe_frontend_url(contact_url):
        contact_url = ""
    config["contact"] = {
        "url": contact_url,
        "email": str(contact_raw.get("email") or "").strip(),
        "phone": str(contact_raw.get("phone") or "").strip(),
    }

    labels_raw = raw.get("action_labels") if isinstance(raw.get("action_labels"), dict) else {}
    action_labels: Dict[str, Dict[str, str]] = {}
    for lang in WIDGET_LANGS:
        entry = labels_raw.get(lang) if isinstance(labels_raw.get(lang), dict) else {}
        action_labels[lang] = {
            key: str(entry.get(key) or "").strip()
            for key in DEFAULT_ACTION_LABELS["de"]
            if str(entry.get(key) or "").strip()
        }
    config["action_labels"] = action_labels
    return config


def _read_widget_config(ctx: BotContext) -> Dict[str, Any]:
    path = _widget_config_path(ctx)
    if path.exists():
        try:
            return _normalize_widget_config(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"Konnte widget_config.json nicht lesen ({path}):", exc)
    return _default_widget_config()


def _write_widget_config(ctx: BotContext, raw: Any) -> Dict[str, Any]:
    config = _normalize_widget_config(raw)
    path = _widget_config_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return config


def _pick_lang_value(mapping: Dict[str, str], lang: str, fallback: str = "") -> str:
    """Sprache -> andere Sprache -> Fallback."""
    lang_key = _normalize_chat_lang(lang) or "de"
    value = (mapping or {}).get(lang_key) or ""
    if value:
        return value
    for other in WIDGET_LANGS:
        alt = (mapping or {}).get(other) or ""
        if alt:
            return alt
    return fallback


def _resolve_widget_config(ctx: BotContext, lang: str) -> Dict[str, Any]:
    """Fertig aufgeloeste Widget-Inhalte fuer eine Sprache (fuer Widget + Snippet)."""
    lang_key = _normalize_chat_lang(lang) or "de"
    config = _read_widget_config(ctx)
    greeting = config.get("greeting") or {}

    topics = []
    for topic in config.get("topics") or []:
        label = _pick_lang_value(topic.get("label") or {}, lang_key)
        question = _pick_lang_value(topic.get("question") or {}, lang_key)
        if not label and not question:
            continue
        topics.append(
            {
                "label": label or question,
                "question": question or label,
                "url": topic.get("url") or "",
                "highlight": bool(topic.get("highlight")),
            }
        )

    return {
        "lang": lang_key,
        "greeting": {
            "enabled": bool(greeting.get("enabled", True)),
            "text": _pick_lang_value(
                greeting.get("text") or {}, lang_key, DEFAULT_GREETING_TEXT.get(lang_key, "")
            ),
            "delay_ms": int(greeting.get("delay_ms", 1200) or 0),
        },
        "topics_label": _pick_lang_value(
            config.get("topics_label") or {}, lang_key, DEFAULT_TOPICS_LABEL.get(lang_key, "")
        ),
        "topics": topics,
        "contact": dict(config.get("contact") or {}),
        "action_labels": _resolved_action_labels(config, lang_key),
    }


def _resolved_action_labels(config: Dict[str, Any], lang: str) -> Dict[str, str]:
    """Button-Texte: Bot-Konfiguration -> andere Sprache -> neutrale Defaults."""
    lang_key = _normalize_chat_lang(lang) or "de"
    defaults = DEFAULT_ACTION_LABELS.get(lang_key) or DEFAULT_ACTION_LABELS["de"]
    configured = (config.get("action_labels") or {}) if isinstance(config.get("action_labels"), dict) else {}
    labels: Dict[str, str] = {}
    for key, fallback in defaults.items():
        value = str((configured.get(lang_key) or {}).get(key) or "").strip()
        if not value:
            for other in WIDGET_LANGS:
                value = str((configured.get(other) or {}).get(key) or "").strip()
                if value:
                    break
        labels[key] = value or fallback
    return labels


def _contact_action_url(contact: Dict[str, Any]) -> str:
    """Kontaktweg aus der Bot-Konfiguration, sonst ENV-Default. Nie aus dem Antworttext."""
    data = contact or {}
    url = str(data.get("url") or "").strip()
    if _is_safe_frontend_url(url):
        return url
    email = str(data.get("email") or "").strip() or (CONTACT_EMAIL or "")
    phone = str(data.get("phone") or "").strip() or (CONTACT_PHONE or "")
    env_url = (CONTACT_URL or "").strip()
    if _is_safe_frontend_url(env_url):
        return env_url
    if email:
        return f"mailto:{email}"
    if phone:
        digits = re.sub(r"[^0-9+]", "", phone)
        if digits:
            return f"tel:{digits}"
    return ""


def _pricing_for_model(model: str) -> Dict[str, float]:
    if not model:
        model = DEFAULT_MODEL
    return DEFAULT_PRICING.get(model, DEFAULT_PRICING.get(DEFAULT_MODEL, {"input": 0.00015, "output": 0.0006}))


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Grobe Schätzung: 1 Token ~ 4 Zeichen bei Deutsch/Englisch
    return max(1, int(len(text) / 4))


def _cost_from_usage_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {"input": 0, "output": 0}
    per_model: List[Dict[str, Any]] = []
    total_cost = 0.0

    for item in items or []:
        model = (item.get("model") or item.get("snapshot_id") or item.get("aggregation") or "unknown").strip()
        input_tokens = (
            item.get("input_tokens")
            or item.get("n_context_tokens_total")
            or item.get("context_tokens")
            or 0
        )
        output_tokens = (
            item.get("output_tokens")
            or item.get("n_generated_tokens_total")
            or item.get("generated_tokens")
            or 0
        )
        try:
            input_tokens = int(input_tokens)
        except Exception:
            input_tokens = 0
        try:
            output_tokens = int(output_tokens)
        except Exception:
            output_tokens = 0

        pricing = _pricing_for_model(model)
        cost = (input_tokens / 1000.0) * pricing["input"] + (output_tokens / 1000.0) * pricing["output"]

        totals["input"] += input_tokens
        totals["output"] += output_tokens
        total_cost += cost

        per_model.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "pricing": pricing,
                "cost_usd": round(cost, 6),
            }
        )

    return {
        "totals": totals,
        "per_model": per_model,
        "cost_usd": round(total_cost, 6),
    }


def _load_schedules_from_disk() -> None:
    global _schedules
    if SCHEDULE_STORE.exists():
        try:
            data = json.loads(SCHEDULE_STORE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _schedules = data
        except Exception as exc:
            print("Konnte schedules.json nicht laden:", exc)


def _save_schedules_to_disk() -> None:
    try:
        SCHEDULE_STORE.write_text(json.dumps(_schedules, indent=2), encoding="utf-8")
    except Exception as exc:
        print("Konnte schedules.json nicht speichern:", exc)


def _build_bot_context_from_schedule(job: Dict[str, Any]) -> BotContext:
    paths = job.get("paths") or {}
    base_path = Path(paths.get("base_path") or BASE_DIR)
    return BotContext(
        customer_id=job.get("customer_id") or "",
        customer_slug=job.get("customer_slug") or "",
        bot_id=str(job.get("bot_id") or ""),
        bot_slug=job.get("bot_slug") or "",
        docs_dir=_ensure_path(paths.get("docs_dir") or base_path / "output_markdown"),
        faiss_dir=_ensure_path(paths.get("faiss_dir") or base_path / "faiss_index"),
        summary_path=_ensure_path(paths.get("summary_path") or base_path / "summary.json"),
        faq_path=_ensure_path(paths.get("faq_path") or base_path / "output_markdown/faqs.md"),
        upload_dir=_ensure_path(paths.get("upload_dir") or base_path / "output_markdown/uploads"),
        model=job.get("model") or DEFAULT_MODEL,
        retriever_k=int(job.get("retriever_k") or DEFAULT_RETRIEVER_K),
        prompt_path=_ensure_path(paths.get("prompt_path") or base_path / "prompt.txt"),
    )


def _schedule_minutes_from_payload(freq: str, raw_minutes: Any) -> int:
    if freq in {"daily", "day"}:
        return 60 * 24
    if freq in {"weekly", "week"}:
        return 60 * 24 * 7
    if freq in {"monthly", "month"}:
        return 60 * 24 * 30
    try:
        return int(raw_minutes or 0)
    except Exception:
        return 0


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return default


def _iso_utc(ts: datetime) -> str:
    return ts.isoformat() + "Z"


def _ensure_schedule_next_run(job: Dict[str, Any], now: Optional[datetime] = None) -> Tuple[Optional[datetime], bool]:
    current = now or datetime.utcnow()
    minutes = _schedule_minutes_from_payload(str(job.get("frequency") or ""), job.get("minutes"))
    if minutes <= 0:
        return None, False

    changed = False
    next_run = _parse_iso_ts(job.get("next_run_at"))
    if next_run is None:
        last_run = _parse_iso_ts(job.get("last_run"))
        anchor = last_run or current
        next_run = anchor + timedelta(minutes=minutes)
        job["next_run_at"] = _iso_utc(next_run)
        changed = True
    return next_run, changed


def _run_scheduled_job(job: Dict[str, Any]) -> None:
    try:
        ctx = _build_bot_context_from_schedule(job)
        lock = _bot_run_lock(ctx)
        if lock.locked():
            return
        acquired = lock.acquire(blocking=False)
        if not acquired:
            return
        _run_state.setdefault(ctx.bot_id, {"running": False, "last_run": None})
        _run_state[ctx.bot_id]["running"] = True
        _run_state[ctx.bot_id]["started_at"] = _iso_utc(datetime.utcnow())
        _run_state[ctx.bot_id]["mode"] = "scrape_and_index"
        _run_state[ctx.bot_id]["requested_sitemap_url"] = (job.get("sitemap_url") or "").strip() or None
        try:
            result = _run_scrape_and_index(ctx, job.get("sitemap_url") or "", cleanup_stale=False)
            _run_state[ctx.bot_id]["last_run"] = result
        finally:
            _run_state[ctx.bot_id]["running"] = False
            _run_state[ctx.bot_id]["started_at"] = None
            _run_state[ctx.bot_id]["mode"] = None
            _run_state[ctx.bot_id]["requested_sitemap_url"] = None
            lock.release()
    except Exception as exc:
        print("Fehler im geplanten Job:", exc)


def _scheduler_loop():
    while True:
        try:
            now = datetime.utcnow()
            changed = False
            with _schedule_lock:
                for bot_id, job in list(_schedules.items()):
                    if not job.get("enabled"):
                        continue
                    minutes = _schedule_minutes_from_payload(str(job.get("frequency") or ""), job.get("minutes"))
                    if minutes <= 0:
                        continue
                    next_run, migrated = _ensure_schedule_next_run(job, now=now)
                    if migrated:
                        changed = True
                    if next_run is None:
                        continue
                    if next_run <= now:
                        job["last_run"] = _iso_utc(now)
                        job["next_run_at"] = _iso_utc(now + timedelta(minutes=minutes))
                        changed = True
                        threading.Thread(target=_run_scheduled_job, args=(dict(job),), daemon=True).start()
            if changed:
                _save_schedules_to_disk()
        except Exception as exc:
            print("Scheduler-Loop Fehler:", exc)
        time.sleep(60)


def _ensure_scheduler_started():
    global _scheduler_started
    if _scheduler_started:
        return
    _load_schedules_from_disk()
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    _scheduler_started = True


def _build_bot_context(bot_slug: str) -> BotContext:
    user_id = _require_user_id()
    customer = _ensure_profile(user_id)
    bot = _fetch_chatbot(user_id, bot_slug)

    customer_slug = customer.get("slug") or user_id
    base_path_val = bot.get("base_path") or f"kunden/{customer_slug}/{bot_slug}"
    base_path = _ensure_path(base_path_val)

    docs_dir = _ensure_path(bot.get("output_markdown_path") or base_path / "output_markdown")
    faiss_dir = _ensure_path(bot.get("faiss_path") or base_path / "faiss_index")
    summary_path = base_path / "summary.json"
    faq_path = docs_dir / "faqs.md"
    upload_dir = docs_dir / "uploads"
    prompt_path = base_path / "prompt.txt"

    return BotContext(
        customer_id=user_id,
        customer_slug=customer_slug,
        bot_id=str(bot.get("id")),
        bot_slug=bot_slug,
        docs_dir=docs_dir,
        faiss_dir=faiss_dir,
        summary_path=summary_path,
        faq_path=faq_path,
        upload_dir=upload_dir,
        model=bot.get("model") or DEFAULT_MODEL,
        retriever_k=int(bot.get("retriever_k") or DEFAULT_RETRIEVER_K),
        prompt_path=prompt_path,
    )


def _build_public_bot_context(bot_slug: str, customer_id: Optional[str] = None) -> BotContext:
    """Build bot context for public access without requiring user authentication."""
    bot = _fetch_chatbot_public(bot_slug, customer_id=customer_id)
    customer_id = bot.get("customer_id") or customer_id or ""
    
    # Try to get customer slug from profile, fallback to customer_id
    customer_slug = customer_id[:8] if customer_id else "public"
    try:
        if customer_id:
            profile_resp = (
                _get_supabase_client()
                .table("customer_profiles")
                .select("slug")
                .eq("id", customer_id)
                .limit(1)
                .execute()
            )
            if profile_resp.data and len(profile_resp.data) > 0 and profile_resp.data[0].get("slug"):
                customer_slug = profile_resp.data[0].get("slug")
    except Exception:
        pass
    
    base_path_val = bot.get("base_path") or f"kunden/{customer_slug}/{bot_slug}"
    base_path = _ensure_path(base_path_val)

    docs_dir = _ensure_path(bot.get("output_markdown_path") or base_path / "output_markdown")
    faiss_dir = _ensure_path(bot.get("faiss_path") or base_path / "faiss_index")
    summary_path = base_path / "summary.json"
    faq_path = docs_dir / "faqs.md"
    upload_dir = docs_dir / "uploads"
    prompt_path = base_path / "prompt.txt"

    return BotContext(
        customer_id=customer_id,
        customer_slug=customer_slug,
        bot_id=str(bot.get("id")),
        bot_slug=bot_slug,
        docs_dir=docs_dir,
        faiss_dir=faiss_dir,
        summary_path=summary_path,
        faq_path=faq_path,
        upload_dir=upload_dir,
        model=bot.get("model") or DEFAULT_MODEL,
        retriever_k=int(bot.get("retriever_k") or DEFAULT_RETRIEVER_K),
        prompt_path=prompt_path,
    )


def _ensure_bot_dirs(ctx: BotContext) -> None:
    ctx.docs_dir.mkdir(parents=True, exist_ok=True)
    ctx.upload_dir.mkdir(parents=True, exist_ok=True)
    ctx.faiss_dir.mkdir(parents=True, exist_ok=True)
    ctx.summary_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_scheduler_started()


def _qa_cache_key(ctx: BotContext) -> str:
    return f"{ctx.bot_id}:{ctx.faiss_dir.resolve()}"


def _invalidate_bot_cache(ctx: BotContext) -> None:
    with qa_cache_lock:
        qa_cache.pop(_qa_cache_key(ctx), None)


def build_rag(ctx: BotContext):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt. Bitte in .env hinterlegen oder als Umgebungsvariable setzen.")

    embeddings = OpenAIEmbeddings(api_key=api_key, model=EMBED_MODEL)

    if not ctx.faiss_dir.is_dir():
        raise RuntimeError(f"FAISS-Index nicht gefunden in: {ctx.faiss_dir}")

    print(f"[{datetime.now()}] Lade FAISS-Index aus {ctx.faiss_dir} ...")
    vectordb = FAISS.load_local(
        str(ctx.faiss_dir),
        embeddings,
        allow_dangerous_deserialization=True,
    )

    try:
        retrieval_k = int(ctx.retriever_k or DEFAULT_RETRIEVER_K)
    except Exception:
        retrieval_k = DEFAULT_RETRIEVER_K
    retrieval_k = max(retrieval_k, DEFAULT_RETRIEVER_K)
    fetch_k = max(retrieval_k * RAG_FETCH_MULTIPLIER, RAG_MIN_FETCH_K)
    context_doc_limit = max(retrieval_k * 2, RAG_MIN_CONTEXT_DOCS)
    model_name = (os.environ.get("RAG_CHAT_MODEL") or ctx.model or DEFAULT_MODEL).strip()

    llm = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        temperature=0,
    )

    def _prepare_history(raw_history: Iterable[Dict[str, Any]] | None) -> List[Any]:
        """Normalisiere History-Einträge in LangChain-Messages."""
        if not raw_history:
            return []

        cleaned: List[Any] = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or item.get("sender") or "").strip().lower()
            content = (item.get("content") or item.get("text") or "").strip()
            if not content:
                continue
            if role in {"assistant", "ai", "bot"}:
                cleaned.append(AIMessage(content=content))
            else:
                cleaned.append(HumanMessage(content=content))

        return cleaned[-CHAT_HISTORY_LIMIT:]

    def _rewrite_question(question: str, history_msgs: List[Any]) -> str:
        """Mache eine Folgefrage eigenständig verständlich, falls History vorhanden."""
        if not history_msgs:
            return question

        prompt = [
            SystemMessage(
                content=(
                    "Du formulierst Folgefragen aus einem Chatverlauf so um, "
                    "dass sie ohne den Verlauf verständlich sind. "
                    "Füge keine neuen Informationen hinzu."
                )
            ),
            *history_msgs,
            HumanMessage(
                content=(
                    "Formuliere die letzte Nutzerfrage eigenständig verständlich. "
                    "Lass alle Vorgeschichte weg, aber erhalte die Intention. "
                    f"Letzte Frage: {question}"
                )
            ),
        ]

        try:
            rewritten = llm.invoke(prompt)
            new_q = getattr(rewritten, "content", "") if not isinstance(rewritten, str) else rewritten
            return new_q.strip() or question
        except Exception as exc:
            print("Konnte Frage nicht umformulieren:", exc)
            return question

    def _history_as_text(history_msgs: List[Any]) -> str:
        lines = []
        for msg in history_msgs:
            role = "Nutzer" if isinstance(msg, HumanMessage) else "Assistent"
            lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)

    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-zA-Z0-9äöüÄÖÜß]{3,}", (text or "").lower())

    def _doc_key(doc: Any, fallback: str) -> str:
        meta = getattr(doc, "metadata", {}) or {}
        return str(meta.get("chunk_id") or meta.get("doc_id") or meta.get("source") or fallback)

    def _collect_context_docs(original_question: str, rewritten_question: str) -> List[Any]:
        query_variants: List[str] = []
        for q in [rewritten_question, original_question]:
            cleaned = (q or "").strip()
            if cleaned and cleaned not in query_variants:
                query_variants.append(cleaned)

        if not query_variants:
            return []

        merged_docs: Dict[str, Dict[str, Any]] = {}
        for query in query_variants:
            try:
                scored_results = vectordb.similarity_search_with_score(query, k=fetch_k)
            except Exception as exc:
                print(f"Similarity retrieval fehlgeschlagen ({query}):", exc)
                scored_results = []

            for idx, (doc, score) in enumerate(scored_results):
                key = _doc_key(doc, f"sim-{idx}")
                try:
                    numeric_score = float(score)
                except Exception:
                    numeric_score = None

                existing = merged_docs.get(key)
                if not existing:
                    merged_docs[key] = {"doc": doc, "score": numeric_score}
                else:
                    old_score = existing.get("score")
                    if numeric_score is not None and (old_score is None or numeric_score < old_score):
                        existing["score"] = numeric_score
                        existing["doc"] = doc

            try:
                mmr_docs = vectordb.max_marginal_relevance_search(
                    query,
                    k=context_doc_limit,
                    fetch_k=fetch_k,
                )
            except Exception as exc:
                print(f"MMR retrieval fehlgeschlagen ({query}):", exc)
                mmr_docs = []

            for idx, doc in enumerate(mmr_docs):
                key = _doc_key(doc, f"mmr-{idx}")
                if key not in merged_docs:
                    merged_docs[key] = {"doc": doc, "score": None}

        if not merged_docs:
            try:
                fallback_docs = vectordb.similarity_search(query_variants[0], k=retrieval_k)
                return fallback_docs
            except Exception as exc:
                print("Fallback retrieval fehlgeschlagen:", exc)
                return []

        query_tokens = set(_tokenize(" ".join(query_variants)))
        ranked_entries = []
        for entry in merged_docs.values():
            doc = entry["doc"]
            score = entry.get("score")
            content = (getattr(doc, "page_content", "") or "")
            content_tokens = set(_tokenize(content))
            overlap = len(query_tokens.intersection(content_tokens))
            score_rank = score if score is not None else 1e9
            ranked_entries.append((-overlap, score_rank, -len(content), entry))

        ranked_entries.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3]["doc"] for item in ranked_entries[:context_doc_limit]]

    def answer(
        question: str,
        history: Iterable[Dict[str, Any]] | None = None,
        lang: Optional[str] = None,
        docs_out: Optional[List[Any]] = None,
    ) -> str:
        """Beantwortet die Frage.

        `docs_out` ist optional eine Liste des Aufrufers, in die die tatsaechlich
        verwendeten Kontext-Dokumente geschrieben werden. Bewusst als Parameter
        (nicht als Attribut am Funktionsobjekt), damit parallele Requests sich
        nicht gegenseitig ueberschreiben.
        """
        lang_key = _normalize_chat_lang(lang) or "de"
        unknown_source_label = "unknown source" if lang_key == "en" else "unbekannte Quelle"
        source_label_name = "Source" if lang_key == "en" else "Quelle"
        title_label_name = "Title" if lang_key == "en" else "Titel"
        section_label_name = "Section" if lang_key == "en" else "Abschnitt"
        trimmed_label = "...[truncated]" if lang_key == "en" else "...[gekürzt]"

        history_msgs = _prepare_history(history)
        effective_question = _rewrite_question(question, history_msgs)
        docs = _collect_context_docs(question, effective_question)

        # Sprache der aktuellen Nachricht schlaegt die Sprache der Website.
        # Ohne diese Vorgabe folgt das Modell dem deutschen Kontext und Verlauf.
        target_lang = _detect_message_lang(question) or lang_key
        target_name = _lang_display_name(target_lang)

        if not docs:
            if target_lang == "de":
                return (
                    "Dazu habe ich leider keine Informationen im Index gefunden. "
                    f"Bitte melde dich {_contact_hint('de')}."
                )
            if target_lang == "en":
                return (
                    "I could not find relevant information in the index for this question. "
                    f"Please contact us {_contact_hint('en')}."
                )
            return NO_INFO_TEXTS.get(target_lang, NO_INFO_TEXTS["en"])

        context_parts = []
        context_chars = 0
        used_docs: List[Any] = []
        for i, d in enumerate(docs, start=1):
            meta = d.metadata or {}
            src = _public_url_from_meta(meta) or meta.get("filename") or unknown_source_label
            title = meta.get("title") or meta.get("filename") or ""
            section = meta.get("section") or ""
            content = (d.page_content or "").strip()
            if not content:
                continue
            if len(content) > RAG_MAX_DOC_CHARS:
                content = content[:RAG_MAX_DOC_CHARS].rstrip() + f"\n{trimmed_label}"

            source_label = f"[{i}] ({source_label_name}: {src}"
            if title:
                source_label += f" | {title_label_name}: {title}"
            if section:
                source_label += f" | {section_label_name}: {section}"
            source_label += ")"
            entry = f"{source_label}\n{content}\n"

            if context_parts and context_chars + len(entry) > RAG_MAX_CONTEXT_CHARS:
                break

            context_parts.append(entry)
            context_chars += len(entry)
            used_docs.append(d)

        if docs_out is not None:
            docs_out.extend(used_docs)

        if not context_parts:
            if lang_key == "en":
                return (
                    "I found content, but could not extract usable text passages from it. "
                    f"Please contact us {_contact_hint(lang_key)}."
                )
            return (
                "Ich habe Inhalte gefunden, aber keine verwertbaren Textpassagen extrahieren können. "
                f"Bitte melde dich {_contact_hint(lang_key)}."
            )

        context = "\n\n".join(context_parts)

        system_prompt = _read_prompt(ctx)
        if lang_key == "en":
            quality_rules = (
                "Answer quality:\n"
                "- Be concrete and avoid shallow answers.\n"
                "- If the context contains details (e.g. contacts, names, numbers, conditions, steps), mention them explicitly.\n"
                "- For rooms, products, properties, offers, services, or concrete items: mention the name, the most relevant facts, and a useful next step instead of only saying that it exists.\n"
                "- Do not use generic filler if concrete facts are available.\n"
                "- If something is missing, clearly state what is not present in the context.\n"
                "- If you answered the question, do NOT append any notice that you do not know something.\n"
                "- Sentences like \"I do not know based on the website context.\" are only allowed when the "
                "entire answer consists of that notice."
            )

        else:
            quality_rules = (
                "Antwortqualität:\n"
                "- Antworte konkret und nicht oberflächlich.\n"
                "- Wenn im Kontext Details stehen (z.B. Kontaktwege, Namen, Zahlen, Bedingungen, Schritte), nenne sie explizit.\n"
                "- Bei Zimmern, Produkten, Immobilien, Angeboten, Leistungen oder konkreten Objekten: nenne Name, wichtigste Merkmale und einen sinnvollen nächsten Schritt, statt nur zu sagen, dass es etwas gibt.\n"
                "- Gib keine vagen Allgemeinplätze, wenn konkrete Angaben vorhanden sind.\n"
                "- Wenn etwas fehlt, sage klar, was im Kontext nicht enthalten ist.\n"
                "- Wenn du die Frage inhaltlich beantwortet hast, hänge KEINEN Hinweis an, dass du etwas "
                "nicht weißt.\n"
                "- Sätze wie \"Das weiß ich auf Basis des Website-Kontexts nicht.\" sind nur erlaubt, wenn die "
                "gesamte Antwort ausschließlich aus diesem Hinweis besteht."
            )


        history_text = _history_as_text(history_msgs) if history_msgs else ""
        if lang_key == "en":
            history_block = (
                "Previous chat history (only for pronoun/context disambiguation, not a knowledge source):\n"
                f"{history_text}\n\n"
                if history_text
                else ""
            )
            user_prompt = (
                f"{history_block}"
                f"Context from the index:\n{context}\n\n"
                f"Current question: {question}\n\n"
                "Answer requirements:\n"
                "- Use only information from the context.\n"
                "- Be detailed and complete.\n"
                "- Include concrete facts when available.\n"
                "- If data is missing, state the gap clearly.\n\n"
                "Answer:"
            )
        else:
            history_block = (
                f"Bisheriger Chatverlauf (nur zur Klärung von Pronomen, keine Wissensquelle):\n{history_text}\n\n"
                if history_text
                else ""
            )

            user_prompt = (
                f"{history_block}"
                f"Kontext aus dem Index:\n{context}\n\n"
                f"Aktuelle Frage: {question}\n\n"
                "Antwortvorgaben:\n"
                "- Nimm nur Informationen aus dem Kontext.\n"
                "- Sei detailliert und vollständig.\n"
                "- Nenne konkrete Fakten, falls vorhanden.\n"
                "- Wenn Daten fehlen, benenne die Lücke klar.\n\n"
                "Antwort:"
            )

        # Die Sprachregel steht bewusst am Ende des System-Prompts und wird nach
        # der Frage wiederholt: so gewinnt sie gegen Kontext und Verlauf.
        language_rule = (
            "MANDATORY LANGUAGE RULE - this overrides every other instruction:\n"
            f"- The user's current message is written in {target_name}.\n"
            f"- Write the ENTIRE answer in {target_name}.\n"
            "- Never switch to another language, not even if the context, the system prompt or the "
            "previous chat history are written in a different language.\n"
            "- Translate facts from the context into that language. Keep proper nouns, product "
            "names, prices, URLs and e-mail addresses unchanged."
        )
        messages = [
            SystemMessage(content=f"{system_prompt}\n\n{quality_rules}\n\n{language_rule}"),
            HumanMessage(content=f"{user_prompt}\n\n(Reminder: answer in {target_name}.)"),
        ]
        resp = llm.invoke(messages)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return _strip_redundant_unknown_notice(content)

    answer._vectordb = vectordb
    return answer


def _load_vectordb(ctx: BotContext) -> FAISS:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt. Bitte in .env hinterlegen oder als Umgebungsvariable setzen.")

    if not ctx.faiss_dir.is_dir():
        raise RuntimeError(f"FAISS-Index nicht gefunden in: {ctx.faiss_dir}")

    embeddings = OpenAIEmbeddings(api_key=api_key, model=EMBED_MODEL)
    vectordb = FAISS.load_local(
        str(ctx.faiss_dir),
        embeddings,
        allow_dangerous_deserialization=True,
    )
    return vectordb


def _serialize_memory_entry(doc_id: str, doc: Any, score: Optional[float], fallback_map: Dict[str, str]) -> Dict[str, Any]:
    meta = doc.metadata or {}
    chunk_id = meta.get("chunk_id") or meta.get("doc_id") or doc_id
    resolved_doc_id = meta.get("doc_id") or fallback_map.get(chunk_id) or doc_id

    source = meta.get("source") or meta.get("filename") or "unbekannte Quelle"
    title = meta.get("title") or meta.get("filename") or Path(source).name
    section = meta.get("section") or ""
    text_full = (doc.page_content or "").strip()
    preview = re.sub(r"\s+", " ", text_full)[:650]

    return {
        "doc_id": resolved_doc_id,
        "chunk_id": chunk_id,
        "source": source,
        "title": title,
        "section": section,
        "text": text_full,
        "preview": preview,
        "score": score,
    }


def _memory_snapshot(vectordb: FAISS, query: Optional[str], limit: int) -> Dict[str, Any]:
    docstore = getattr(vectordb, "docstore", None)
    store = docstore._dict if hasattr(docstore, "_dict") else {}
    chunk_to_doc_id: Dict[str, str] = {}
    for doc_id, doc in store.items():
        meta = doc.metadata or {}
        chunk_key = meta.get("chunk_id") or meta.get("doc_id") or doc_id
        chunk_to_doc_id[chunk_key] = doc_id

    items: List[Dict[str, Any]] = []
    if query:
        results = vectordb.similarity_search_with_score(query, k=limit)
        for doc, score in results:
            doc_meta = doc.metadata or {}
            doc_identifier = doc_meta.get("doc_id") or doc_meta.get("chunk_id") or ""
            items.append(_serialize_memory_entry(doc_identifier, doc, float(score), chunk_to_doc_id))
    else:
        for doc_id, doc in store.items():
            items.append(_serialize_memory_entry(doc_id, doc, None, chunk_to_doc_id))
        items.sort(key=lambda x: (x.get("source", ""), x.get("chunk_id", "")))
        items = items[:limit]

    return {
        "items": items,
        "total": len(store),
        "query": query or "",
    }


def get_qa(ctx: BotContext):
    key = _qa_cache_key(ctx)
    with qa_cache_lock:
        cached = qa_cache.get(key)
        if cached:
            return cached
        qa_cache[key] = build_rag(ctx)
        return qa_cache[key]


def _load_summary(ctx: BotContext) -> Optional[Any]:
    try:
        if ctx.summary_path.exists():
            with ctx.summary_path.open(encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        print(f"Konnte summary.json nicht laden ({ctx.summary_path}):", exc)
    return None


def _parse_markdown_frontmatter(md_path: Path) -> Dict[str, str]:
    """Liest einfaches YAML-ähnliches Frontmatter aus einer Markdown-Datei."""
    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    frontmatter: Dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            frontmatter[key] = value
    return frontmatter


def _build_reindex_summary(ctx: BotContext) -> List[Dict[str, Any]]:
    """Erstellt eine Summary aller beim Reindex berücksichtigten Markdown-Dateien."""
    rows: List[Dict[str, Any]] = []
    for md_path in sorted(ctx.docs_dir.rglob("*.md")):
        if not md_path.is_file():
            continue

        rel_file = str(md_path.relative_to(ctx.docs_dir))
        fm = _parse_markdown_frontmatter(md_path)
        source = (fm.get("source") or "").strip()
        requested_url = (fm.get("requested_url") or "").strip()
        final_url = (fm.get("url") or "").strip()

        if not final_url and source:
            final_url = source
        effective_url = requested_url or final_url or f"file:{rel_file}"

        rows.append(
            {
                "url": effective_url,
                "final_url": final_url or effective_url,
                "status": "indexed_rebuild",
                "file": rel_file,
            }
        )
    return rows


def _write_summary(ctx: BotContext, summary: List[Dict[str, Any]]) -> None:
    ctx.summary_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


_RICH_ITEM_INTENT_RE = re.compile(
    r"\b("
    r"zimmer|room|rooms|suite|suiten|chalet|chalets|apartment|apartments|"
    r"wohnung|wohnungen|immobilie|immobilien|haus|haeuser|häuser|projekt|projekte|"
    r"produkt|produkte|product|products|angebot|angebote|service|leistung|leistungen|"
    r"tarif|tarife|paket|pakete|sauna|pool|terrasse|balkon|garten|kaufen|buchen|"
    r"anfragen|anfrage|besichtigung|verfügbarkeit|verfuegbarkeit"
    r")\b",
    re.IGNORECASE,
)
_RICH_UNANSWERED_RE = re.compile(
    r"(keine\s+(?:informationen|angaben)|nicht\s+(?:gefunden|enthalten|vorhanden)|"
    r"could\s+not\s+find|no\s+relevant\s+information|not\s+available)",
    re.IGNORECASE,
)
# Breitere Erkennung von "dazu gibt es keine Auskunft". Absichtlich mit
# Einschub-Toleranz ("keine SPEZIFISCHEN Informationen"), daran scheiterte
# die enge Variante oben.
# Die Lueckenmuster nutzen [\w./-]+ statt \w+, sonst brechen zusammengesetzte
# Woerter wie "Website-Kontexts" die Erkennung.
_NO_INFO_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"kein(?:e|erlei)?(?:\s+[\w./-]+){0,2}\s+(?:informationen|angaben|auskunft|details|hinweise|daten)",
        r"nicht(?:\s+[\w./-]+){0,3}\s+(?:enthalten|vorhanden|verfuegbar|verfügbar|angegeben|genannt|gefunden|beschrieben|aufgefuehrt|aufgeführt|hinterlegt)",
        r"wei(?:ß|ss)\s+ich(?:\s+[\w./-]+){0,6}?\s+nicht",
        r"(?:kann|koennen|können)\s+ich(?:\s+[\w./-]+){0,3}?\s+(?:keine|nichts|dazu\s+nichts)",
        r"liegen\s+(?:mir\s+)?(?:leider\s+)?keine",
        r"(?:no|not\s+any)(?:\s+[\w./-]+){0,2}\s+(?:information|details|data)",
        r"(?:is|are|was|were)(?:\s+[\w./-]+){0,2}\s+not\s+(?:available|included|contained|described|mentioned|specified|provided)",
        r"(?:cannot|can\s?not|can't|unable\s+to)\s+(?:provide|give|answer|find|determine|say)",
        r"(?:do(?:es)?\s+not|don'?t)\s+(?:know|contain|include|have)",
        r"could\s+not\s+find",
    )
]
# Meta-/Kontaktfragen bekommen nie eine Produktkarte.
_RICH_META_QUESTION_RE = re.compile(
    r"(erreich|kontakt|telefon|telefonnummer|e-?mail|oeffnungszeit|öffnungszeit|adresse|anfahrt|"
    r"impressum|datenschutz|wer\s+(?:ist|sind|seid)|was\s+ist\s+(?:die|der|das)\b|"
    r"who\s+(?:is|are)|what\s+is\s+(?:the|a)\b|opening\s+hours|address|reach\s+you|contact)",
    re.IGNORECASE,
)
_RICH_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_RICH_LINK_RE = re.compile(r"\[([^\]\n]+)\]((?:\((?:https?://|mailto:|tel:|/)[^\s)]+\)))")
_RICH_FACT_LABEL_RE = re.compile(
    r"^(adresse|standort|größe|groesse|fläche|flaeche|wohnfläche|wohnflaeche|"
    r"wohnnutzfläche|wohnnutzflaeche|zimmer|personen|preis|kosten|tarif|"
    r"immobilien|tops|baubeginn|status|verfügbarkeit|verfuegbarkeit|"
    r"kontakt|telefon|e-mail|email|leistung)\b",
    re.IGNORECASE,
)
_RICH_SKIP_TITLES = {
    "datenschutz",
    "impressum",
    "barrierefreiheit",
    "barrierefreiheitserklaerung",
    "agb",
    "kontakt",
    "ueberuns",
    "überuns",
    "aboutus",
    "about",
    "news",
    "newsbeitraege",
    "presse",
    "blog",
    "karriere",
    "jobs",
    "cookies",
    "cookierichtlinie",
}


def _is_safe_frontend_url(value: Any) -> bool:
    url = str(value or "").strip()
    if not url:
        return False
    lowered = url.lower()
    return lowered.startswith(("http://", "https://", "mailto:", "tel:", "/"))


def _public_url_from_meta(meta: Dict[str, Any]) -> str:
    for key in ("url", "requested_url", "final_url", "source_url", "canonical_url"):
        value = str((meta or {}).get(key) or "").strip()
        if _is_safe_frontend_url(value):
            return value
    source = str((meta or {}).get("source") or "").strip()
    if source.lower().startswith(("http://", "https://", "mailto:", "tel:")):
        return source
    return ""


def _strip_frontmatter_text(text: str) -> str:
    source = text or ""
    if not source.startswith("---"):
        return source
    parts = source.split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else source


def _parse_frontmatter_text(text: str) -> Dict[str, str]:
    source = text or ""
    if not source.startswith("---"):
        return {}
    parts = source.split("---", 2)
    if len(parts) < 3:
        return {}
    result: Dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def _clean_card_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value or "").strip(" #*-_")
    title = re.sub(r"^(Document|Section)\s*:\s*", "", title, flags=re.IGNORECASE).strip()
    if not title or title.upper() == "N/A":
        return ""
    parts = [p.strip() for p in re.split(r"\s+(?:[-|]|\u2013|\u2014)\s+", title) if p.strip()]
    if len(parts) > 1 and len(parts[0]) >= 4:
        return parts[0][:90]
    return title[:90]


def _markdown_to_plain(text: str) -> str:
    plain = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text or "")
    plain = re.sub(r"\[([^\]\n]+)\]\((?:https?://|mailto:|tel:|/)[^)]+\)", r"\1", plain)
    plain = re.sub(r"^\s{0,3}#{1,6}\s*", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"[*_`>]+", "", plain)
    plain = re.sub(r"^\s*[-+]\s+", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"\b(?:Document|Section):\s*", "", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _limit_text(text: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    truncated = clean[: limit + 1].rsplit(" ", 1)[0].strip()
    return (truncated or clean[:limit].strip()) + "..."


def _extract_first_heading(markdown: str) -> str:
    for line in _strip_frontmatter_text(markdown).splitlines():
        match = re.match(r"^\s{0,3}#\s+(.+?)\s*$", line)
        if match:
            return _clean_card_title(match.group(1))
    return ""


_IMAGE_SKIP_RE = re.compile(
    r"(placeholder|platzhalter|logo|favicon|sprite|icon[-_.]|avatar|dummy|blank|pixel|spacer|"
    r"\.svg($|\?)|loading|lazy[-_])",
    re.IGNORECASE,
)
_IMAGE_SIZE_RE = re.compile(r"-(\d{2,4})x(\d{2,4})\.(?:jpe?g|png|webp|avif)$", re.IGNORECASE)


def _is_usable_card_image(url: str) -> bool:
    """Filtert Platzhalter, Logos und Mini-Thumbnails aus."""
    if not _is_safe_frontend_url(url):
        return False
    if _IMAGE_SKIP_RE.search(url):
        return False
    size = _IMAGE_SIZE_RE.search(url.split("?", 1)[0])
    if size:
        width, height = int(size.group(1)), int(size.group(2))
        if width < 300 or height < 150:
            return False
    return True


def _extract_first_image_url(markdown: str) -> str:
    """Bevorzugt ein Inhaltsbild nach der ersten H1 statt des Seiten-Banners."""
    body = _strip_frontmatter_text(markdown or "")
    heading = re.search(r"^\s{0,3}#\s+.+$", body, re.MULTILINE)
    heading_pos = heading.end() if heading else 0

    after_heading = ""
    before_heading = ""
    for match in _RICH_IMAGE_RE.finditer(body):
        url = match.group(1).strip()
        if not _is_usable_card_image(url):
            continue
        if match.start() >= heading_pos:
            after_heading = after_heading or url
            break
        before_heading = before_heading or url
    return after_heading or before_heading


def _extract_first_action_url(text: str) -> str:
    match = re.search(r"\b(https?://[^\s<>)]+|mailto:[^\s<>)]+|tel:[^\s<>)]+)", text or "", re.IGNORECASE)
    if not match:
        return ""
    url = match.group(1).rstrip(".,;:")
    return url if _is_safe_frontend_url(url) else ""


def _resolve_doc_source_path(ctx: BotContext, doc: Any) -> Optional[Path]:
    meta = getattr(doc, "metadata", {}) or {}
    candidates = [meta.get("source"), meta.get("filename")]
    allowed_roots = [ctx.docs_dir.resolve(), ctx.upload_dir.resolve(), ctx.prompt_path.parent.resolve()]

    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw or raw.lower().startswith(("http://", "https://", "mailto:", "tel:", "upload:")):
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = ctx.docs_dir / raw
        try:
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            if any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
                return resolved
        except Exception:
            continue
    return None


def _read_doc_markdown(ctx: BotContext, doc: Any) -> str:
    source_path = _resolve_doc_source_path(ctx, doc)
    if source_path:
        try:
            return source_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"Konnte Rich-Reply-Quelle nicht lesen ({source_path}):", exc)
    return getattr(doc, "page_content", "") or ""


def _doc_public_url(ctx: BotContext, doc: Any, markdown: str) -> str:
    meta = getattr(doc, "metadata", {}) or {}
    url = _public_url_from_meta(meta)
    if url:
        return url
    fm = _parse_frontmatter_text(markdown)
    for key in ("requested_url", "url", "final_url", "source"):
        value = (fm.get(key) or "").strip()
        if _is_safe_frontend_url(value):
            return value
    return ""


def _extract_card_teaser(markdown: str, title: str) -> str:
    body = _strip_frontmatter_text(markdown)
    title_key = _markdown_to_plain(title).lower()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("---") or line.startswith("!"):
            continue
        if re.search(r"\[[^\]]+\]\([^)]+\)", line) and len(_markdown_to_plain(line)) < 35:
            continue
        plain = _markdown_to_plain(line).strip(" -*")
        if not plain or len(plain) < 45:
            continue
        lowered = plain.lower()
        if lowered == title_key or lowered in {"beschreibung", "daten & fakten", "galerie", "kontakt"}:
            continue
        if lowered.startswith(("startseite", "immobilien", "produkte", "teilen")):
            continue
        return _limit_text(plain, 190)
    return ""


def _is_no_info_sentence(sentence: str) -> bool:
    """True, wenn der Satz nur sagt, dass etwas nicht bekannt/enthalten ist."""
    clean = (sentence or "").strip()
    if not clean:
        return False
    return any(pattern.search(clean) for pattern in _NO_INFO_PATTERNS)


def _substantive_answer_text(answer: str) -> str:
    """Antworttext ohne 'keine Auskunft'-Saetze - Basis fuer Karte und Fallback."""
    body, _ = _split_sources_tail(answer or "")
    plain = _markdown_to_plain(body)
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(plain) if s.strip()]
    kept = [s for s in sentences if not _is_no_info_sentence(s)]
    return " ".join(kept).strip()


def _answer_card_description(answer: str) -> str:
    """Kartenbeschreibung: nie der 'keine Informationen'-Hinweis."""
    plain = _substantive_answer_text(answer)
    plain = re.sub(r"^(ja|yes)[,:\s]+", "", plain, flags=re.IGNORECASE).strip()
    if len(plain) < 40:
        # Zu wenig Substanz -> Aufrufer nutzt den Seiten-Teaser.
        return ""
    return _limit_text(plain, 180)


_DETAIL_JUNK_RE = re.compile(
    r"(xml\s+version|encoding\s*=|<\s*svg|viewbox|xmlns|https?://|www\.|@media|function\s*\(|"
    r"\{|\}|&[a-z]+;|base64)",
    re.IGNORECASE,
)


def _is_junk_detail_value(value: str) -> bool:
    """Werte aus Scraping-Resten (SVG/XML/CSS) gehoeren nicht in die Fakten-Chips."""
    clean = (value or "").strip()
    if not clean or _DETAIL_JUNK_RE.search(clean):
        return True
    if not re.search(r"[A-Za-zÄÖÜäöüß0-9]", clean):
        return True
    # Reine Label-Zeilen ("Kontakt:") sind kein Wert.
    return clean.endswith(":")


def _extract_card_details(markdown: str, answer: str) -> List[str]:
    details: List[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        clean = _limit_text(_markdown_to_plain(value), 82).strip(" ,.;")
        key = clean.lower()
        if clean and key not in seen and len(clean) >= 3:
            seen.add(key)
            details.append(clean)

    lines = [
        _markdown_to_plain(line).strip(" :")
        for line in _strip_frontmatter_text(markdown or "").splitlines()
        if _markdown_to_plain(line).strip(" :")
    ]
    for idx, line in enumerate(lines[:-1]):
        value = lines[idx + 1]
        if _RICH_FACT_LABEL_RE.search(line) and 1 <= len(value) <= 80:
            lowered_pair = f"{line} {value}".lower()
            if line.lower() in {"immobilien", "produkte"} and not re.search(r"\d", value):
                continue
            if any(skip in lowered_pair for skip in ("startseite", "blick in die anlage", "teilen")):
                continue
            if _is_junk_detail_value(value):
                continue
            # Label darf sich nicht im Wert wiederholen ("Kontakt: Kontakt & Stoerdienst").
            if line.lower() in value.lower() or value.lower() in line.lower():
                continue
            add(f"{line}: {value}")
        if len(details) >= 3:
            return details

    compact = _markdown_to_plain((answer or "") + "\n" + (markdown or ""))
    for pattern in (
        r"\b\d+(?:[.,]\d+)?\s*(?:m²|qm|m2)\b",
        r"\b\d+\s*(?:Zimmer|Rooms|Tops|Immobilien|Wohnungen)\b",
        r"\b(?:Sauna|Pool|Terrasse|Balkon|Garten|Kachelofen|Tiefgarage|Conciergeservice)\b",
    ):
        for match in re.finditer(pattern, compact, re.IGNORECASE):
            add(match.group(0))
            if len(details) >= 3:
                return details
    return details


def _looks_unanswered(answer: str) -> bool:
    """True, wenn die Antwort im Kern keine inhaltliche Auskunft gibt.

    Nicht die Zeichenlaenge entscheidet, sondern was nach Abzug der
    'keine Auskunft'-Saetze uebrig bleibt.
    """
    body, _ = _split_sources_tail(answer or "")
    plain = _markdown_to_plain(body).strip()
    if not plain:
        return True
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(plain) if s.strip()]
    if not any(_is_no_info_sentence(s) for s in sentences):
        return False
    # 40 Zeichen: eine kurze, echte Teilantwort ("Wir bieten Strom, Wärme,
    # Wasser und Kälte an.") soll weiterhin als Antwort gelten.
    return len(_substantive_answer_text(answer)) < 40


_INTENT_STOPWORDS = {
    "welche", "welcher", "welches", "eine", "einen", "einem", "eines", "habt", "haben", "bietet",
    "bieten", "gibt", "kann", "kannst", "koennen", "können", "moechte", "möchte", "bitte", "mehr",
    "details", "informationen", "infos", "aufnehmen", "senden", "ueber", "über", "unsere", "unser",
    "euer", "eure", "which", "what", "have", "offer", "about", "would", "like", "more", "please",
    "send", "your", "does", "there", "bereich", "bereiche", "thema", "themen", "frage", "fragen",
}


def _is_object_like_title(title: str) -> bool:
    """True, wenn der Titel wie eine Bezeichnung wirkt und nicht wie ein Satz.

    Marketing-H1 wie "Frische Kühle fürs Büro und daheim." sind als Kartentitel
    und als Betreff unbrauchbar.
    """
    clean = re.sub(r"\s+", " ", (title or "").strip())
    if not clean:
        return False
    if clean.endswith((".", "!", "?")):
        return False
    return len(clean.split()) <= 6


def _item_intent_keywords(widget_cfg: Optional[Dict[str, Any]]) -> set:
    """Zusatz-Stichwoerter aus den konfigurierten Themen des Bots.

    Die eingebaute Wortliste (_RICH_ITEM_INTENT_RE) ist auf Hotel/Immobilien
    zugeschnitten. Ein Energieversorger hat "Kälte" oder "Fernwärme" - solche
    Begriffe kommen aus der Bot-Konfiguration, nicht aus dem Code.
    """
    keywords: set = set()
    for topic in ((widget_cfg or {}).get("topics") or []):
        if not isinstance(topic, dict):
            continue
        for value in (topic.get("label"), topic.get("question")):
            for token in re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", str(value or "").lower()):
                if token in _INTENT_STOPWORDS or _RICH_META_QUESTION_RE.search(token):
                    continue
                keywords.add(token)
    return keywords


def _has_rich_item_intent(
    question: str,
    answer: str,
    docs: List[Any],
    extra_keywords: Optional[set] = None,
) -> bool:
    """Karte nur bei echtem Objekt-/Angebotsbezug in der FRAGE.

    Bewusst nicht geprueft werden:
    - der Antworttext (dort steht fast immer irgendein Treffer wie
      "Leistungen", wodurch auch "Wer ist die Hall AG?" eine Karte bekam)
    - Dokumenttitel bei langen Fragen (ein Titel wie "Service auf Knopfdruck"
      loeste bei "Wie kann ich euch erreichen?" eine Produktkarte aus)

    Meta-Fragen (Kontakt, Adresse, "wer ist") bekommen nie eine Karte.
    """
    q = question or ""
    if _RICH_META_QUESTION_RE.search(q):
        return False
    if _RICH_ITEM_INTENT_RE.search(q):
        return True
    if extra_keywords:
        q_tokens = set(re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", q.lower()))
        if q_tokens & extra_keywords:
            return True
    # Sehr kurze Folgefragen ("und die Chalets?") haben oft keinen eigenen
    # Bezug - dann entscheiden die Titel der Antwortquellen.
    if len(q.split()) <= 4:
        doc_text = " ".join(
            str((getattr(doc, "metadata", {}) or {}).get(key) or "")
            for doc in docs[:3]
            for key in ("title", "section", "filename")
        )
        return bool(_RICH_ITEM_INTENT_RE.search(doc_text))
    return False


def _rich_query_text(question: str, history: Any) -> str:
    parts: List[str] = []
    if isinstance(history, list):
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("text") or "").strip()
            if content:
                parts.append(content)
    parts.append(question or "")
    return _limit_text(" ".join(parts), 1400)


def _rich_docs_for_question(ctx: BotContext, rag: Any, question: str, history: Any) -> List[Any]:
    vectordb = getattr(rag, "_vectordb", None)
    if vectordb is None:
        try:
            vectordb = _load_vectordb(ctx)
        except Exception as exc:
            print("Rich-Reply-Retrieval nicht verfügbar:", exc)
            return []
    try:
        return vectordb.similarity_search(_rich_query_text(question, history), k=4)
    except Exception as exc:
        print("Rich-Reply-Retrieval fehlgeschlagen:", exc)
        return []


def _doc_skip_keys(meta: Dict[str, Any], title: str) -> List[str]:
    """Vergleichsschluessel aus Titel, Dateiname und URL-Segment."""
    keys = [title]
    filename = str(meta.get("filename") or meta.get("source") or "")
    if filename:
        keys.append(Path(filename).stem)
    url = _public_url_from_meta(meta)
    if url:
        keys.append(url.rstrip("/").rsplit("/", 1)[-1])
    return [re.sub(r"[^a-z0-9äöüß]+", "", key.lower()) for key in keys if key]


def _pick_rich_doc(docs: List[Any], question: str) -> Optional[Any]:
    """Waehlt das Dokument, das am besten zur Frage passt.

    Vorher wurde einfach das erste nicht-ausgeschlossene Dokument genommen -
    bei "Welche Immobilien bietet ihr an?" landete so die "Über uns"-Seite auf
    der Karte. Jetzt zaehlt die Wortueberlappung mit der Frage; die
    Trefferreihenfolge des Retrievals entscheidet nur bei Gleichstand.
    """
    if not docs:
        return None

    wants_contact = bool(re.search(r"\b(kontakt|telefon|e-mail|email|contact)\b", question or "", re.IGNORECASE))
    question_tokens = set(re.findall(r"[a-zA-ZäöüÄÖÜß0-9]{4,}", (question or "").lower()))

    best_doc = None
    best_score = None
    for idx, doc in enumerate(docs):
        meta = getattr(doc, "metadata", {}) or {}
        title = _clean_card_title(str(meta.get("section") or meta.get("title") or meta.get("filename") or ""))
        skip_keys = _doc_skip_keys(meta, title)
        if not wants_contact and any(key in _RICH_SKIP_TITLES for key in skip_keys):
            continue

        url = _public_url_from_meta(meta)
        haystack = " ".join(
            [title, str(meta.get("title") or ""), str(meta.get("section") or ""), str(meta.get("filename") or ""), url]
        ).lower()
        haystack_tokens = set(re.findall(r"[a-zA-ZäöüÄÖÜß0-9]{4,}", haystack))
        overlap = len(question_tokens & haystack_tokens)
        # Ueberlappung dominiert, dann Vollstaendigkeit, dann Retrieval-Rang.
        score = (overlap, 1 if (title and url) else 0, -idx)
        if best_score is None or score > best_score:
            best_score = score
            best_doc = doc

    # Wenn alle Kandidaten ausgeschlossen sind, gibt es bewusst keine Karte
    # (statt einer Datenschutz-/Impressum-Karte).
    return best_doc


def _build_rich_card(ctx: BotContext, doc: Any, answer: str, lang: str) -> Optional[Dict[str, Any]]:
    markdown = _read_doc_markdown(ctx, doc)
    meta = getattr(doc, "metadata", {}) or {}
    # Kandidaten in Prioritaet: H1, Abschnitt, Seitentitel. Genommen wird der
    # erste, der wie eine Bezeichnung aussieht - Slogans ("Frische Kühle fürs
    # Büro und daheim.") und FAQ-Fragen als Abschnittstitel sind unbrauchbar.
    title_candidates = [
        _extract_first_heading(markdown),
        _clean_card_title(str(meta.get("section") or "")),
        _clean_card_title(str(meta.get("title") or meta.get("filename") or "")),
    ]
    title = next(
        (candidate for candidate in title_candidates if _is_object_like_title(candidate)),
        next((candidate for candidate in title_candidates if candidate), ""),
    )
    description = _answer_card_description(answer) or _extract_card_teaser(markdown, title)
    url = _doc_public_url(ctx, doc, markdown)
    image_url = _extract_first_image_url(markdown)
    details = _extract_card_details(markdown, answer)

    if not title and not description:
        return None

    card: Dict[str, Any] = {
        "title": title or ("Recommendation" if _normalize_chat_lang(lang) == "en" else "Empfehlung"),
        "description": description,
        "details": details,
    }
    if url:
        card["url"] = url
    if image_url:
        card["image_url"] = image_url
    return card


def _append_action(actions: List[Dict[str, str]], label: str, question: str = "", url: str = "") -> None:
    clean_label = _limit_text(label, 34).strip()
    if not clean_label:
        return
    action: Dict[str, str] = {"label": clean_label}
    if url and _is_safe_frontend_url(url):
        action["type"] = "link"
        action["url"] = url
    elif question:
        action["type"] = "question"
        action["question"] = question.strip()
    else:
        return
    if any(existing.get("label", "").lower() == clean_label.lower() for existing in actions):
        return
    actions.append(action)


def _action_subject(title: str, lang: str) -> str:
    """Kartentitel als Betreff nutzbar machen.

    Viele H1 sind Saetze ("Wir sind fuer Sie da.") - als Betreff ergeben sie
    Unsinn. Dann lieber eine neutrale Bezeichnung.
    """
    lang_key = _normalize_chat_lang(lang) or "de"
    fallback = DEFAULT_ACTION_SUBJECT.get(lang_key, DEFAULT_ACTION_SUBJECT["de"])
    clean = re.sub(r"\s+", " ", (title or "").strip())
    if not clean:
        return fallback
    if not _is_object_like_title(clean):
        return fallback
    return clean.rstrip(" .!?,;:") or fallback


def _build_quick_actions(
    card: Optional[Dict[str, Any]],
    question: str,
    answer: str,
    lang: str,
    widget_cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    lang_key = _normalize_chat_lang(lang) or "de"
    cfg = widget_cfg or {}
    labels = cfg.get("action_labels") or DEFAULT_ACTION_LABELS.get(lang_key) or DEFAULT_ACTION_LABELS["de"]
    actions: List[Dict[str, str]] = []

    if card:
        subject = _action_subject(card.get("title") or "", lang_key)
        card_url = card.get("url") or ""
        if card_url:
            _append_action(actions, labels.get("view", ""), url=card_url)
        if lang_key == "en":
            _append_action(
                actions,
                labels.get("inquiry", ""),
                f"I would like to send an inquiry about {subject}. What information do you need?",
            )
            _append_action(actions, labels.get("details", ""), f"What details are available about {subject}?")
        else:
            _append_action(
                actions,
                labels.get("inquiry", ""),
                f"Ich möchte eine Anfrage senden. Es geht um: {subject}. Welche Informationen brauchst du?",
            )
            _append_action(actions, labels.get("details", ""), f"Welche Details kannst du zu \"{subject}\" nennen?")
        return actions[:3]

    # Kontaktweg kommt aus der Bot-Konfiguration bzw. ENV - nie aus dem
    # Antworttext (dort steht meist nur eine Quellen-URL).
    contact_url = _contact_action_url(cfg.get("contact") or {})
    if lang_key == "en":
        _append_action(actions, labels.get("details", ""), "Can you explain that in more detail?")
        _append_action(actions, labels.get("next_step", ""), "What is the next step?")
        if contact_url:
            _append_action(actions, labels.get("contact", ""), url=contact_url)
        else:
            _append_action(actions, labels.get("contact", ""), "How can I contact someone about this?")
    else:
        _append_action(actions, labels.get("details", ""), "Kannst du das genauer erklären?")
        _append_action(actions, labels.get("next_step", ""), "Was ist der nächste Schritt?")
        if contact_url:
            _append_action(actions, labels.get("contact", ""), url=contact_url)
        else:
            _append_action(actions, labels.get("contact", ""), "Wie kann ich dazu Kontakt aufnehmen?")
    return actions[:3]


# ---------------------------------------------------------------------------
# Antwort-Buttons per KI
# Labels und Folgefragen entstehen aus dem laufenden Gespraech statt aus festen
# Textbausteinen. Die konfigurierten action_labels bleiben als Fallback, wenn
# der Call scheitert, zu lange braucht oder unbrauchbares JSON liefert.
# ---------------------------------------------------------------------------

AI_ACTIONS_LABEL_MAX = 24
AI_ACTIONS_LABEL_WORDS = 3
AI_ACTIONS_QUESTION_MAX = 180
AI_ACTIONS_MAX = 3
try:
    AI_ACTIONS_TIMEOUT = float(os.environ.get("RAG_AI_ACTIONS_TIMEOUT", "8"))
except Exception:
    AI_ACTIONS_TIMEOUT = 8.0

_AI_ACTIONS_SYSTEM = (
    "WICHTIGSTE FORMREGEL: label ist eine Menue-Beschriftung aus ZWEI bis DREI Woertern. "
    "Zaehle die Woerter, bevor du antwortest. Vier oder mehr Woerter sind verboten, ebenso Fragen "
    "und ganze Saetze.\n\n"
    "Du erzeugst die Klick-Buttons, die unter der Antwort eines Chat-Assistenten "
    "auf einer Firmen-Website stehen.\n"
    "Regeln:\n"
    "- Zwei bis drei Buttons, die genau zu diesem Gespraech passen und den Nutzer "
    "einen Schritt weiterbringen.\n"
    "- Jeder Button oeffnet ein NEUES Thema, das im bisherigen Gespraech noch nicht vorkam. "
    "Wiederhole nie die aktuelle Frage, eine fruehere Frage oder den Inhalt der Antwort - auch "
    "nicht anders formuliert. Wurde nach Preisen gefragt, biete etwas anderes an.\n"
    "- Die Buttons sollen neugierig machen: nenne Themen, die der Nutzer wahrscheinlich als "
    "naechstes interessant findet.\n"
    "- label: zwei bis drei Woerter, hoechstens 24 Zeichen. Es ist eine Menue-Beschriftung, "
    "keine Frage und kein Satz. Gut: \"Zimmer ansehen\", \"Preise & Pauschalen\", \"Anfahrt\", "
    "\"Termin anfragen\". Schlecht: \"Wo befindet sich die Zentrale?\", \"Ich moechte mehr wissen\". "
    "Keine Emojis, kein Satzzeichen am Ende, niemals abgeschnittene Wortgruppen.\n"
    "- question: die Nachricht, die beim Klick als Nutzerfrage gesendet wird - ein "
    "vollstaendiger, eigenstaendig verstaendlicher Satz.\n"
    "- SPRACHE, wichtigste Regel: Schreibe ALLE Werte von label und question ausschliesslich in der "
    "Sprache, die im Kontext unter \"Sprache der Buttons\" steht. Diese Anweisung ist auf Deutsch, "
    "das aendert daran nichts; auch aeltere Nachrichten im Gespraech aendern daran nichts. Ist die "
    "Sprache Tuerkisch, heisst ein Anruf-Button \"Ara\" und nicht \"Anrufen\".\n"
    "- Erfinde nichts: keine Preise, Zahlen, Angebote, Telefonnummern oder URLs, "
    "die nicht im Kontext stehen.\n"
    "- Aktions-Buttons (type \"link\") nur mit einem target, das der Kontext unter "
    "\"Verfuegbare Aktionen\" auflistet: \"card\", \"contact\", \"phone\" oder \"email\". "
    "Die Adresse setzt das System, du gibst nie eine URL oder Nummer aus. "
    "Bei type \"link\" kein question angeben.\n"
    "- Wenn \"phone\" verfuegbar ist und Anrufen im Gespraech sinnvoll waere (Beratung, "
    "Termin, dringende Rueckfrage, Kontaktwunsch), setze einen Anruf-Button an die erste "
    "Stelle; das Label nennt das Anrufen.\n"
    "- Hoechstens zwei Aktions-Buttons, jedes target nur einmal.\n"
    "- Mindestens ein Button muss type \"question\" sein und unter den Aktions-Buttons stehen.\n"
    "- Keine zwei Buttons mit gleicher Bedeutung.\n"
    "Gib zusaetzlich \"card\" an: die Nummer der Seite aus \"Verfuegbare Seiten\", die als Karte unter "
    "der Antwort erscheinen soll. Waehle nur eine Seite, die genau das Thema der Antwort vertieft und "
    "dem Nutzer echten Mehrwert bringt. Passt keine Seite wirklich zum Inhalt der Antwort, ist die "
    "Antwort allgemein, eine Begruessung oder eine Absage, gib 0 an. Im Zweifel immer 0 - eine "
    "unpassende Karte ist schlechter als keine.\n"
    "Antworte ausschliesslich mit JSON in dieser Form: "
    "{\"card\": 0, \"actions\": [{\"label\": \"...\", \"type\": \"question\", \"question\": \"...\"}]}"
)

# Telefonnummern nur mit klarem Signal erkennen. Ohne Signal landen Preise,
# Jahreszahlen, Uhrzeiten und Groessenangaben im Anruf-Button.
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().\-/]{5,}\d")
_PHONE_CUE_RE = re.compile(
    r"(?:tel|telefon|telephone|fon|phone|mobil|handy|hotline|zentrale|durchwahl|festnetz|"
    r"whatsapp|anruf|anrufen|rufen sie(?: uns)?(?: gerne)? an|ruf(?:e)? uns an|"
    r"erreichbar|erreichst|erreichen sie|erreichen|call us|call|nummer|number)"
    r"[^0-9+]{0,20}$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _normalize_phone(raw: str) -> str:
    """Rohtext zu einer waehlbaren Nummer. Leerer String = unbrauchbar."""
    cleaned = re.sub(r"[^\d+]", "", str(raw or ""))
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    digits = cleaned.lstrip("+")
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        return ""
    return cleaned


def _phone_from_text(text: str) -> str:
    """Erste plausible Telefonnummer aus einem Text (Antwort, Quellen).

    Akzeptiert wird eine Zahlenfolge nur, wenn sie eine Landesvorwahl hat, ein
    Hinweiswort davor steht oder sie wie eine gegliederte Rufnummer aussieht.
    """
    plain = _markdown_to_plain(text or "")
    for match in _PHONE_CANDIDATE_RE.finditer(plain):
        candidate = match.group(0)
        number = _normalize_phone(candidate)
        if not number:
            continue
        has_cue = bool(_PHONE_CUE_RE.search(plain[max(0, match.start() - 40) : match.start()]))
        looks_grouped = "/" in candidate and len(number.lstrip("+")) >= 9
        if number.startswith("+") or has_cue or looks_grouped:
            return number
    return ""


def _email_from_text(text: str) -> str:
    match = _EMAIL_RE.search(_markdown_to_plain(text or ""))
    return match.group(0) if match else ""


def _sources_text(docs: Optional[List[Any]]) -> str:
    """Rohtext der Quellen, gegen den Nummern und Adressen geprueft werden."""
    parts: List[str] = []
    for doc in (docs or [])[:6]:
        content = getattr(doc, "page_content", "") or ""
        if content:
            parts.append(str(content))
        meta = getattr(doc, "metadata", {}) or {}
        for key in ("title", "section", "url", "source"):
            value = meta.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(parts)


def _phone_in_sources(number: str, sources: str) -> bool:
    """Steht die Nummer wirklich so in den Quellen?

    Verglichen wird gegen echte Nummern-Kandidaten aus dem Quelltext, nicht
    gegen alle Ziffern am Stueck: sonst gilt eine erfundene Nummer als belegt,
    weil ihre Endziffern zufaellig in einem Zaehlerstand vorkommen.
    Schreibweisen (0043, +43, 0...) werden ueber die letzten Ziffern verglichen.
    """
    digits = re.sub(r"\D", "", number or "")
    if len(digits) < 7:
        return False
    tail = digits[-7:]
    for match in _PHONE_CANDIDATE_RE.finditer(_markdown_to_plain(sources or "")):
        candidate = re.sub(r"\D", "", match.group(0))
        if len(candidate) >= 7 and candidate.endswith(tail):
            return True
    return False


def _email_in_sources(email: str, sources: str) -> bool:
    return bool(email) and email.lower() in (sources or "").lower()

_ai_actions_llm_cache: Dict[str, Any] = {}


def _ai_actions_enabled() -> bool:
    return _as_bool(os.environ.get("RAG_AI_ACTIONS"), True)


def _ai_actions_llm(ctx: BotContext) -> Optional[Any]:
    """LLM fuer die Button-Generierung - bewusst mit hartem Timeout ohne Retry.

    Die Buttons sind Beiwerk: lieber die statischen Fallback-Texte als eine
    Antwort, die wegen eines haengenden Zweit-Calls spaeter ankommt.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    model_name = (
        os.environ.get("RAG_AI_ACTIONS_MODEL")
        or os.environ.get("RAG_CHAT_MODEL")
        or ctx.model
        or DEFAULT_MODEL
    ).strip()
    cached = _ai_actions_llm_cache.get(model_name)
    if cached is not None:
        return cached
    try:
        llm = ChatOpenAI(
            api_key=api_key,
            model=model_name,
            temperature=0.4,
            max_tokens=260,
            timeout=AI_ACTIONS_TIMEOUT,
            max_retries=0,
        )
    except Exception as exc:
        print("Button-LLM konnte nicht erstellt werden:", exc)
        return None
    _ai_actions_llm_cache[model_name] = llm
    return llm


def _history_lines(history: Any, limit: int = 4) -> str:
    """Die letzten Gespraechszuege als kurzer Text fuer den Prompt."""
    if not isinstance(history, list):
        return ""
    lines: List[str] = []
    for item in history[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("sender") or "user").strip().lower()
        content = _markdown_to_plain(str(item.get("content") or item.get("text") or "")).strip()
        if not content:
            continue
        speaker = "Assistent" if role in {"assistant", "ai", "bot"} else "Nutzer"
        lines.append(f"{speaker}: {_limit_text(content, 240)}")
    return "\n".join(lines)


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Erstes JSON-Objekt aus einer Modellantwort lesen (auch in ```-Bloecken)."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


# Funktionswoerter, mit denen ein Label niemals enden darf - sonst steht auf
# dem Button ein Fragment wie "Wo befindet sich die".
_LABEL_TAIL_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "zu", "zum", "zur", "in", "im", "an", "am", "auf", "fuer", "für", "mit",
    "von", "bei", "sich", "wie", "wo", "was", "ist", "sind", "the", "a", "an", "and", "or",
    "to", "for", "with", "of", "on", "at", "is", "are", "how", "what", "where", "my", "your",
    "ve", "ile", "için", "icin", "bir", "de", "la", "le", "les", "des", "el", "il", "et",
}


# Woerter, mit denen ein Satz beginnt - als Button-Beschriftung unbrauchbar.
_LABEL_SENTENCE_STARTERS = {
    "wo", "wie", "was", "wann", "warum", "wer", "welche", "welcher", "welches", "ist", "sind",
    "gibt", "kann", "koennen", "können", "haben", "habt", "ich", "du", "sie", "wir", "moechte",
    "möchte", "how", "what", "where", "when", "why", "who", "which", "can", "do", "does", "is",
    "are", "i", "you", "we", "nasıl", "nasil", "nerede", "hangi", "kim", "quel", "quelle",
    "comment", "donde", "dónde", "como", "cómo", "dove", "quanto",
}


def _clean_action_label(value: Any) -> str:
    """Button-Beschriftung: kurze Menue-Bezeichnung, nie ein Satz oder Fragment."""
    label = re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'")
    label = re.sub(r"[.!?;:,\-\u2026]+$", "", label).strip()
    if not label:
        return ""

    words = label.split(" ")
    # Ein Satz laesst sich nicht zu einem guten Label kuerzen - dann lieber
    # keinen Button als "Wo befindet sich die".
    if len(words) > 5:
        return ""
    if len(words) > 1 and words[0].lower().strip("¿¡") in _LABEL_SENTENCE_STARTERS:
        return ""
    if len(words) > AI_ACTIONS_LABEL_WORDS:
        words = words[:AI_ACTIONS_LABEL_WORDS]
    # Zeichenlimit: lieber ein Wort weniger als ein abgeschnittenes Wort.
    while words and len(" ".join(words)) > AI_ACTIONS_LABEL_MAX:
        words.pop()
    # Fuellwoerter am Ende abschneiden, sonst bleibt ein Fragment stehen.
    while len(words) > 1 and words[-1].lower().strip(".,;:!?") in _LABEL_TAIL_STOPWORDS:
        words.pop()

    label = " ".join(words).strip()
    if len(words) == 1 and words[0].lower() in _LABEL_TAIL_STOPWORDS:
        return ""
    return re.sub(r"[.!?;:,\-]+$", "", label).strip()


def _compare_tokens(text: str) -> set:
    """Wortmenge fuer den Aehnlichkeitsvergleich (ohne Fuellwoerter)."""
    tokens = set(re.findall(r"[a-zA-Z0-9äöüÄÖÜßçğıöşü]{3,}", (text or "").lower()))
    return {token for token in tokens if token not in _LABEL_TAIL_STOPWORDS}


def _asked_token_sets(question: str, history: Any) -> List[set]:
    """Wortmengen aller bisherigen Nutzerfragen inklusive der aktuellen."""
    sets = [_compare_tokens(question)]
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("sender") or "user").strip().lower()
            if role in {"assistant", "ai", "bot"}:
                continue
            content = str(item.get("content") or item.get("text") or "")
            if content.strip():
                sets.append(_compare_tokens(content))
    return [tokens for tokens in sets if tokens]


def _is_repeat_action(text: str, known_sets: List[set], ratio: float = 0.75, min_overlap: int = 2) -> bool:
    """True, wenn der Button wiederholt, was schon gefragt oder gezeigt wurde.

    Gegen die aktuelle Frage wird milder geprueft (viele sinnvolle Folgefragen
    teilen ein Wort mit ihr), gegen bereits gezeigte Buttons strenger.
    """
    tokens = _compare_tokens(text)
    if not tokens:
        return False
    for known in known_sets:
        if not known:
            continue
        overlap = len(tokens & known)
        if overlap >= min_overlap and overlap / min(len(tokens), len(known)) >= ratio:
            return True
    return False


def _ai_quick_actions(
    ctx: BotContext,
    candidates: List[Dict[str, Any]],
    question: str,
    answer: str,
    history: Any,
    lang: str,
    widget_cfg: Optional[Dict[str, Any]] = None,
    source_docs: Optional[List[Any]] = None,
    offered: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]], bool]:
    """Buttons und passende Karte aus dem Gespraech.

    Rueckgabe: (Aktionen, gewaehlte Karte, ob die KI geantwortet hat).
    """
    if not _ai_actions_enabled():
        return [], None, False
    llm = _ai_actions_llm(ctx)
    if llm is None:
        return [], None, False

    cfg = widget_cfg or {}
    lang_key = _normalize_chat_lang(lang) or "de"
    # Buttons folgen der Sprache der aktuellen Frage - nicht der Website und
    # nicht dem, was das Modell aus aelteren Nachrichten herausliest.
    button_lang = _detect_message_lang(question) or lang_key
    lang_name = _lang_display_name(button_lang)
    contact = cfg.get("contact") or {}
    # Nur eine echte Seite darf "contact" sein - sonst verspricht das Label eine
    # Kontaktseite und der Klick oeffnet das Mailprogramm.
    contact_page = str(contact.get("url") or "").strip()
    if not contact_page.lower().startswith(("http://", "https://", "/")):
        contact_page = ""

    # Nummer/Adresse aus der Antwort schlaegt die konfigurierte: sie gehoert zu
    # genau dem Thema, ueber das gerade gesprochen wird. Sie muss aber in den
    # Quellen belegt sein, sonst waehlt der Button eine erfundene Nummer.
    # Globale ENV-Defaults bleiben aussen vor - in einem Multi-Tenant-Setup
    # gehoeren sie keinem einzelnen Bot.
    sources = _sources_text(source_docs)
    configured_phone = _normalize_phone(contact.get("phone") or "")
    configured_email = str(contact.get("email") or "").strip()

    phone_from_answer = _phone_from_text(answer)
    if phone_from_answer and not _phone_in_sources(phone_from_answer, sources):
        print("Anruf-Button verworfen, Nummer nicht in den Quellen:", phone_from_answer)
        phone_from_answer = ""
    email_from_answer = _email_from_text(answer)
    if email_from_answer and not _email_in_sources(email_from_answer, sources):
        email_from_answer = ""

    phone = phone_from_answer or configured_phone
    email = email_from_answer or configured_email
    link_targets = {
        "card": "",
        "contact": contact_page,
        "phone": f"tel:{phone}" if phone else "",
        "email": f"mailto:{email}" if email else "",
    }

    topics = [
        str(topic.get("label") or "").strip()
        for topic in (cfg.get("topics") or [])
        if isinstance(topic, dict) and str(topic.get("label") or "").strip()
    ]

    available = [
        'card (oeffnet die Seite, die du unter "card" auswaehlst)' if candidates else "",
        "contact (Kontaktseite des Unternehmens)" if link_targets["contact"] else "",
        f"phone (waehlt {phone} direkt auf dem Geraet)" if phone else "",
        f"email (oeffnet eine Mail an {email})" if email else "",
    ]
    context_lines = [
        f"Sprache der Buttons: {lang_name}",
        "Verfuegbare Aktionen: " + (", ".join(filter(None, available)) or "keine"),
    ]
    verlauf = _history_lines(history)
    if verlauf:
        context_lines.append(f"Bisheriges Gespraech:\n{verlauf}")
    if topics:
        context_lines.append("Themen des Unternehmens: " + ", ".join(topics[:8]))
    if candidates:
        lines = []
        for idx, item in enumerate(candidates, start=1):
            path = str(item.get("url") or "").split("//", 1)[-1]
            path = "/" + path.split("/", 1)[1] if "/" in path else path
            lines.append(f"{idx}) {item.get('title', '')} - {path}")
        context_lines.append('Verfuegbare Seiten (fuer "card"):\n' + "\n".join(lines))
    else:
        context_lines.append('Verfuegbare Seiten: keine - "card" muss 0 sein.')
    shown = [str(label).strip() for label in (offered or []) if str(label).strip()][-10:]
    if shown:
        context_lines.append(
            "Diese Buttons wurden im Gespraech schon angezeigt - biete keinen davon noch einmal an, "
            "auch nicht anders formuliert:\n- " + "\n- ".join(shown)
        )
    context_lines.append(f"Aktuelle Frage des Nutzers: {_limit_text(question, 300)}")
    context_lines.append(
        "Antwort des Assistenten:\n"
        + _limit_text(_markdown_to_plain(_split_sources_tail(answer or "")[0]), 900)
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(content=_AI_ACTIONS_SYSTEM),
                HumanMessage(content="\n\n".join(context_lines)),
            ]
        )
    except Exception as exc:
        print("Button-Generierung fehlgeschlagen:", exc)
        return [], None, False

    content = getattr(response, "content", response)
    if isinstance(content, list):  # manche Modelle liefern Content-Bloecke
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    payload = _parse_json_object(str(content or ""))
    if not payload:
        print("Button-Generierung: kein JSON in der Antwort")
        return [], None, False

    # Karte: nur die vom Modell gewaehlte Seite, sonst keine.
    try:
        choice = int(payload.get("card") or 0)
    except Exception:
        choice = 0
    chosen_card = candidates[choice - 1] if 1 <= choice <= len(candidates) else None
    if chosen_card:
        link_targets["card"] = chosen_card.get("url") or ""

    # Aktionen (Anrufen, Seite oeffnen) stehen vor den Folgefragen - sie gehoeren
    # optisch zur Karte darueber, die Fragen sind der Rest des Gespraechs.
    link_actions: List[Dict[str, str]] = []
    question_actions: List[Dict[str, str]] = []
    used_targets: set = set()
    asked_sets = _asked_token_sets(question, history)
    shown_sets = [_compare_tokens(label) for label in (offered or []) if str(label).strip()]
    shown_sets = [tokens for tokens in shown_sets if tokens]
    for item in (payload.get("actions") or [])[:6]:
        if not isinstance(item, dict):
            continue
        label = _clean_action_label(item.get("label"))
        if not label:
            continue
        kind = str(item.get("type") or "question").strip().lower()
        target = str(item.get("target") or item.get("url_ref") or "").strip().lower()
        # Das Modell schreibt oft "type": "phone" statt type link + target phone.
        if not target and kind in link_targets:
            target, kind = kind, "link"
        if kind == "link":
            url = link_targets.get(target) or ""
            # Modell-URLs werden nie uebernommen - nur die bekannten Ziele.
            if not url or target in used_targets or len(link_actions) >= 2:
                continue
            _append_action(link_actions, label, url=url)
            used_targets.add(target)
            continue
        if len(question_actions) >= AI_ACTIONS_MAX:
            continue
        follow_up = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
        # Ohne Fragetext ist es ein missglueckter Aktions-Button ("Anrufen"):
        # als Chatnachricht abgeschickt waere er sinnlos.
        if len(follow_up) < 6:
            continue
        # Kein Button, der wiederholt, was schon gefragt oder schon angeboten wurde.
        if _is_repeat_action(follow_up, asked_sets) or _is_repeat_action(
            label, shown_sets, ratio=0.6, min_overlap=1
        ):
            continue
        _append_action(
            question_actions, label, question=_limit_text(follow_up, AI_ACTIONS_QUESTION_MAX)
        )

    actions = link_actions + [
        action
        for action in question_actions
        if not any(existing["label"].lower() == action["label"].lower() for existing in link_actions)
    ]
    return actions[:AI_ACTIONS_MAX], chosen_card, True


# Seiten, die als Antwortkarte nie weiterhelfen - zusaetzlich zu _RICH_SKIP_TITLES
# wird hier auch die URL geprueft (Startseite, Archive, Funktionsseiten).
_BOILERPLATE_URL_MARKERS = (
    "impressum", "datenschutz", "privacy", "/agb", "terms", "cookie", "sitemap",
    "widerruf", "disclaimer", "newsletter", "login", "anmelden", "warenkorb",
    "checkout", "kasse", "mein-konto", "my-account", "/suche", "/search", "404",
    "/category/", "/tag/", "/author/",
)


def _is_boilerplate_page(title: str, url: str) -> bool:
    haystack = f"{title} {url}".lower()
    if any(marker in haystack for marker in _BOILERPLATE_URL_MARKERS):
        return True
    # Startseite: kein sinnvolles Ziel fuer eine Detailkarte.
    path = re.sub(r"^https?://[^/]+", "", str(url or "")).split("?", 1)[0].split("#", 1)[0]
    return path.strip("/") == ""


def _build_rich_reply(
    ctx: BotContext,
    rag: Any,
    question: str,
    answer: str,
    history: Any,
    lang: str,
    docs: Optional[List[Any]] = None,
    widget_cfg: Optional[Dict[str, Any]] = None,
    offered: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Karte + Aktionen zur Antwort.

    `docs` sind die Dokumente, auf denen die Antwort beruht. Nur wenn sie
    fehlen, wird ersatzweise neu gesucht - sonst koennte die Karte eine andere
    Seite zeigen als die Antwort (und es kostet einen Embedding-Call).
    """
    card: Optional[Dict[str, Any]] = None
    source_docs = list(docs or [])
    if not source_docs:
        source_docs = _rich_docs_for_question(ctx, rag, question, history)

    if widget_cfg is None:
        try:
            widget_cfg = _resolve_widget_config(ctx, lang)
        except Exception as exc:
            print("Widget-Konfiguration für Rich-Reply nicht lesbar:", exc)
            widget_cfg = {}

    # Kandidaten sammeln - welche davon zur Antwort passt, entscheidet der
    # Button-Call. Eine Karte, die nur der beste Suchtreffer ist, zeigt sonst
    # irgendeine Seite ohne Bezug zur Antwort.
    candidates: List[Dict[str, Any]] = []
    if source_docs and not _looks_unanswered(answer):
        seen_urls = set()
        for doc in source_docs[:6]:
            if len(candidates) >= 3:
                break
            meta = getattr(doc, "metadata", {}) or {}
            title = _clean_card_title(str(meta.get("section") or meta.get("title") or meta.get("filename") or ""))
            if any(key in _RICH_SKIP_TITLES for key in _doc_skip_keys(meta, title)):
                continue
            built = _build_rich_card(ctx, doc, answer, lang)
            if not built or not built.get("url") or not built.get("title"):
                continue
            if _is_boilerplate_page(str(built.get("title") or ""), str(built.get("url") or "")):
                continue
            if built["url"] in seen_urls:
                continue
            seen_urls.add(built["url"])
            candidates.append(built)

    # Erst die KI fragen; die statischen Labels sind nur noch das Sicherheitsnetz.
    actions: List[Dict[str, str]] = []
    ai_answered = False
    try:
        actions, card, ai_answered = _ai_quick_actions(
            ctx, candidates, question, answer, history, lang,
            widget_cfg=widget_cfg, source_docs=source_docs, offered=offered,
        )
    except Exception as exc:
        print("KI-Buttons nicht verfügbar:", exc)
    if not ai_answered and candidates:
        # Ohne KI-Urteil greift die alte Heuristik: Karte nur bei klarem
        # Objekt-/Angebotsbezug in der Frage.
        intent_keywords = _item_intent_keywords(widget_cfg)
        if _has_rich_item_intent(question, answer, source_docs, extra_keywords=intent_keywords):
            card = candidates[0]
    if not actions:
        actions = _build_quick_actions(card, question, answer, lang, widget_cfg=widget_cfg)

    rich: Dict[str, Any] = {"version": 1, "actions": actions}
    if card:
        rich["cards"] = [card]
    return rich


def _read_faqs(ctx: BotContext) -> List[Dict[str, str]]:
    if not ctx.faq_path.exists():
        return []

    content = ctx.faq_path.read_text(encoding="utf-8")
    items = []
    question = None
    answer_lines = []

    for line in content.splitlines():
        if line.startswith("## "):
            if question is not None:
                items.append({"question": question, "answer": "\n".join(answer_lines).strip()})
            question = line[3:].strip()
            answer_lines = []
        elif question is not None:
            answer_lines.append(line)

    if question is not None:
        items.append({"question": question, "answer": "\n".join(answer_lines).strip()})

    return [item for item in items if item["question"] or item["answer"]]


def _write_faqs(ctx: BotContext, faqs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    cleaned = []
    for item in faqs:
        q = (item.get("question") or "").strip()
        a = (item.get("answer") or "").strip()
        if not q and not a:
            continue
        cleaned.append({"question": q, "answer": a})

    lines = ["# Manuelle FAQs", ""]
    for item in cleaned:
        lines.append(f"## {item['question']}")
        if item["answer"]:
            lines.append(item["answer"])
        lines.append("")

    ctx.faq_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.faq_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return cleaned


def _slugify_filename(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-")
    return slug or "upload"


def _normalize_pdf_text_block(text: str) -> str:
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


def _pdf_bytes_to_markdown(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        text = _normalize_pdf_text_block(text)
        if text:
            parts.append(text)
    content = "\n\n".join(parts).strip()
    if not content:
        raise ValueError("Konnte keinen Text aus dem PDF extrahieren.")
    return content


def _write_pdf_markdown(ctx: BotContext, orig_filename: str, markdown_body: str) -> str:
    slug = _slugify_filename(Path(orig_filename).stem)
    md_name = f"pdf-{slug}.md"
    out_path = ctx.upload_dir / md_name
    uploaded_at = datetime.utcnow().isoformat() + "Z"
    frontmatter = "\n".join(
        [
            "---",
            f"source: upload:{orig_filename}",
            f"title: PDF Upload {orig_filename}",
            f"uploaded_at: {uploaded_at}",
            "---",
            "",
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + markdown_body.strip() + "\n", encoding="utf-8")
    return str(out_path.relative_to(BASE_DIR))


def _record_chat_event(
    ctx: BotContext,
    user_id: Optional[str],
    question: str,
    answer: Optional[str],
    status: str,
    error: Optional[str] = None,
) -> None:
    """Persistiert Chat-Metriken in Supabase. Fehler werden nur geloggt."""
    event = {
        "bot_id": ctx.bot_id,
        "bot_slug": ctx.bot_slug,
        "customer_id": ctx.customer_id,
        "user_id": user_id or None,
        "question": question,
        "answer": answer,
        "status": status,
        "error": error,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        _get_supabase_client().table("chat_events").insert(event).execute()
    except Exception as exc:
        print("Konnte chat_event nicht speichern:", exc)


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v).replace(tzinfo=None)
    except Exception:
        return None


TOPIC_CATEGORIES = {
    "Preise & Kosten": {
        "icon": "💰",
        "words": {
            "preis", "preise", "preiswert", "preisliste", "preise", "preisen",
            "kosten", "kostenlos", "kostensparend", "kostenpunkt", "kostenfrage",
            "bezahlung", "zahlung", "zahlungsweise", "zahlungsart", "zahlungsoption",
            "rechnung", "rechnungen", "rechnungsadresse", "rechnungsbetrag",
            "tarif", "tarife", "tarifänderung", "tarifwechsel",
            "gebuhr", "gebühren", "gebühr", "gebuhren",
            "abrechnung", "abrechnungszeitraum", "abrechnungsmodell",
            "kosti", "teuer", "billig", "günstig", "gunstig", "rabatt", "rabatte",
            "angebot", "angebote", "sonderangebot", "sonderangebote",
            "preiserhöhung", "preissenkung", "preisnachlass",
            "abonnement", "abo", "abos", "subscription",
            "monatspreis", "jahrespreis", "stückpreis", "stückzahl",
            "kreditkarte", "lastschrift", "überweisung", "uberweisung",
            "zahlungsplan", "ratenzahlung", "raten", "vertrag", "verträge",
            "vertragslaufzeit", "laufzeit", "mindestlaufzeit", "verlängerung",
            "stornierung", "storno", "erstattung", "rückbuchung",
            "kostenlos", "gratis", "free", "freeplan", "freemium",
            "pricing", "price", "cost", "costs", "discount",
            "invoice", "subscription", "plan", "plans", "tier",
        },
    },
    "Technik & Integration": {
        "icon": "🔧",
        "words": {
            "api", "apis", "schnittstelle", "schnittstellen", "endpoint", "endpoints",
            "integration", "integrations", "integrationen", "integrieren", "einbinden",
            "webhook", "webhooks", "rest", "graphql", "json", "xml",
            "sdk", "sdk", "library", "bibliothek", "modul", "module",
            "plugin", "plugins", "erweiterung", "erweiterungen", "addon", "add-on",
            "entwickler", "developer", "dev", "devs", "programmierung",
            "code", "coding", "programmentwicklung", "softwareentwicklung",
            "konfiguration", "konfigurieren", "config", "setup", "einrichten",
            "einstellungen", "settings", "parameter", "optionen", "einstellung",
            "technisch", "technische", "technologie", "technologien",
            "schnittstellendokumentation", "dokumentation", "docs",
            "fernzugriff", "remote", "hosting", "server", "cloud",
            "sso", "oauth", "authentifizierung", "autorisierung",
            "implementation", "implementierung", "anbindung", "verknüpfung",
            "connect", "connector", "zapier", "make", "n8n", "automate",
            "automation", "automatisierung", "automatisieren",
        },
    },
    "Onboarding & Einrichtung": {
        "icon": "🚀",
        "words": {
            "onboarding", "einrichten", "einrichtung", "setup", "start", "starten",
            "beginn", "beginnen", "erste", "erster", "erstes", "ersten", "erstem",
            "anfang", "anfangen", "anfanger", "anfänger", "anfänglich",
            "einführung", "einfuehrung", "einführen", "tutorial", "tutorials",
            "anleitung", "anleitungen", "guide", "guides", "handbuch",
            "schritt", "schritte", "schrittweise", "schritt-für-schritt",
            "schnellstart", "quickstart", "quick-start", "getting-started",
            "registrierung", "registrieren", "anmeldung", "anmelden", "signup",
            "account", "konto", "kontoeinrichtung", "profil", "profilierung",
            "demo", "demoversion", "testversion", "testphase",
            "testaccount", "testkonto", "testzugang", "trial",
            "konfiguration", "konfigurieren", "initialisierung", "initialisieren",
            "willkommen", "welcome", "startseite",
        },
    },
    "Support & Hilfe": {
        "icon": "💬",
        "words": {
            "support", "hilfsbereitschaft", "hilfe", "helfen", "unterstützung",
            "unterstützen", "assistenz", "beistand", "beratung", "berate",
            "kontakt", "kontaktieren", "erreichbar", "erreichbarkeit",
            "chat", "chatten", "livechat", "live-chat",
            "ticket", "tickets", "ticketnummer",
            "problem", "probleme", "problematisch", "problemlösung",
            "fehler", "fehlerhaft", "fehlermeldung", "fehlercode",
            "bug", "bugs", "fehlerbehebung", "debug", "debugging",
            "stoerung", "störung", "störungen", "ausfall", "ausfälle",
            "meldung", "melden", "gemeldet", "feedback", "rückmeldung",
            "beschwerde", "beschwerden", "reklamation",
            "antwort", "antworten", "beantwortet", "unbeantwortet",
            "reaktion", "reaktionszeit", "wartezeit",
            "hotline", "telefon", "anruf", "erreichbarkeit",
            "email", "e-mail", "mail", "nachricht", "nachrichten",
        },
    },
    "Datenschutz & Sicherheit": {
        "icon": "🔒",
        "words": {
            "datenschutz", "datenschutzerklärung", "datenschutzrichtlinie",
            "datenschutzbeauftragter", "dsb", "dsgvo", "gdpr", "gdpr",
            "sicherheit", "sicherheitslücke", "sicherheitsupdate",
            "verschlüsselung", "verschlusselung", "encrypt", "encryption",
            "ssl", "tls", "zertifikat", "zertifikate",
            "passwort", "passwörter", "password", "passwortschutz",
            "zugang", "zugänge", "zugangsschutz", "zugriffsrechte",
            "zugriff", "zugriffsbeschränkung", "zugriffskontrolle",
            "vertraulich", "vertraulichkeit", "vertrauliche",
            "anonymisierung", "anonymisieren", "pseudonymisierung",
            "einwilligung", "zustimmung", "consent", "opt-in", "opt-out",
            "loeschung", "löschung", "löschen", "löschrecht",
            "speicherung", "speichern", "speicherfrist", "aufbewahrung",
            "audit", "prüfung", "prüfung", "compliance", "richtlinien",
            "sicher", "unsicher", "schutz", "bedrohung", "cyber",
        },
    },
    "Funktionen & Features": {
        "icon": "⚙️",
        "words": {
            "funktion", "funktionen", "feature", "features", "eigenschaft",
            "eigenschaften", "möglichkeit", "möglichkeiten", "option",
            "optionen", "werkzeug", "werkzeuge", "tool", "tools",
            "dashboard", "übersicht", "uebersicht", "kontrollzentrum",
            "bericht", "berichte", "report", "reports", "reporting",
            "analyse", "analysen", "analytics", "statistik", "statistiken",
            "benachrichtigung", "benachrichtigungen", "notification",
            "notifications", "alert", "alerts", "hinweis",
            "automatisierung", "automatik", "automation",
            "vorlage", "vorlagen", "template", "templates",
            "anpassung", "anpassen", "customizing", "customization",
            "export", "import", "download", "upload",
            "suche", "suchen", "filter", "filtern", "sorting", "sortierung",
            "sprache", "sprachen", "language", "languages", "mehrsprachig",
            "dark", "light", "mode", "design", "layout",
            "ersatz", "alternative", "auswahl", "auswahlen",
        },
    },
    "Kündigung & Vertragsende": {
        "icon": "🚪",
        "words": {
            "kundigung", "kündigung", "kundigen", "kündigen", "kuendigung",
            "widerruf", "widerrufen", "widerrufsrecht", "widerrufsfrist",
            "abmeldung", "abmelden", "deaktivierung", "deaktivieren",
            "beendigung", "beenden", "beendung", "schluss", "ende",
            "vertragsende", "vertragskündigung", "vertrag",
            "laufzeitende", "ablauf", "ablaufen", "verfall",
            "kuendigungsfrist", "kündigungsfrist", "frist", "fristen",
            "mitteilung", "mitteilungsfrist",
            "austritt", "austreten", "abgang", "verlassen",
            "cancel", "cancellation", "terminate", "termination",
            "abschaltung", "abschalten", "decommission",
            "sonderkündigung", "außerordentlich",
        },
    },
    "Leistung & Qualität": {
        "icon": "⚡",
        "words": {
            "leistung", "leistungen", "leistungsfähigkeit", "performance",
            "geschwindigkeit", "schnell", "langsam", "reaktionszeit",
            "verfügbarkeit", "verfügbarkeitsgarantie", "uptime", "downtime",
            "stabilität", "stabil", "zuverlässig", "zuverlässigkeit",
            "qualität", "quality", "qualitätssicherung", "qs",
            "skalierbarkeit", "skalierung", "skalierbar", "skalieren",
            "leistungssprung", "beschränkung", "limitierung", "limit",
            "limits", "begrenzung", "kapazität", "kapazitäten",
            "slower", "slow", "fast", "faster", "speed",
            "optimierung", "optimieren", "verbesserung", "verbessern",
            "latenz", "latency", "durchsatz", "throughput",
            "auslastung", "load", "belastung", "belastbar",
            "wartung", "maintenance", "update", "updates",
        },
    },
    "Daten & Reports": {
        "icon": "📊",
        "words": {
            "daten", "datum", "data", "dataset", "datenbank",
            "datenbanken", "datenbankverwaltung",
            "statistik", "statistiken", "statistisch", "auswertung",
            "auswertungen", "kennzahl", "kennzahlen", "kpi", "kpi's",
            "metrik", "metriken", "metric", "metrics",
            "report", "reports", "reporting", "berichterstattung",
            "visualisierung", "diagramm", "diagramme", "chart", "charts",
            "grafik", "grafiken", "grafische", "graph", "graphs",
            "tabelle", "tabellen", "table", "tables",
            "csv", "excel", "pdf", "export", "import", "download",
            "analyse", "analysen", "analyseergebnis", "analytics",
            "dashboard", "dashboards", "übersicht", "overview",
            "historie", "historisch", "verlauf", "log", "logs",
            "speicherung", "speicher", "archivierung", "archiv",
        },
    },
    "Benutzer & Zugänge": {
        "icon": "👤",
        "words": {
            "benutzer", "benutzerkonto", "user", "users", "nutzer", "nutzerkonto",
            "benutzername", "username", "login", "log-in", "signin", "sign-in",
            "anmelden", "anmeldung", "registrieren", "registrierung",
            "abmelden", "abmeldung", "logout", "signout", "sign-out",
            "passwort", "password", "passwortvergessen", "passwortzurucksetzen",
            "zurücksetzen", "reset", "recovery", "wiederherstellung",
            "profil", "profile", "kontostammdaten", "kontoeinstellungen",
            "rolle", "rollen", "role", "roles", "berechtigung", "berechtigungen",
            "permission", "permissions", "zugriff", "zugriffsrechte",
            "admin", "administrator", "adminbereich", "verwaltung",
            "team", "teams", "teammitglied", "mitarbeiter", "kollege",
            "einladung", "einladen", "invite", "invitation",
            "benutzerdefiniert", "custom", "personalisierung",
            "konto", "kontoverwaltung", "account", "accounts",
        },
    },
    "Kommunikation & Kontakt": {
        "icon": "📧",
        "words": {
            "kontakt", "kontaktformular", "kontaktaufnahme",
            "email", "e-mail", "mail", "mailbox", "postfach",
            "telefon", "telefonisch", "anruf", "rueckruf",
            "rückruf", "hotline", "kundenhotline", "servicenummer",
            "nachricht", "nachrichten", "message", "messages",
            "chat", "chatten", "messenger", "whatsapp", "telegram",
            "brief", "briefwechsel", "korrespondenz",
            "antwort", "antwortzeit", "rückmeldung", "feedback",
            "termin", "termine", "kalender", "buchung", "meeting",
            "sprechstunde", "sprechzeiten", "öffnungszeiten",
            "adresse", "standort", "filiale", "büro",
            "kundenbetreuung", "kundenservice", "kundenberatung",
        },
    },
    "Training & Weiterbildung": {
        "icon": "📚",
        "words": {
            "training", "trainings", "schulung", "schulungen", "kurs", "kurse",
            "weiterbildung", "weiterbildungsangebote", "lernen", "learning",
            "lernen", "lernprozess", "e-learning", "elearning",
            "webinar", "webinare", "seminar", "seminare", "workshop",
            "workshops", "vortrag", "vorträge", "präsentation",
            "präsentationen", "video", "videos", "tutorial", "tutorials",
            "zertifizierung", "zertifikat", "zertifikate", "certificate",
            "wissen", "wissenstransfer", "know-how", "kompetenz",
            "onboarding", "handbuch", "dokumentation", "docs",
            "best-practice", "bestandspraxis", "leitfaden",
            "coaching", "mentoring", "mentoringprogramm",
        },
    },
    "Rechtliches & Compliance": {
        "icon": "⚖️",
        "words": {
            "recht", "rechte", "rechtlich", "rechtliche", "gesetzeslage",
            "agb", "agbs", "allgemeine", "geschäftsbedingungen", "bedingungen",
            "nutzungsbedingungen", "nutzungsrecht", "lizenz", "lizenzen",
            "lizenzierung", "lizenzmodell", "lizenzvereinbarung",
            "haftung", "haftungsausschluss", "haftungsbegrenzung",
            "impressum", "rechtshinweis", "urheberrecht", "copyright",
            "markenrecht", "markenzeichen", "trademark", "patent",
            "vertragsrecht", "vertragsbedingung", "vertragsparteien",
            "widerrufsrecht", "widerrufsbelehrung", "widerrufsfrist",
            "gesetz", "gesetzesänderung", "regulierung", "regulatorisch",
            "compliance", "konformität", "audit", "prüfung",
            "dsgvo", "gdpr", "datenschutzgrundverordnung",
            "schadensersatz", "anspruch", "ansprüche",
            "clause", "terms", "legal",
        },
    },
    "Golf & Sport": {
        "icon": "⛳",
        "words": {
            "golf", "golfen", "golfsport", "golfplatz", "golfplätze",
            "golfkurs", "golfkurse", "golftraining", "golfstunde",
            "abschlag", "abschläge", "tee", "teebox", "tees", "tees",
            "loch", "löcher", "hole", "holes", "runde", "runden", "round",
            "handicap", "handikap", "abschlag", "drive", "driver",
            "eisen", "eisern", "putt", "putter", "putten",
            "schläger", "schläger", "ball", "bälle", "golfball",
            "fairway", "rough", "bunker", "sand", "hindernis",
            "green", "grün", "caddie", "caddy", "cart", "golfcart",
            "club", "clubs", "golfclub", "mitgliedschaft", "clubhaus",
            "drivingrange", "range", "übung", "praxis",
            "turnier", "tournament", "wettbewerb", "meisterschaft",
            "profi", "professional", "pgaprofi", "trainer", "coach",
            "swing", "schwung", "swing", "technik", "schlag",
            "bahn", "bahnen", "pars", "par", "birdie", "bogey",
            "score", "scorecard", "ergebniskarte", "spielvorgabe",
            "camp", "camps", "trainingscamp", "trainingslager",
            "sport", "sportlich", "sportart", "fitness",
        },
    },
    "Produkt & Angebot": {
        "icon": "🛍️",
        "words": {
            "produkt", "produkte", "product", "products",
            "angebot", "angebote", "offer", "offers",
            "service", "dienstleistung", "dienstleistungen", "dienst",
            "lösung", "lösungen", "solution", "solutions",
            "plattform", "platform", "software", "app", "application",
            "tool", "tools", "werkzeug", "werkzeuge",
            "version", "versionen", "release", "update", "updates",
            "upgrade", "upgrades", "neu", "neuerung", "neuerungen",
            "release-notes", "changelog", "änderungen",
            "pakete", "package", "packages", "bundle", "bündel",
            "vergleich", "vergleichen", "comparison", "compare",
            "beschreibung", "specification", "spezifikation",
            "bestellung", "bestellen", "order", "ordering",
            "lieferung", "lieferzeit", "versand", "shipping",
            "katalog", "kataloge", "portfolio", "sortiment",
        },
    },
    "Team & Unternehmen": {
        "icon": "🏢",
        "words": {
            "team", "teams", "abteilung", "abteilungen", "department",
            "unternehmen", "firma", "gesellschaft", "company",
            "organisation", "organization", "organisatorisch",
            "mitarbeiter", "mitarbeitende", "kollege", "kollegen",
            "chef", "leitung", "management", "manager",
            "personalschaft", "personalabteilung", "hr",
            "büro", "büroräume", "standort", "standorte",
            "partner", "partnerprogramm", "kooperation",
            "zusammenarbeit", "collaboration", "netzwerk",
            "karriere", "jobs", "stellenangebot", "stellenanzeige",
            "bewerbung", "stellenanzeige", "recruiting",
            "kultur", "werte", "mission", "vision",
        },
    },
    "Zahlungs & Abrechnung": {
        "icon": "💳",
        "words": {
            "zahlung", "zahlungen", "payment", "payments",
            "rechnung", "rechnungen", "invoice", "invoices",
            "bezahlmethode", "zahlungsmethode", "zahlungsmöglichkeiten",
            "kreditkarte", "karte", "card", "visa", "mastercard",
            "lastschrift", "sepa", "überweisung", "banküberweisung",
            "paypal", "apple-pay", "google-pay",
            "steuer", "steuern", "steueridentifikationsnummer", "ust",
            "mehrwertsteuer", "mwst", "steuerbeleg", "steuernummer",
            "buchhaltung", "finance", "finanzen", "finanziell",
            "saldo", "saldoausgleich", "offen", "rückstand",
            "fälligkeit", "fällig", "mahnung", "mahnungen",
        },
    },
}

_STOPWORDS = frozenset({
    "aber", "als", "am", "an", "auch", "auf", "aus", "bei", "bin", "bis", "bist",
    "da", "damit", "dann", "das", "dass", "dein", "deine", "dem", "den", "der",
    "des", "die", "dies", "diese", "diesem", "diesen", "dieser", "dieses",
    "doch", "du", "durch", "ein", "eine", "einem", "einen", "einer", "eines",
    "er", "es", "euch", "euer", "eure", "für", "hab", "habe", "haben", "hat",
    "hier", "hin", "ich", "ihr", "ihre", "im", "in", "ist", "ja", "jede",
    "jedem", "jeden", "jeder", "jedes", "jene", "kann", "kannst", "können",
    "konnte", "machen", "man", "mein", "meine", "mit", "muss", "musste",
    "nach", "nicht", "nichts", "noch", "nun", "nur", "ob", "oder", "ohne",
    "sehr", "sein", "seine", "selbst", "sich", "sie", "sind", "so", "solche",
    "sollen", "sollte", "sondern", "über", "um", "und", "uns", "unser",
    "unserer", "unter", "vom", "von", "vor", "war", "waren", "was", "weg",
    "weil", "welche", "welchem", "welchen", "welcher", "wenn", "wer", "werde",
    "werden", "wie", "wieder", "will", "wir", "wird", "wirst", "wo", "woher",
    "wohin", "zu", "zum", "zur", "zurück", "zusammen", "weitere", "ihrem",
    "wollen", "wurde", "wurden", "einem", "ihren", "dankedanke", "bitte",
    "schon", "gerne", "genau", "richtig", "gut", "gern", "vielleicht",
    "easy", "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "this", "that", "with",
})


_TOPIC_TOKEN_RE = re.compile(r"[a-zA-Z0-9äöüÄÖÜß]{3,}")
_TOPIC_CLEAN_RE = re.compile(r"[^a-z0-9]")
_TOPIC_UMLAUT_MAP = str.maketrans({
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
})
_TOPIC_STEM_SUFFIXES = ("innen", "ungen", "ung", "en", "er", "es", "e", "n")


def _normalize_topic_word(value: str) -> str:
    text = (value or "").strip().lower().translate(_TOPIC_UMLAUT_MAP)
    return _TOPIC_CLEAN_RE.sub("", text)


def _token_variants(token: str) -> List[str]:
    variants = [token]
    for suffix in _TOPIC_STEM_SUFFIXES:
        if not token.endswith(suffix):
            continue
        stem = token[: -len(suffix)]
        if len(stem) >= 4 and stem not in variants:
            variants.append(stem)
    return variants


_STOPWORDS_NORMALIZED = frozenset(
    w for w in (_normalize_topic_word(sw) for sw in _STOPWORDS) if w
)

_TOPIC_LOOKUP: Dict[str, List[str]] = {}
for _topic_name, _topic_data in TOPIC_CATEGORIES.items():
    for _raw_word in _topic_data.get("words", set()):
        _normalized_word = _normalize_topic_word(_raw_word)
        if len(_normalized_word) < 3:
            continue
        _TOPIC_LOOKUP.setdefault(_normalized_word, [])
        if _topic_name not in _TOPIC_LOOKUP[_normalized_word]:
            _TOPIC_LOOKUP[_normalized_word].append(_topic_name)


def _topics_for_normalized_token(normalized_token: str) -> List[str]:
    if len(normalized_token) < 3:
        return []
    matched_topics: List[str] = []
    for variant in _token_variants(normalized_token):
        topic_hits = _TOPIC_LOOKUP.get(variant, [])
        if len(topic_hits) != 1:
            continue
        topic_name = topic_hits[0]
        if topic_name not in matched_topics:
            matched_topics.append(topic_name)
    return matched_topics


def _topics_for_question(question: str) -> set[str]:
    matched_topics: set[str] = set()
    for token in _TOPIC_TOKEN_RE.findall(question.lower()):
        normalized_token = _normalize_topic_word(token)
        if len(normalized_token) < 4 or normalized_token in _STOPWORDS_NORMALIZED:
            continue
        for topic_name in _topics_for_normalized_token(normalized_token):
            matched_topics.add(topic_name)
    return matched_topics


def _classify_token(token: str) -> Optional[str]:
    normalized_token = _normalize_topic_word(token)
    topics = _topics_for_normalized_token(normalized_token)
    return topics[0] if topics else None


def _topic_question_counts(rows: List[Dict[str, Any]], topic_name: str) -> Counter[str]:
    question_counter: Counter[str] = Counter()
    if topic_name not in TOPIC_CATEGORIES:
        return question_counter
    for row in rows:
        question = (row.get("question") or "").strip()
        if not question:
            continue
        if topic_name in _topics_for_question(question):
            question_counter[question] += 1
    return question_counter


def _aggregate_chat_events(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregiert einfache Chat-Statistiken aus den zuletzt geladenen Events."""
    now = datetime.utcnow()
    week_cutoff = now - timedelta(days=7)
    month_cutoff = now - timedelta(days=30)
    quarter_cutoff = now - timedelta(days=90)

    def _day_label(dt: datetime) -> str:
        return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][dt.weekday()]

    week_users: set[str] = set()
    month_users: set[str] = set()
    quarter_users: set[str] = set()

    overview = {
        "week": {"chats": 0, "users": 0},
        "month": {"chats": 0, "users": 0},
        "quarter": {"chats": 0, "users": 0},
    }

    daily_counter = defaultdict(int)
    question_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    topic_questions: Dict[str, Counter[str]] = defaultdict(Counter)
    answered = escalated = failed = 0

    for row in rows:
        ts = _parse_iso_ts(str(row.get("created_at", "")))
        if not ts:
            continue
        uid = (row.get("user_id") or "").strip()
        question = (row.get("question") or "").strip()
        status = (row.get("status") or "").strip().lower()

        if ts >= week_cutoff:
            overview["week"]["chats"] += 1
            if uid:
                week_users.add(uid)
        if ts >= month_cutoff:
            overview["month"]["chats"] += 1
            if uid:
                month_users.add(uid)
        if ts >= quarter_cutoff:
            overview["quarter"]["chats"] += 1
            if uid:
                quarter_users.add(uid)

        if ts >= week_cutoff:
            daily_counter[_day_label(ts)] += 1

        if question:
            question_counter[question] += 1
            seen_topics = _topics_for_question(question)
            for topic_name in seen_topics:
                topic_counter[topic_name] += 1
            for tn in seen_topics:
                topic_questions[tn][question] += 1

        if status in {"ok", "answered", "success"}:
            answered += 1
        elif status in {"escalated"}:
            escalated += 1
        else:
            failed += 1

    overview["week"]["users"] = len(week_users)
    overview["month"]["users"] = len(month_users)
    overview["quarter"]["users"] = len(quarter_users)

    last_7_days = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        label = _day_label(day)
        last_7_days.append({"day": label, "count": daily_counter.get(label, 0)})

    top_questions = [
        {"question": q, "count": c}
        for q, c in question_counter.most_common(6)
    ]

    trending_topics = []
    for t, c in topic_counter.most_common(8):
        topic_q_counts = topic_questions.get(t, Counter())
        topic_q_sorted = topic_q_counts.most_common(15)
        trending_topics.append({
            "topic": t,
            "icon": TOPIC_CATEGORIES[t]["icon"],
            "delta": f"{c} Erwähnungen",
            "questions": [{"q": q, "count": cnt} for q, cnt in topic_q_sorted],
        })

    total = answered + escalated + failed
    completion = {
        "answered": answered / total if total else 0,
        "escalated": escalated / total if total else 0,
        "failed": failed / total if total else 0,
    }

    return {
        "overview": overview,
        "topQuestions": top_questions,
        "trendingTopics": trending_topics,
        "daily": last_7_days,
        "completion": completion,
    }


def _bot_run_lock(ctx: BotContext) -> threading.Lock:
    if ctx.bot_id not in _run_locks:
        _run_locks[ctx.bot_id] = threading.Lock()
    return _run_locks[ctx.bot_id]


def _clear_faiss_dir(faiss_dir: Path) -> None:
    """Entfernt bestehenden FAISS-Inhalt für einen echten Voll-Reindex."""
    if not faiss_dir.exists():
        return
    for entry in faiss_dir.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except Exception as exc:
            print(f"Konnte FAISS-Entry nicht löschen ({entry}):", exc)


def _collect_reindex_scope(ctx: BotContext) -> Dict[str, Any]:
    """Sammelt, welche Markdown-Quellen beim Reindex berücksichtigt werden."""
    all_docs = [p for p in ctx.docs_dir.rglob("*.md") if p.is_file()]
    upload_docs = [p for p in ctx.upload_dir.rglob("*.md") if p.is_file()] if ctx.upload_dir.exists() else []
    faq_exists = ctx.faq_path.exists() and ctx.faq_path.is_file()
    return {
        "docs_dir": str(ctx.docs_dir),
        "total_markdown_files": len(all_docs),
        "upload_markdown_files": len(upload_docs),
        "faq_included": bool(faq_exists),
    }


def _run_scrape_and_index(ctx: BotContext, sitemap_url: str, cleanup_stale: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "sitemap_url": sitemap_url,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "status": "running",
        "mode": "scrape_and_index",
        "bot_slug": ctx.bot_slug,
        "cleanup_stale": bool(cleanup_stale),
    }

    if not os.environ.get("OPENAI_API_KEY"):
        result["status"] = "error"
        result["error"] = "OPENAI_API_KEY fehlt für Embeddings/LLM."
        result["logs"] = []
        result["finished_at"] = datetime.utcnow().isoformat() + "Z"
        return result

    _ensure_bot_dirs(ctx)
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            scraper = sitemap_scraper.SitemapScraper(
                max_workers=1,
                verbose=True,
                output_dir=ctx.docs_dir,
                summary_path=ctx.summary_path,
            )
            urls = scraper.fetch_sitemap_urls(sitemap_url)
            if not urls:
                raise RuntimeError(
                    "In der Sitemap wurden keine passenden URLs gefunden. "
                    "Hinweis: Englische URLs (/en/...) werden automatisch übersprungen."
                )
            scraper.process_urls(urls, parallel=False, cleanup_stale=cleanup_stale)
            build_index.build_index(ctx.docs_dir, ctx.faiss_dir)

        result["status"] = "ok"
        result["summary"] = _load_summary(ctx)

        try:
            _invalidate_bot_cache(ctx)
            get_qa(ctx)
        except Exception as reload_err:
            result["status"] = "warn"
            result["error"] = f"Index erstellt, Reload fehlgeschlagen: {reload_err}"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc().splitlines()
    finally:
        result["logs"] = buffer.getvalue().splitlines()
        result["finished_at"] = datetime.utcnow().isoformat() + "Z"

    return result


def _run_index_only(ctx: BotContext) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "sitemap_url": None,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "status": "running",
        "mode": "index_only",
        "bot_slug": ctx.bot_slug,
        "full_rebuild": True,
    }

    if not os.environ.get("OPENAI_API_KEY"):
        result["status"] = "error"
        result["error"] = "OPENAI_API_KEY fehlt für Embeddings/LLM."
        result["logs"] = []
        result["finished_at"] = datetime.utcnow().isoformat() + "Z"
        return result

    _ensure_bot_dirs(ctx)
    buffer = io.StringIO()
    try:
        scope = _collect_reindex_scope(ctx)
        result["reindex_scope"] = scope
        if scope["total_markdown_files"] <= 0:
            raise RuntimeError(
                f"Keine Markdown-Dateien für Reindex gefunden unter: {ctx.docs_dir}"
            )

        with redirect_stdout(buffer):
            print("[INFO] Vollständiger Reindex gestartet (ohne Scraping).")
            print(f"[INFO] Quellen: {scope['total_markdown_files']} Markdown-Dateien "
                  f"(Uploads: {scope['upload_markdown_files']}, FAQ: {scope['faq_included']})")
            _clear_faiss_dir(ctx.faiss_dir)
            build_index.build_index(ctx.docs_dir, ctx.faiss_dir)
            reindex_summary = _build_reindex_summary(ctx)
            _write_summary(ctx, reindex_summary)
            print(f"[INFO] summary.json für Reindex aktualisiert: {ctx.summary_path}")

        result["status"] = "ok"
        result["summary"] = _load_summary(ctx)
        result["info"] = (
            "Vollständiger Reindex abgeschlossen. "
            "Es wurden alle Markdown-Quellen (inkl. uploads/faqs) neu indexiert."
        )

        try:
            _invalidate_bot_cache(ctx)
            get_qa(ctx)
        except Exception as reload_err:
            result["status"] = "warn"
            result["error"] = f"Index erstellt, Reload fehlgeschlagen: {reload_err}"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc().splitlines()
    finally:
        result["logs"] = buffer.getvalue().splitlines()
        result["finished_at"] = datetime.utcnow().isoformat() + "Z"

    return result


app = Flask(__name__)


@app.route("/")
def index():
    return redirect("/login/")


@app.route("/login", strict_slashes=False)
def login_page():
    return render_template("dashboard.html")


@app.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/pdf-upload")
def pdf_upload_page():
    return render_template("pdf-upload.html")


@app.route("/faqs")
def faqs_page():
    return render_template("faqs.html")


@app.route("/scraping")
def scraping_page():
    return render_template("scraping.html")


@app.route("/chat")
def chat_page():
    return render_template("chat.html")


@app.route("/memory")
def memory_page():
    return render_template("memory.html")


@app.route("/widget-design")
def widget_design_page():
    return render_template("widget-design.html")


@app.route("/stats")
def stats_page():
    return render_template("stats.html")


@app.route("/usage")
def usage_page():
    return render_template("usage.html")


@app.route("/integrate")
def integrate_page():
    return render_template("integrate.html")


@app.route("/embed")
def embed():
    return render_template("embed.html")


@app.route("/api/public/widget_config", methods=["GET"])
def public_widget_config():
    """Begruessungs-Popup + Schnellaktionen fuer das Widget.

    Wird sowohl aus dem Embed (gleiche Domain) als auch vom Snippet auf der
    Kundenseite (andere Domain) gelesen -> CORS erlaubt, nur Lesezugriff.
    """
    bot_slug = _extract_bot_slug(None)
    customer_id = _extract_customer_id(None)
    lang = _extract_chat_lang(None)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            try:
                ctx = _build_bot_context(bot_slug)
            except PermissionError:
                ctx = None
        if ctx is None:
            ctx = _build_public_bot_context(bot_slug, customer_id=customer_id)

        payload = _resolve_widget_config(ctx, lang)
        response = jsonify(payload)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=60"
        return response
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as exc:
        return jsonify({"error": f"Widget-Konfiguration konnte nicht geladen werden: {exc}"}), 500


@app.route("/api/admin/widget_config", methods=["GET", "POST"])
def admin_widget_config():
    """Widget-Inhalte (Popup-Text, Themen) lesen und speichern."""
    payload = {}
    if request.method == "POST":
        payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        _ensure_bot_dirs(ctx)
        if request.method == "GET":
            return jsonify(
                {
                    "config": _read_widget_config(ctx),
                    "defaults": {
                        "greeting_text": DEFAULT_GREETING_TEXT,
                        "topics_label": DEFAULT_TOPICS_LABEL,
                    },
                }
            )

        config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        saved = _write_widget_config(ctx, config)
        return jsonify({"status": "ok", "config": saved})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Widget-Konfiguration konnte nicht gespeichert werden: {exc}"}), 500


@app.route("/api/public/bot_lookup", methods=["GET"])
def public_bot_lookup():
    bot_slug = _extract_bot_slug(None)
    customer_id = _extract_customer_id(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        query = (
            _get_supabase_client()
            .table("chatbots")
            .select("id, customer_id, slug")
            .eq("slug", bot_slug)
        )
        if customer_id:
            query = query.eq("customer_id", customer_id)
        resp = query.limit(2).execute()
        data = resp.data if hasattr(resp, "data") else None
        if not data:
            return jsonify({"error": "Chatbot nicht gefunden."}), 404
        if len(data) > 1:
            return jsonify(
                {
                    "error": "Mehrere Chatbots mit diesem slug gefunden. Bitte slug eindeutig machen oder customer_id uebergeben.",
                    "count": len(data),
                }
            ), 409
        bot = data[0]
        return jsonify(
            {
                "bot_id": bot.get("id"),
                "customer_id": bot.get("customer_id"),
                "slug": bot.get("slug"),
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Bot konnte nicht geladen werden: {exc}"}), 500


@app.route("/api/download-wp-plugin", methods=["GET", "POST"])
def download_wp_plugin():
    """Generate and download a WordPress plugin ZIP with the user's chat widget configuration."""
    # Get all query parameters for the embed URL
    def _parse_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            cleaned = value.strip().lower()
            if cleaned in ("1", "true", "yes", "on"):
                return True
            if cleaned in ("0", "false", "no", "off"):
                return False
        return default

    greeting_payload: Any = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        embed_url = (payload.get("embed_url") or "").strip()
        icon = payload.get("icon") or "💬"
        launcher_bg = (payload.get("launcher_bg") or "").strip()
        show_hero_toggle = _parse_bool(payload.get("show_hero_toggle"), False)
        greeting_payload = payload.get("greeting")
    else:
        embed_url = (request.args.get("embed_url", "") or "").strip()
        icon = request.args.get("icon", "💬")
        launcher_bg = (request.args.get("launcher_bg", "") or "").strip()
        show_hero_toggle = _parse_bool(request.args.get("show_hero_toggle"), False)

    if not launcher_bg:
        launcher_bg = "#8c8875"

    if not embed_url:
        return jsonify({"error": "embed_url parameter required"}), 400

    def _php_single_quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    embed_url_safe = _php_single_quote(embed_url)
    icon_safe = _php_single_quote(str(icon))
    launcher_bg_safe = _php_single_quote(launcher_bg)
    show_hero_toggle_js = "true" if show_hero_toggle else "false"

    # Begruessungs-Popup: Default aus der Bot-Konfiguration mitgeben.
    greeting_cfg = _normalize_widget_config({"greeting": greeting_payload or {}}).get("greeting") or {}
    greeting_js = json.dumps(
        {
            "enabled": bool(greeting_cfg.get("enabled", True)),
            "text": greeting_cfg.get("text") or {},
            "delay_ms": int(greeting_cfg.get("delay_ms", 1200) or 0),
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")

    # WordPress plugin PHP content
    plugin_php = f'''<?php
/**
 * Plugin Name: AI Chat Widget
 * Description: Ein AI-Chat-Widget für deine WordPress-Website.
 * Version: 1.0.4
 * Author: Scraping Admin
 * License: GPL v2 or later
 */

if (!defined('ABSPATH')) {{
    exit;
}}

// Add the chat widget to the footer
function ai_chat_widget_footer() {{
    $embed_url = esc_url('{embed_url_safe}');
    $icon = '{icon_safe}';
    $icon_setting = get_option('ai_chat_widget_icon', '');
    if (!empty($icon_setting)) {{
        $icon = $icon_setting;
    }}
    $button_bg = '{launcher_bg_safe}';
    if (empty($button_bg)) {{
        $button_bg = '#8c8875';
    }}
    $button_bg_setting = get_option('ai_chat_widget_button_bg', '');
    if (!empty($button_bg_setting)) {{
        $button_bg = $button_bg_setting;
    }}
    $icon_trim = trim($icon);
    $icon_is_svg = stripos($icon_trim, '<svg') === 0;
    $icon_allowed = array(
        'svg' => array('xmlns' => true, 'viewBox' => true, 'width' => true, 'height' => true, 'fill' => true),
        'g' => array('fill' => true, 'stroke' => true, 'stroke-width' => true, 'stroke-linecap' => true, 'stroke-linejoin' => true, 'transform' => true, 'filter' => true),
        'path' => array('d' => true, 'fill' => true, 'stroke' => true, 'stroke-width' => true, 'stroke-linecap' => true, 'stroke-linejoin' => true),
        'rect' => array('x' => true, 'y' => true, 'width' => true, 'height' => true, 'rx' => true, 'ry' => true, 'fill' => true, 'stroke' => true),
        'circle' => array('cx' => true, 'cy' => true, 'r' => true, 'fill' => true, 'stroke' => true),
        'ellipse' => array('cx' => true, 'cy' => true, 'rx' => true, 'ry' => true, 'fill' => true, 'stroke' => true),
        'line' => array('x1' => true, 'y1' => true, 'x2' => true, 'y2' => true, 'stroke' => true, 'stroke-width' => true),
        'polyline' => array('points' => true, 'fill' => true, 'stroke' => true, 'stroke-width' => true),
        'polygon' => array('points' => true, 'fill' => true, 'stroke' => true, 'stroke-width' => true),
        'defs' => array(),
        'filter' => array('id' => true, 'x' => true, 'y' => true, 'width' => true, 'height' => true, 'filterUnits' => true, 'color-interpolation-filters' => true),
        'feFlood' => array('flood-opacity' => true, 'result' => true),
        'feColorMatrix' => array('type' => true, 'values' => true, 'in' => true, 'result' => true),
        'feOffset' => array('dy' => true, 'dx' => true),
        'feGaussianBlur' => array('stdDeviation' => true),
        'feComposite' => array('in2' => true, 'operator' => true),
        'feBlend' => array('mode' => true, 'in' => true, 'in2' => true, 'result' => true),
    );
    // Trust admin setting for SVG to avoid stripping ViewBox etc.
    $icon_html = $icon_is_svg ? $icon_trim : esc_html($icon_trim);
    ?>
    <style>
        #ai-chat-widget {{
            opacity: 0;
            visibility: hidden;
            pointer-events: none;
            transform: translateY(8px);
            transition: opacity 0.25s ease, transform 0.25s ease, visibility 0.25s ease;
        }}
        #ai-chat-widget.ai-chat-visible {{
            opacity: 1;
            visibility: visible;
            pointer-events: auto;
            transform: translateY(0);
        }}
        #ai-chat-toggle {{ display: flex; align-items: center; justify-content: center; }}
        #ai-chat-toggle svg {{ width: 24px; height: 24px; flex-shrink: 0; display: block; }}
        .ai-chat-hero-box {{
            display: inline-flex;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(15, 23, 42, 0.12);
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
            margin-top: 16px;
        }}
        .ai-chat-hero-toggle {{
            border: none;
            background: #0f766e;
            color: #fff;
            padding: 8px 14px;
            border-radius: 999px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
        }}
        /* Begruessungs-Popup ueber dem Chat-Button */
        #ai-chat-teaser {{
            position: absolute;
            bottom: 80px;
            right: 0;
            display: none;
            align-items: flex-start;
            gap: 12px;
            width: max-content;
            max-width: 300px;
            padding: 14px 16px;
            background: #ffffff;
            color: #1b1c1e;
            border: 1px solid rgba(27, 28, 30, 0.10);
            border-radius: 18px;
            box-shadow: 0 18px 40px rgba(17, 16, 13, 0.16);
            font-size: 15px;
            font-weight: 600;
            line-height: 1.35;
            text-align: left;
            cursor: pointer;
        }}
        #ai-chat-teaser.ai-chat-teaser-visible {{
            display: flex;
        }}
        #ai-chat-teaser-close {{
            flex-shrink: 0;
            margin: -2px -4px 0 0;
            padding: 0 2px;
            border: none;
            background: transparent;
            color: rgba(27, 28, 30, 0.45);
            font-size: 18px;
            line-height: 1.1;
            cursor: pointer;
        }}
        #ai-chat-teaser-close:hover {{
            color: #1b1c1e;
        }}
        @media (max-width: 640px) {{
            #ai-chat-teaser {{
                max-width: calc(100vw - 48px);
            }}
        }}
    </style>
    <div id="ai-chat-widget" style="position: fixed; bottom: 24px; right: 24px; z-index: 9999;">
        <div id="ai-chat-teaser" role="button" tabindex="0" aria-label="Chat oeffnen">
            <span id="ai-chat-teaser-text"></span>
            <button type="button" id="ai-chat-teaser-close" aria-label="Hinweis schliessen">&times;</button>
        </div>
        <button id="ai-chat-toggle" onclick="toggleAIChat()" style="
            width: 64px; height: 64px; border-radius: 50%; border: none;
            background: <?php echo esc_attr($button_bg); ?>;
            color: white; font-size: 24px; cursor: pointer;
            transition: transform 0.2s ease;
            display: flex; align-items: center; justify-content: center;
        " onmouseover="this.style.transform='scale(1.1)'; "
           onmouseout="this.style.transform='scale(1)'; ">
            <?php echo $icon_html; ?>
        </button>
        <div id="ai-chat-panel" style="
            display: none; position: absolute; bottom: 80px; right: 0;
            width: 400px; max-width: calc(100vw - 48px); height: 550px;
            border-radius: 20px; overflow: hidden;
            box-shadow: 0 24px 60px rgba(17, 16, 13, 0.20);
            border: none;
            background: #fff;
        ">
            <iframe id="ai-chat-embed" src="about:blank" data-src="<?php echo $embed_url; ?>"
                width="100%" height="100%" style="border: none;"
                sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox"
                title="AI Chat Widget"></iframe>
        </div>
    </div>
    <script>
    if (typeof window.AIChatWidgetConfig === 'undefined') {{
        window.AIChatWidgetConfig = {{}};
    }}
    window.AIChatWidgetConfig.defaultShowHeroToggle = {show_hero_toggle_js};
    if (typeof window.AIChatWidgetConfig.showHeroToggle === 'undefined') {{
        window.AIChatWidgetConfig.showHeroToggle = window.AIChatWidgetConfig.defaultShowHeroToggle;
    }}

    function resolveAiChatLang() {{
        var lang = (document.documentElement.getAttribute('lang') || navigator.language || '').toLowerCase();
        return lang ? lang.split('-')[0] : '';
    }}

    function withLangParam(url) {{
        if (!url) return url;
        var lang = resolveAiChatLang();
        if (!lang) return url;
        try {{
            var parsed = new URL(url, window.location.origin);
            if (!parsed.searchParams.has('lang')) parsed.searchParams.set('lang', lang);
            return parsed.toString();
        }} catch (e) {{
            return url;
        }}
    }}

    function setAiChatEmbedSrc() {{
        var iframe = document.getElementById('ai-chat-embed');
        if (!iframe) return;
        var baseUrl = iframe.getAttribute('data-src') || iframe.src || '';
        var nextUrl = withLangParam(baseUrl);
        if (nextUrl) iframe.src = nextUrl;
    }}

    setAiChatEmbedSrc();

    function openAIChatPanel() {{
        var panel = document.getElementById('ai-chat-panel');
        if (panel) panel.style.display = 'block';
        hideAIChatTeaser(true);
    }}

    function closeAIChatPanel() {{
        var panel = document.getElementById('ai-chat-panel');
        if (panel) panel.style.display = 'none';
    }}

    /* --- Begruessungs-Popup: einmal pro Besucher ---------------------- */
    var AI_CHAT_GREETING = {greeting_js};

    function aiChatEmbedUrl() {{
        var iframe = document.getElementById('ai-chat-embed');
        if (!iframe) return '';
        return iframe.getAttribute('data-src') || iframe.src || '';
    }}

    function aiChatBotSlug() {{
        try {{
            var src = aiChatEmbedUrl();
            if (!src) return '';
            return new URL(src, window.location.origin).searchParams.get('bot_slug') || '';
        }} catch (e) {{
            return '';
        }}
    }}

    function aiChatTeaserKey() {{
        return 'aiChatTeaserSeen:' + (aiChatBotSlug() || 'default');
    }}

    function aiChatWidgetConfigUrl() {{
        try {{
            var src = aiChatEmbedUrl();
            if (!src) return '';
            var parsed = new URL(src, window.location.origin);
            var slug = parsed.searchParams.get('bot_slug');
            if (!slug) return '';
            var query = 'bot_slug=' + encodeURIComponent(slug);
            var customer = parsed.searchParams.get('customer_id');
            if (customer) query += '&customer_id=' + encodeURIComponent(customer);
            var lang = resolveAiChatLang();
            if (lang) query += '&lang=' + encodeURIComponent(lang);
            return parsed.origin + '/api/public/widget_config?' + query;
        }} catch (e) {{
            return '';
        }}
    }}

    function hideAIChatTeaser(remember) {{
        var teaser = document.getElementById('ai-chat-teaser');
        if (teaser) teaser.classList.remove('ai-chat-teaser-visible');
        if (remember) {{
            try {{ window.localStorage.setItem(aiChatTeaserKey(), '1'); }} catch (e) {{}}
        }}
    }}

    (function () {{
        var teaser = document.getElementById('ai-chat-teaser');
        var textEl = document.getElementById('ai-chat-teaser-text');
        var closeBtn = document.getElementById('ai-chat-teaser-close');
        if (!teaser || !textEl) return;

        var seen = false;
        try {{ seen = window.localStorage.getItem(aiChatTeaserKey()) === '1'; }} catch (e) {{}}
        if (seen) return;

        if (closeBtn) {{
            closeBtn.addEventListener('click', function (event) {{
                event.stopPropagation();
                hideAIChatTeaser(true);
            }});
        }}
        teaser.addEventListener('click', function () {{
            hideAIChatTeaser(true);
            openAIChatPanel();
        }});
        teaser.addEventListener('keydown', function (event) {{
            if (event.key === 'Enter' || event.key === ' ') {{
                event.preventDefault();
                hideAIChatTeaser(true);
                openAIChatPanel();
            }}
        }});

        var lang = resolveAiChatLang() === 'en' ? 'en' : 'de';
        var injected = AI_CHAT_GREETING || {{}};
        var injectedText = (injected.text && (injected.text[lang] || injected.text.de || injected.text.en)) || '';

        var url = aiChatWidgetConfigUrl();
        var live = (url && window.fetch)
            ? fetch(url).then(function (res) {{ return res.ok ? res.json() : null; }}).catch(function () {{ return null; }})
            : null;
        var settled = live
            ? Promise.race([live, new Promise(function (resolve) {{ window.setTimeout(function () {{ resolve(null); }}, 1500); }})])
            : Promise.resolve(null);

        var delay = typeof injected.delay_ms === 'number' ? injected.delay_ms : 1200;
        window.setTimeout(function () {{
            settled.then(function (cfg) {{
                var greeting = (cfg && cfg.greeting) ? cfg.greeting : injected;
                if (greeting.enabled === false) return;
                var text = (typeof greeting.text === 'string' && greeting.text) ? greeting.text : injectedText;
                if (!text) return;
                var panel = document.getElementById('ai-chat-panel');
                if (panel && panel.style.display === 'block') return;
                textEl.textContent = text;
                teaser.classList.add('ai-chat-teaser-visible');
            }});
        }}, Math.max(0, delay));
    }})();

    function toggleAIChat() {{
        var panel = document.getElementById('ai-chat-panel');
        if (!panel) return;
        if (panel.style.display === 'block') {{
            closeAIChatPanel();
        }} else {{
            openAIChatPanel();
        }}
    }}

    (function () {{
        var widget = document.getElementById('ai-chat-widget');
        if (!widget) return;
        var config = window.AIChatWidgetConfig || {{}};
        var parseBool = function(value, fallback) {{
            if (typeof value === 'boolean') return value;
            if (typeof value === 'number') return value !== 0;
            if (typeof value === 'string') {{
                var normalized = value.trim().toLowerCase();
                if (/^(1|true|yes|on)$/.test(normalized)) return true;
                if (/^(0|false|no|off)$/.test(normalized)) return false;
            }}
            return fallback;
        }};
        var collectHeroSelectors = function(cfg) {{
            var selectors = [];
            if (cfg && typeof cfg.heroSelector === 'string' && cfg.heroSelector.trim()) {{
                selectors.push(cfg.heroSelector.trim());
            }}
            selectors.push('[data-ai-chat-hero]', '.block-home-hero', '.block-home-hero-mobile', '.home-hero');
            return selectors;
        }};
        var isHeroCandidateVisible = function(el) {{
            if (!el) return false;
            var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
            var rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }};
        var resolveHeroElement = function(cfg) {{
            var selectors = collectHeroSelectors(cfg);
            var fallback = null;
            for (var i = 0; i < selectors.length; i++) {{
                var selector = selectors[i];
                if (!selector) continue;
                var matches = document.querySelectorAll(selector);
                for (var j = 0; j < matches.length; j++) {{
                    var candidate = matches[j];
                    if (!fallback) fallback = candidate;
                    if (isHeroCandidateVisible(candidate)) return candidate;
                }}
            }}
            return fallback;
        }};
        var computeHeroVisible = function(targetHero) {{
            if (!targetHero) return false;
            var rect = targetHero.getBoundingClientRect();
            return rect.bottom > 0 && rect.top < window.innerHeight;
        }};
        var isMobileViewport = function() {{
            return window.matchMedia ? window.matchMedia('(max-width: 767px)').matches : window.innerWidth <= 767;
        }};
        var shouldShowImmediatelyAfterHero = function(targetHero) {{
            if (heroToggleEnabled || !targetHero || !isMobileViewport()) return false;
            var rect = targetHero.getBoundingClientRect();
            return window.scrollY > 8 && rect.bottom <= window.innerHeight + 24;
        }};
        var collectBookingBarSelectors = function(cfg) {{
            var selectors = [];
            if (cfg && typeof cfg.bookingBarSelector === 'string' && cfg.bookingBarSelector.trim()) {{
                selectors.push(cfg.bookingBarSelector.trim());
            }}
            selectors.push(
                '[data-booking-bar]',
                '[data-booking-widget]',
                '.booking-bar',
                '.bookingbar',
                '.booking-widget',
                '.booking-form',
                '.block-booking-bar',
                '.block-booking-widget',
                '.reservation-bar',
                '.reservation-widget'
            );
            return selectors;
        }};
        var findVisibleBookingBar = function(cfg) {{
            var selectors = collectBookingBarSelectors(cfg);
            for (var i = 0; i < selectors.length; i++) {{
                var matches = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < matches.length; j++) {{
                    var el = matches[j];
                    var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) continue;
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight) return el;
                }}
            }}
            return null;
        }};
        var isBookingBarCompact = function(el) {{
            if (!el) return false;
            var rect = el.getBoundingClientRect();
            var className = (el.className || '').toString().toLowerCase();
            if (/(compact|collapsed|minimized|scrolled|sticky|is-small|is-compact)/.test(className)) return true;
            var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
            var compactHeight = Math.max(72, Math.min(112, viewportHeight * 0.14));
            return window.scrollY > 32 && rect.height <= compactHeight;
        }};
        var shouldShowForCompactMobileBookingBar = function() {{
            if (heroToggleEnabled || !isMobileViewport()) return false;
            return isBookingBarCompact(findVisibleBookingBar(config));
        }};
        var removeHeroToggle = function() {{
            var existingHeroToggle = document.getElementById('ai-chat-hero-toggle');
            if (!existingHeroToggle) return;
            var existingBox = existingHeroToggle.closest('.ai-chat-hero-box');
            if (existingBox && existingBox.parentNode) {{
                existingBox.parentNode.removeChild(existingBox);
            }} else if (existingHeroToggle.parentNode) {{
                existingHeroToggle.parentNode.removeChild(existingHeroToggle);
            }}
        }};
        var baseHeroToggleEnabled = parseBool(config.showHeroToggle, parseBool(config.defaultShowHeroToggle, false));
        var hero = null;
        var heroToggleEnabled = baseHeroToggleEnabled;
        var heroToggle = null;
        var heroObserver = null;
        var scrollFallback = null;
        var domObserver = null;
        var refreshTimer = null;
        var refreshFollowUps = [];
        var forceVisible = false;
        var heroVisible = false;

        var setWidgetVisible = function(visible) {{
            if (visible) {{
                widget.classList.add('ai-chat-visible');
                widget.setAttribute('aria-hidden', 'false');
            }} else {{
                widget.classList.remove('ai-chat-visible');
                widget.setAttribute('aria-hidden', 'true');
                closeAIChatPanel();
            }}
        }};

        var updateVisibility = function() {{
            var shouldShow = forceVisible || !hero || !heroVisible || shouldShowImmediatelyAfterHero(hero) || shouldShowForCompactMobileBookingBar();
            setWidgetVisible(shouldShow);
        }};

        var clearRefreshFollowUps = function() {{
            while (refreshFollowUps.length) {{
                window.clearTimeout(refreshFollowUps.pop());
            }}
        }};

        var scheduleRefreshHero = function() {{
            if (refreshTimer) {{
                window.clearTimeout(refreshTimer);
            }}
            clearRefreshFollowUps();
            refreshTimer = window.setTimeout(function() {{
                refreshTimer = null;
                refreshHero();
                refreshFollowUps.push(window.setTimeout(refreshHero, 120));
                refreshFollowUps.push(window.setTimeout(refreshHero, 400));
            }}, 0);
        }};

        var stopHeroObserver = function() {{
            if (heroObserver && typeof heroObserver.disconnect === 'function') {{
                heroObserver.disconnect();
            }}
            heroObserver = null;
            if (scrollFallback) {{
                window.removeEventListener('scroll', scrollFallback);
                window.removeEventListener('resize', scrollFallback);
                scrollFallback = null;
            }}
        }};

        var observeDomChanges = function() {{
            if (!('MutationObserver' in window)) return;
            if (domObserver && typeof domObserver.disconnect === 'function') {{
                domObserver.disconnect();
            }}
            domObserver = new MutationObserver(function(mutations) {{
                for (var i = 0; i < mutations.length; i++) {{
                    var mutation = mutations[i];
                    if (mutation.type === 'childList' && (mutation.addedNodes.length || mutation.removedNodes.length)) {{
                        scheduleRefreshHero();
                        return;
                    }}
                }}
            }});
            domObserver.observe(document.documentElement, {{ childList: true, subtree: true }});
        }};

        var installNavigationHooks = function() {{
            if (window.__aiChatNavigationHooksInstalled) return;
            window.__aiChatNavigationHooksInstalled = true;
            var dispatchRouteChange = function() {{
                window.dispatchEvent(new Event('ai-chat-route-change'));
            }};
            var wrapHistoryMethod = function(methodName) {{
                if (!window.history || typeof window.history[methodName] !== 'function') return;
                var original = window.history[methodName];
                window.history[methodName] = function() {{
                    var result = original.apply(this, arguments);
                    dispatchRouteChange();
                    return result;
                }};
            }};
            wrapHistoryMethod('pushState');
            wrapHistoryMethod('replaceState');
            window.addEventListener('popstate', dispatchRouteChange);
            window.addEventListener('hashchange', dispatchRouteChange);
        }};
        var installVisibilityRefreshHooks = function() {{
            var ticking = false;
            var refresh = function() {{
                if (ticking) return;
                ticking = true;
                window.requestAnimationFrame(function() {{
                    ticking = false;
                    if (hero) heroVisible = computeHeroVisible(hero);
                    updateVisibility();
                }});
            }};
            window.addEventListener('scroll', refresh, {{ passive: true }});
            window.addEventListener('resize', refresh);
        }};

        var syncHeroToggle = function() {{
            if (!hero || !heroToggleEnabled) {{
                removeHeroToggle();
                heroToggle = null;
                return;
            }}
            heroToggle = document.getElementById('ai-chat-hero-toggle');
            if (heroToggle && !hero.contains(heroToggle)) {{
                removeHeroToggle();
                heroToggle = null;
            }}
            if (heroToggle) return;
            var box = document.createElement('div');
            box.className = 'ai-chat-hero-box';
            heroToggle = document.createElement('button');
            heroToggle.id = 'ai-chat-hero-toggle';
            heroToggle.type = 'button';
            heroToggle.className = 'ai-chat-hero-toggle';
            heroToggle.textContent = 'Chat starten';
            heroToggle.setAttribute('aria-pressed', 'false');
            box.appendChild(heroToggle);
            hero.appendChild(box);
            heroToggle.addEventListener('click', function() {{
                forceVisible = !forceVisible;
                heroToggle.setAttribute('aria-pressed', forceVisible ? 'true' : 'false');
                if (forceVisible) {{
                    setWidgetVisible(true);
                    openAIChatPanel();
                }} else {{
                    updateVisibility();
                }}
            }});
        }};

        var observeHero = function() {{
            stopHeroObserver();
            if (!hero) {{
                heroVisible = false;
                updateVisibility();
                return;
            }}
            heroVisible = computeHeroVisible(hero);
            updateVisibility();
            if ('IntersectionObserver' in window) {{
                heroObserver = new IntersectionObserver(function(entries) {{
                    if (entries && entries[0]) {{
                        heroVisible = entries[0].isIntersecting && entries[0].intersectionRatio > 0;
                        updateVisibility();
                    }}
                }}, {{ threshold: 0 }});
                heroObserver.observe(hero);
            }} else {{
                scrollFallback = function() {{
                    heroVisible = computeHeroVisible(hero);
                    updateVisibility();
                }};
                window.addEventListener('scroll', scrollFallback, {{ passive: true }});
                window.addEventListener('resize', scrollFallback);
            }}
        }};

        var refreshHero = function() {{
            var nextHero = resolveHeroElement(config);
            var heroChanged = nextHero !== hero;
            hero = nextHero;
            heroToggleEnabled = baseHeroToggleEnabled;
            if (hero) {{
                var heroToggleAttr = hero.getAttribute('data-ai-chat-hero-toggle');
                if (heroToggleAttr) {{
                    heroToggleEnabled = parseBool(heroToggleAttr, heroToggleEnabled);
                }}
            }}
            syncHeroToggle();
            if (heroChanged) {{
                observeHero();
            }} else {{
                heroVisible = computeHeroVisible(hero);
                updateVisibility();
            }}
        }};

        installNavigationHooks();
        installVisibilityRefreshHooks();
        observeDomChanges();
        scheduleRefreshHero();
        window.addEventListener('load', scheduleRefreshHero);
        window.addEventListener('resize', scheduleRefreshHero);
        window.addEventListener('ai-chat-route-change', scheduleRefreshHero);
        }})();

    document.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape') {{
            closeAIChatPanel();
        }}
    }});

    window.addEventListener('message', function(e) {{
        if (e.data && e.data.type === 'chat-close') {{
            closeAIChatPanel();
        }}
    }});

    // Fix SVG Icon logic requested
    document.addEventListener("DOMContentLoaded", function() {{
        try {{
            var button = document.querySelector('button#ai-chat-toggle');
            var svg = button ? button.querySelector('svg') : null;
            if (!svg) return;
            var g = svg.querySelector('g');
            
            // 1. Set viewBox to encompass the content
            var bbox = (g || svg).getBBox();
            var padding = 2;
            var adjustedViewBox = (bbox.x - padding) + " " + (bbox.y - padding) + " " + (bbox.width + padding * 2) + " " + (bbox.height + padding * 2);
            svg.setAttribute('viewBox', adjustedViewBox);

            // 2. Ensure SVG fills its parent button
            svg.style.width = '40px';
            svg.style.height = '40px';

            // 3. Make sure the container button isn't hiding it
            button.style.display = 'flex';
            button.style.alignItems = 'center';
            button.style.justifyContent = 'center';
            button.style.boxShadow = 'none';
        }} catch(e) {{
            console.warn("Auto-fix SVG failed:", e);
        }}
    }});
    </script>
    <?php
}}
add_action('wp_footer', 'ai_chat_widget_footer');

// Add admin menu for settings
function ai_chat_widget_admin_menu() {{
    add_options_page(
        'AI Chat Widget',
        'AI Chat Widget',
        'manage_options',
        'ai-chat-widget',
        'ai_chat_widget_settings_page'
    );
}}
add_action('admin_menu', 'ai_chat_widget_admin_menu');

function ai_chat_widget_register_settings() {{
    register_setting('ai_chat_widget_settings', 'ai_chat_widget_icon');
    register_setting('ai_chat_widget_settings', 'ai_chat_widget_button_bg');
}}
add_action('admin_init', 'ai_chat_widget_register_settings');

function ai_chat_widget_settings_page() {{
    $icon_setting = get_option('ai_chat_widget_icon', '');
    $button_bg_setting = get_option('ai_chat_widget_button_bg', '');
    ?>
    <div class="wrap">
        <h1>AI Chat Widget Einstellungen</h1>
        <p>Das AI Chat Widget ist aktiv und zeigt den Chat-Button auf allen Seiten an.</p>
        <h2>Design anpassen</h2>
        <form method="post" action="options.php">
            <?php settings_fields('ai_chat_widget_settings'); ?>
            <table class="form-table">
                <tr>
                    <th scope="row">Icon (SVG oder Emoji)</th>
                    <td>
                        <textarea name="ai_chat_widget_icon" rows="6" class="large-text code"><?php echo esc_textarea($icon_setting); ?></textarea>
                        <p class="description">Leer lassen, um das Standard-Icon aus der Plattform zu verwenden.</p>
                    </td>
                </tr>
                <tr>
                    <th scope="row">Chat-Button Farbe</th>
                    <td>
                        <input type="text" name="ai_chat_widget_button_bg" value="<?php echo esc_attr($button_bg_setting); ?>" class="regular-text" placeholder="<?php echo esc_attr($button_bg); ?>" />
                        <p class="description">Optional. Z.B. #8c8875 oder linear-gradient(...)</p>
                    </td>
                </tr>
            </table>
            <?php submit_button(); ?>
        </form>
        <h2>Aktuelle Konfiguration</h2>
        <table class="form-table">
            <tr>
                <th>Embed URL</th>
                <td><code><?php echo esc_html('{embed_url_safe}'); ?></code></td>
            </tr>
            <tr>
                <th>Icon</th>
                <td><?php echo esc_html($icon); ?></td>
            </tr>
        </table>
        <p><em>Um die Einstellungen zu ändern, lade ein neues Plugin von der Scraping Admin Plattform herunter.</em></p>
    </div>
    <?php
}}
'''
    
    # Create ZIP file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('ai-chat-widget/ai-chat-widget.php', plugin_php)
        
        # Add a readme file
        readme = """# AI Chat Widget für WordPress

## Installation

1. Lade diese ZIP-Datei in deinem WordPress-Admin unter "Plugins > Installieren > Plugin hochladen" hoch
2. Aktiviere das Plugin "AI Chat Widget"
3. Das Chat-Widget erscheint automatisch auf allen Seiten unten rechts

## Einstellungen

Nach der Aktivierung findest du unter "Einstellungen > AI Chat Widget" eine Übersicht deiner Konfiguration.

## Anpassungen

Um das Design oder die Konfiguration zu ändern:
1. Gehe zur Scraping Admin Plattform
2. Passe das Design unter "Widget Design" an
3. Lade unter "Integrieren" ein neues Plugin herunter
4. Ersetze das alte Plugin durch das neue

## Support

Bei Fragen wende dich an das Scraping Admin Team.
"""
        zf.writestr('ai-chat-widget/readme.txt', readme)
    
    zip_buffer.seek(0)
    
    return Response(
        zip_buffer.getvalue(),
        mimetype='application/zip',
        headers={
            'Content-Disposition': 'attachment; filename=ai-chat-widget.zip'
        }
    )


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    try:
        user_id = _require_user_id()
        return jsonify({"user_id": user_id})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Auth-Check fehlgeschlagen: {exc}"}), 500


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email und Passwort erforderlich."}), 400

    try:
        auth_client = _get_supabase_public_client()
        sign_res = auth_client.auth.sign_up({"email": email, "password": password})
        session = getattr(sign_res, "session", None)
        access_token = session.access_token if session else None
        user = sign_res.user if hasattr(sign_res, "user") else None

        # Falls Supabase Email-Confirmation aktiviert ist, kommt kein Token. Versuche direktes Login.
        if (not access_token or not user) and sign_res.user and sign_res.user.email:
            login_res = auth_client.auth.sign_in_with_password({"email": email, "password": password})
            session = getattr(login_res, "session", None)
            access_token = session.access_token if session else None
            user = login_res.user if hasattr(login_res, "user") else sign_res.user

        if not access_token or not user:
            return jsonify(
                {
                    "error": "Registrierung fehlgeschlagen (kein Token erhalten). "
                    "Bitte in Supabase Email-Confirmations deaktivieren oder nach E-Mail-Bestätigung erneut einloggen."
                }
            ), 202

        user_id = user.id
        profile = _ensure_profile(user_id, email=email)
        bot = _ensure_default_bot(user_id, profile.get("slug") or user_id)
        ctx = BotContext(
            customer_id=user_id,
            customer_slug=profile.get("slug") or user_id,
            bot_id=str(bot.get("id")),
            bot_slug=bot.get("slug"),
            docs_dir=_ensure_path(bot.get("output_markdown_path")),
            faiss_dir=_ensure_path(bot.get("faiss_path")),
            summary_path=_ensure_path(bot.get("base_path") or f"kunden/{profile.get('slug')}/{bot.get('slug')}/") / "summary.json",
            faq_path=_ensure_path(bot.get("output_markdown_path")) / "faqs.md",
            upload_dir=_ensure_path(bot.get("output_markdown_path")) / "uploads",
            model=bot.get("model") or DEFAULT_MODEL,
            retriever_k=int(bot.get("retriever_k") or DEFAULT_RETRIEVER_K),
            prompt_path=_ensure_path(bot.get("base_path") or f"kunden/{profile.get('slug')}/{bot.get('slug')}/") / "prompt.txt",
        )
        _ensure_bot_dirs(ctx)

        return jsonify(
            {
                "access_token": access_token,
                "user_id": user_id,
                "default_bot_slug": bot.get("slug"),
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Registrierung fehlgeschlagen: {exc}"}), 500


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email und Passwort erforderlich."}), 400

    try:
        auth_client = _get_supabase_public_client()
        login_res = auth_client.auth.sign_in_with_password({"email": email, "password": password})
        session = getattr(login_res, "session", None)
        access_token = session.access_token if session else None
        user = login_res.user if hasattr(login_res, "user") else None
        if not access_token or not user:
            return jsonify({"error": "Login fehlgeschlagen (kein Token erhalten)."}), 401

        user_id = user.id
        profile = _ensure_profile(user_id, email=email)
        bot = _ensure_default_bot(user_id, profile.get("slug") or user_id)
        return jsonify(
            {
                "access_token": access_token,
                "user_id": user_id,
                "default_bot_slug": bot.get("slug"),
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Login fehlgeschlagen: {exc}"}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    history = data.get("history")
    lang = _extract_chat_lang(data)
    bot_slug = _extract_bot_slug(data)
    customer_id = _extract_customer_id(data)

    # Bereits angezeigte Buttons: verhindert, dass dieselbe Empfehlung erneut kommt.
    offered = [str(item) for item in (data.get("offered") or []) if str(item).strip()][-10:]

    if not question:
        return jsonify({"error": "Keine Frage übergeben."}), 400
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            try:
                ctx = _build_bot_context(bot_slug)
            except PermissionError as auth_err:
                # Token fehlt/abgelaufen oder Bot gehört nicht zum User:
                # Fallback auf public + customer_id, damit der Chat weiter läuft.
                if not customer_id:
                    msg = str(auth_err)
                    status = 401
                    if "Chatbot nicht gefunden" in msg:
                        status = 404
                    return jsonify({"error": msg}), status
        if ctx is None:
            # Public bot context - optional customer_id for disambiguation
            ctx = _build_public_bot_context(bot_slug, customer_id=customer_id)
        rag = get_qa(ctx)
        # Die verwendeten Kontext-Dokumente einsammeln, damit die Karte zur
        # Antwort passt und kein zweites Retrieval nötig ist.
        answer_docs: List[Any] = []
        answer = rag(question, history=history, lang=lang, docs_out=answer_docs)
        try:
            rich = _build_rich_reply(
                ctx, rag, question, answer, history, lang, docs=answer_docs, offered=offered
            )
        except Exception as rich_exc:
            print("Rich-Reply konnte nicht erzeugt werden:", rich_exc)
            try:
                fallback_cfg = _resolve_widget_config(ctx, lang)
            except Exception:
                fallback_cfg = {}
            rich = {
                "version": 1,
                "actions": _build_quick_actions(None, question, answer, lang, widget_cfg=fallback_cfg),
            }
        _record_chat_event(ctx, ctx.customer_id, question, answer, status="ok")
        return jsonify({"answer": answer, "rich": rich})
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as e:
        print("Error in /api/chat:", e)
        try:
            ctx = ctx if "ctx" in locals() else None
            if ctx:
                _record_chat_event(ctx, ctx.customer_id, question, None, status="error", error=str(e))
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/faqs", methods=["GET", "POST"])
def admin_faqs():
    payload = {}
    if request.method == "POST":
        payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        if request.method == "GET":
            return jsonify({"faqs": _read_faqs(ctx)})

        faqs = payload.get("faqs")
        if not isinstance(faqs, list):
            return jsonify({"error": "Payload muss 'faqs' als Liste enthalten."}), 400

        saved = _write_faqs(ctx, faqs)
        return jsonify({"status": "ok", "faqs": saved})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"FAQs konnten nicht verarbeitet werden: {exc}"}), 500


@app.route("/api/admin/memory", methods=["GET"])
def admin_memory():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    query = (request.args.get("q") or "").strip()
    limit_raw = request.args.get("limit") or "80"
    try:
        limit = int(limit_raw)
    except Exception:
        limit = 80
    limit = max(1, min(limit, 200))

    try:
        ctx = _build_bot_context(bot_slug)
        vectordb = _load_vectordb(ctx)
        snapshot = _memory_snapshot(vectordb, query or None, limit)
        snapshot["index_dir"] = str(ctx.faiss_dir)
        return jsonify(snapshot)
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Memory konnte nicht geladen werden: {exc}"}), 500


@app.route("/api/admin/memory/delete", methods=["POST"])
def admin_memory_delete():
    payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    ids = payload.get("ids") or []
    chunk_ids = payload.get("chunk_ids") or []
    targets = {str(_id).strip() for _id in ids if str(_id).strip()}
    chunk_targets = {str(_cid).strip() for _cid in chunk_ids if str(_cid).strip()}

    if not targets and not chunk_targets:
        return jsonify({"error": "ids oder chunk_ids erforderlich."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        vectordb = _load_vectordb(ctx)
        docstore = getattr(vectordb, "docstore", None)
        store = docstore._dict if hasattr(docstore, "_dict") else {}
        chunk_to_doc_id = {}
        for doc_id, doc in store.items():
            meta = doc.metadata or {}
            chunk_key = meta.get("chunk_id") or meta.get("doc_id") or doc_id
            chunk_to_doc_id[chunk_key] = doc_id

        for chunk_id in chunk_targets:
            mapped = chunk_to_doc_id.get(chunk_id)
            if mapped:
                targets.add(mapped)

        if not targets:
            return jsonify({"error": "Keine passenden Einträge gefunden."}), 404

        missing_chunks = [cid for cid in chunk_targets if cid not in chunk_to_doc_id]

        vectordb.delete(list(targets))
        vectordb.save_local(str(ctx.faiss_dir))
        _invalidate_bot_cache(ctx)

        remaining = len(store)
        return jsonify(
            {
                "status": "ok",
                "deleted": len(targets),
                "missing_chunks": missing_chunks,
                "remaining": remaining if remaining >= 0 else None,
            }
        )
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Einträge konnten nicht gelöscht werden: {exc}"}), 500


@app.route("/api/admin/memory/update", methods=["POST"])
def admin_memory_update():
    payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)
    doc_id = (payload.get("doc_id") or "").strip()
    chunk_id = (payload.get("chunk_id") or "").strip()
    new_text = (payload.get("text") or "").strip()
    new_title = (payload.get("title") or "").strip()
    new_source = (payload.get("source") or "").strip()

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400
    if not new_text:
        return jsonify({"error": "text fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        vectordb = _load_vectordb(ctx)
        docstore = getattr(vectordb, "docstore", None)
        store = docstore._dict if hasattr(docstore, "_dict") else {}

        chunk_to_doc_id = {}
        for did, doc in store.items():
            meta = doc.metadata or {}
            chunk_key = meta.get("chunk_id") or meta.get("doc_id") or did
            chunk_to_doc_id[chunk_key] = did

        target_id = doc_id or chunk_to_doc_id.get(chunk_id or "")
        if not target_id:
            return jsonify({"error": "Chunk/Doc nicht gefunden."}), 404

        old_doc = store.get(target_id)
        if not old_doc:
            return jsonify({"error": "Dokument nicht gefunden."}), 404

        meta = dict(old_doc.metadata or {})
        meta["chunk_id"] = meta.get("chunk_id") or chunk_id or target_id
        meta["doc_id"] = target_id
        if new_title:
            meta["title"] = new_title
        if new_source:
            meta["source"] = new_source
        meta["edited_at"] = datetime.utcnow().isoformat() + "Z"

        new_doc = Document(page_content=new_text, metadata=meta)

        vectordb.delete([target_id])
        vectordb.add_documents([new_doc], ids=[target_id])
        vectordb.save_local(str(ctx.faiss_dir))
        _invalidate_bot_cache(ctx)

        serialized = _serialize_memory_entry(target_id, new_doc, None, {meta["chunk_id"]: target_id})
        return jsonify({"status": "ok", "item": serialized})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Eintrag konnte nicht aktualisiert werden: {exc}"}), 500


@app.route("/api/admin/prompt", methods=["GET", "POST"])
def admin_prompt():
    payload = {}
    if request.method == "POST":
        payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        _ensure_bot_dirs(ctx)
        if request.method == "GET":
            return jsonify({"prompt": _read_prompt(ctx)})

        prompt_text = payload.get("prompt")
        saved = _write_prompt(ctx, prompt_text or "")
        return jsonify({"status": "ok", "prompt": saved})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Prompt konnte nicht verarbeitet werden: {exc}"}), 500


@app.route("/api/admin/schedule", methods=["GET", "POST"])
def admin_schedule():
    payload = {}
    if request.method == "POST":
        payload = request.get_json(force=True) or {}
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        _ensure_bot_dirs(ctx)
        _ensure_scheduler_started()

        if request.method == "GET":
            changed = False
            with _schedule_lock:
                entry = _schedules.get(ctx.bot_id) or {}
                if entry and entry.get("enabled"):
                    _, changed = _ensure_schedule_next_run(entry, now=datetime.utcnow())
                if changed:
                    _save_schedules_to_disk()
            return jsonify(entry if entry else {"enabled": False})

        freq = (payload.get("frequency") or "").strip().lower()
        enabled = _as_bool(payload.get("enabled"), True)
        minutes = _schedule_minutes_from_payload(freq, payload.get("minutes"))

        with _schedule_lock:
            existing = _schedules.get(ctx.bot_id) or {}
            sitemap_url = (payload.get("sitemap_url") or "").strip() or (existing.get("sitemap_url") or "").strip()
            if enabled and (not sitemap_url or minutes <= 0):
                return jsonify({"error": "Bitte sitemap_url und ein gültiges Intervall angeben."}), 400
            last_run = existing.get("last_run")
            next_run_at = existing.get("next_run_at")
            if enabled:
                next_run_at = _iso_utc(datetime.utcnow() + timedelta(minutes=minutes))
            _schedules[ctx.bot_id] = {
                "bot_id": ctx.bot_id,
                "bot_slug": ctx.bot_slug,
                "customer_id": ctx.customer_id,
                "customer_slug": ctx.customer_slug,
                "sitemap_url": sitemap_url,
                "frequency": freq or "custom",
                "minutes": minutes,
                "enabled": enabled,
                "last_run": last_run,
                "next_run_at": next_run_at,
                "paths": {
                    "base_path": str(ctx.docs_dir.parent),
                    "docs_dir": str(ctx.docs_dir),
                    "faiss_dir": str(ctx.faiss_dir),
                    "summary_path": str(ctx.summary_path),
                    "faq_path": str(ctx.faq_path),
                    "upload_dir": str(ctx.upload_dir),
                    "prompt_path": str(ctx.prompt_path),
                },
                "model": ctx.model,
                "retriever_k": ctx.retriever_k,
            }
            _save_schedules_to_disk()

        return jsonify({"status": "ok", "schedule": _schedules.get(ctx.bot_id)})
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Schedule konnte nicht verarbeitet werden: {exc}"}), 500


@app.route("/api/admin/status", methods=["GET"])
def admin_status():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        state = _run_state.get(ctx.bot_id, {"running": False, "last_run": None})
        return jsonify(
            {
                "running": state.get("running", False),
                "started_at": state.get("started_at"),
                "mode": state.get("mode"),
                "requested_sitemap_url": state.get("requested_sitemap_url"),
                "last_run": state.get("last_run"),
                "index_exists": ctx.faiss_dir.is_dir(),
                "summary": _load_summary(ctx),
            }
        )
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/admin/chat_stats", methods=["GET"])
def admin_chat_stats():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        # Nur der Besitzer darf Chat-Statistiken sehen
        ctx = _build_bot_context(bot_slug)
        # Kleines Cache-Fenster, um unnötige Supabase-Calls bei schnellen Reloads zu vermeiden.
        cache_key = f"{ctx.bot_id}:chat_stats"
        with _chat_stats_cache_lock:
            cached = _chat_stats_cache.get(cache_key)
            if cached and (datetime.utcnow() - cached["ts"]).total_seconds() < 30:
                return jsonify(cached["data"])

        resp = (
            _get_supabase_client()
            .table("chat_events")
            .select("*")
            .eq("bot_id", ctx.bot_id)
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
        )
        rows = resp.data if hasattr(resp, "data") else []
        stats = _aggregate_chat_events(rows or [])

        with _chat_stats_cache_lock:
            _chat_stats_cache[cache_key] = {"ts": datetime.utcnow(), "data": stats}

        return jsonify(stats)
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as exc:
        return jsonify({"error": f"Chat-Statistiken konnten nicht geladen werden: {exc}"}), 500


@app.route("/api/admin/topic_questions", methods=["GET"])
def admin_topic_questions():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    topic_name = (request.args.get("topic") or "").strip()
    if not topic_name:
        return jsonify({"error": "topic fehlt."}), 400
    if topic_name not in TOPIC_CATEGORIES:
        return jsonify({"error": f"Unbekanntes Thema: {topic_name}"}), 404

    try:
        ctx = _build_bot_context(bot_slug)
        resp = (
            _get_supabase_client()
            .table("chat_events")
            .select("question")
            .eq("bot_id", ctx.bot_id)
            .order("created_at", desc=True)
            .limit(3000)
            .execute()
        )
        rows = resp.data if hasattr(resp, "data") else []
        question_counter = _topic_question_counts(rows or [], topic_name)
        items = [{"q": q, "count": c} for q, c in question_counter.most_common(50)]
        return jsonify(
            {
                "topic": topic_name,
                "icon": TOPIC_CATEGORIES[topic_name]["icon"],
                "total": sum(question_counter.values()),
                "questions": items,
            }
        )
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 404
    except Exception as exc:
        return jsonify({"error": f"Themen-Fragen konnten nicht geladen werden: {exc}"}), 500


@app.route("/api/admin/usage", methods=["GET"])
def admin_usage():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        pricing = _pricing_for_model(ctx.model)

        resp = (
            _get_supabase_client()
            .table("chat_events")
            .select("question, answer, status")
            .eq("bot_id", ctx.bot_id)
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
        )
        rows = resp.data if hasattr(resp, "data") else []

        total = len(rows)
        est_input_tokens = 0
        est_output_tokens = 0
        answered = 0

        for row in rows:
            q = (row.get("question") or "").strip()
            a = (row.get("answer") or "").strip()
            est_input_tokens += _estimate_tokens(q)
            est_output_tokens += _estimate_tokens(a)
            if (row.get("status") or "").lower() in {"ok", "answered", "success"}:
                answered += 1

        cost = (
            (est_input_tokens / 1000.0) * pricing["input"]
            + (est_output_tokens / 1000.0) * pricing["output"]
        )

        return jsonify(
            {
                "total_chats": total,
                "answered": answered,
                "est_input_tokens": est_input_tokens,
                "est_output_tokens": est_output_tokens,
                "pricing": pricing,
                "est_cost_usd": round(cost, 4),
                "model": ctx.model,
                "note": "Schätzung basierend auf 1000 letzten Events und 1 Token ≈ 4 Zeichen.",
                "source": "estimated",
            }
        )
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Nutzung konnte nicht berechnet werden: {exc}"}), 500


@app.route("/api/admin/usage/live", methods=["GET"])
def admin_usage_live():
    bot_slug = _extract_bot_slug(None)
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY fehlt."}), 400

    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    params = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get("https://api.openai.com/v1/usage", headers=headers, params=params, timeout=15)
        if resp.status_code in {403, 404}:
            return jsonify({"error": "Usage-API nicht freigeschaltet oder kein Zugriff (403/404)."}), 403
        if not resp.ok:
            return jsonify({"error": f"Usage-API Fehler {resp.status_code}: {resp.text[:200]}"}), resp.status_code

        payload = resp.json()
        items = payload.get("data") or payload.get("items") or []
        cost_info = _cost_from_usage_items(items)

        return jsonify(
            {
                "source": "live",
                "start_date": start_date or payload.get("start_date"),
                "end_date": end_date or payload.get("end_date"),
                "items": cost_info["per_model"],
                "est_input_tokens": cost_info["totals"]["input"],
                "est_output_tokens": cost_info["totals"]["output"],
                "est_cost_usd": cost_info["cost_usd"],
                "pricing": None,
                "model": "multi",
                "note": "Live-Daten aus /v1/usage; Kosten lokal berechnet anhand Preisliste.",
                "raw": {"total_requests": payload.get("total_requests"), "object": payload.get("object")},
            }
        )
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"Usage-API Anfrage fehlgeschlagen: {exc}"}), 500


@app.route("/api/admin/run", methods=["POST"])
def admin_run():
    payload = request.get_json(force=True) or {}
    sitemap_url = (payload.get("sitemap_url") or "").strip()
    index_only = _as_bool(payload.get("index_only"), False)
    async_mode = _as_bool(payload.get("async"), True)
    cleanup_stale = _as_bool(payload.get("cleanup_stale"), False)
    bot_slug = _extract_bot_slug(payload)

    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    if not index_only and not sitemap_url:
        return jsonify({"error": "Bitte eine Sitemap-URL angeben oder index_only setzen."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if not index_only and sitemap_url:
        changed = False
        _ensure_scheduler_started()
        with _schedule_lock:
            entry = _schedules.get(ctx.bot_id)
            if entry:
                if (entry.get("sitemap_url") or "").strip() != sitemap_url:
                    entry["sitemap_url"] = sitemap_url
                    changed = True
            else:
                changed = True
                _schedules[ctx.bot_id] = {
                    "bot_id": ctx.bot_id,
                    "bot_slug": ctx.bot_slug,
                    "customer_id": ctx.customer_id,
                    "customer_slug": ctx.customer_slug,
                    "sitemap_url": sitemap_url,
                    "frequency": "weekly",
                    "minutes": 60 * 24 * 7,
                    "enabled": False,
                    "last_run": None,
                    "next_run_at": None,
                    "paths": {
                        "base_path": str(ctx.docs_dir.parent),
                        "docs_dir": str(ctx.docs_dir),
                        "faiss_dir": str(ctx.faiss_dir),
                        "summary_path": str(ctx.summary_path),
                        "faq_path": str(ctx.faq_path),
                        "upload_dir": str(ctx.upload_dir),
                        "prompt_path": str(ctx.prompt_path),
                    },
                    "model": ctx.model,
                    "retriever_k": ctx.retriever_k,
                }
        if changed:
            _save_schedules_to_disk()

    lock = _bot_run_lock(ctx)

    if lock.locked():
        return jsonify({"error": "Ein anderer Job läuft bereits für diesen Bot."}), 409

    acquired = lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "Ein anderer Job läuft bereits für diesen Bot."}), 409

    started_at = _iso_utc(datetime.utcnow())
    _run_state.setdefault(ctx.bot_id, {"running": False, "last_run": None})
    _run_state[ctx.bot_id]["running"] = True
    _run_state[ctx.bot_id]["started_at"] = started_at
    _run_state[ctx.bot_id]["mode"] = "index_only" if index_only else "scrape_and_index"
    _run_state[ctx.bot_id]["requested_sitemap_url"] = None if index_only else sitemap_url

    if async_mode:
        def _worker():
            try:
                result = (
                    _run_index_only(ctx)
                    if index_only
                    else _run_scrape_and_index(ctx, sitemap_url, cleanup_stale=cleanup_stale)
                )
            except Exception as exc:
                result = {
                    "status": "error",
                    "mode": "index_only" if index_only else "scrape_and_index",
                    "bot_slug": ctx.bot_slug,
                    "started_at": started_at,
                    "finished_at": _iso_utc(datetime.utcnow()),
                    "error": f"Background-Job fehlgeschlagen: {exc}",
                    "traceback": traceback.format_exc().splitlines(),
                    "logs": [],
                    "summary": None,
                }
            finally:
                _run_state[ctx.bot_id]["last_run"] = result
                _run_state[ctx.bot_id]["running"] = False
                _run_state[ctx.bot_id]["started_at"] = None
                _run_state[ctx.bot_id]["mode"] = None
                _run_state[ctx.bot_id]["requested_sitemap_url"] = None
                lock.release()

        threading.Thread(target=_worker, daemon=True).start()
        return jsonify(
            {
                "status": "started",
                "running": True,
                "mode": "index_only" if index_only else "scrape_and_index",
                "requested_sitemap_url": None if index_only else sitemap_url,
                "cleanup_stale": bool(cleanup_stale),
                "bot_slug": ctx.bot_slug,
                "started_at": started_at,
                "message": (
                    "Vollständiger Reindex gestartet. Fortschritt über /api/admin/status abrufen."
                    if index_only
                    else "Job gestartet. Fortschritt über /api/admin/status abrufen."
                ),
            }
        ), 202

    try:
        result = (
            _run_index_only(ctx)
            if index_only
            else _run_scrape_and_index(ctx, sitemap_url, cleanup_stale=cleanup_stale)
        )
        _run_state[ctx.bot_id]["last_run"] = result
        status_code = 200 if result.get("status") in {"ok", "warn"} else 500
        return jsonify(result), status_code
    finally:
        _run_state[ctx.bot_id]["running"] = False
        _run_state[ctx.bot_id]["started_at"] = None
        _run_state[ctx.bot_id]["mode"] = None
        _run_state[ctx.bot_id]["requested_sitemap_url"] = None
        lock.release()


@app.route("/api/admin/upload_pdf", methods=["POST"])
def upload_pdf():
    bot_slug = _extract_bot_slug(request.form.to_dict(flat=True))
    if not bot_slug:
        return jsonify({"error": "bot_slug fehlt."}), 400

    files = request.files.getlist("pdf") or request.files.getlist("pdfs")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"error": "Bitte mindestens eine PDF-Datei hochladen (Form-Field 'pdf')."}), 400

    try:
        ctx = _build_bot_context(bot_slug)
        max_bytes = 12 * 1024 * 1024  # 12 MB pro Datei
        results: List[Dict[str, Any]] = []
        uploaded_paths: List[str] = []

        for pdf_file in files:
            filename = (pdf_file.filename or "").strip() or "upload.pdf"
            item: Dict[str, Any] = {"filename": filename}

            if not filename.lower().endswith(".pdf"):
                item["status"] = "error"
                item["error"] = "Nur PDF-Dateien sind erlaubt."
                results.append(item)
                continue

            data = pdf_file.read()
            if not data:
                item["status"] = "error"
                item["error"] = "Die Datei ist leer."
                results.append(item)
                continue

            if len(data) > max_bytes:
                item["status"] = "error"
                item["error"] = "PDF ist zu groß (max. 12 MB pro Datei)."
                results.append(item)
                continue

            try:
                markdown_body = _pdf_bytes_to_markdown(data)
                md_rel_path = _write_pdf_markdown(ctx, filename, markdown_body)
                item["status"] = "ok"
                item["markdown_file"] = md_rel_path
                uploaded_paths.append(md_rel_path)
            except Exception as exc:
                item["status"] = "error"
                item["error"] = f"PDF konnte nicht verarbeitet werden: {exc}"

            results.append(item)

        success_count = len(uploaded_paths)
        error_count = len(results) - success_count

        if success_count == 0:
            return jsonify(
                {
                    "status": "error",
                    "error": "Keine PDF-Datei konnte verarbeitet werden.",
                    "uploaded_count": 0,
                    "failed_count": error_count,
                    "total_files": len(results),
                    "results": results,
                }
            ), 400

        run_status = "ok" if error_count == 0 else "partial"
        info = (
            f"{success_count}/{len(results)} PDF-Dateien gespeichert. "
            "Bitte 'Nur Index neu bauen' ausführen."
        )
        response: Dict[str, Any] = {
            "status": run_status,
            "uploaded_count": success_count,
            "failed_count": error_count,
            "total_files": len(results),
            "markdown_files": uploaded_paths,
            "results": results,
            "info": info,
        }
        if success_count == 1:
            response["markdown_file"] = uploaded_paths[0]
        return jsonify(response), 200
    except PermissionError as auth_err:
        return jsonify({"error": str(auth_err)}), 401
    except Exception as exc:
        return jsonify({"error": f"PDF-Upload fehlgeschlagen: {exc}"}), 500


if __name__ == "__main__":
    # Relative Redirects bleiben korrekt; hier wird nur festgelegt, auf welchem Port
    # die App selbst lauscht, wenn sie direkt per `python app.py` gestartet wird.
    host = os.environ.get("APP_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("APP_PORT") or os.environ.get("PORT") or "8443")
    except ValueError:
        port = 8443
    debug = (os.environ.get("FLASK_DEBUG", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    app.run(host=host, port=port, debug=debug)
