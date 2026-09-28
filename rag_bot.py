"""Worker Telegram Komara avec polling robuste, multilingue et mémoire locale (SQLite)."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import signal
import sqlite3
import tempfile
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import telebot
from dotenv import load_dotenv
from telebot.apihelper import ApiTelegramException
from telebot.types import ReplyKeyboardMarkup

from local_search import significant_token_count, trouver_meilleure_reponse
from local_stats import record_unrecognized
import actions
import catalogue
import kb_import
import google_link
import osm_maps
import backup_drive
import weekly_report
import tts
import img_gen
import skills
from normalize_text import normalize_text

# ---------------------------------------------------------------------------
# Configuration générale
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip()
POLL_TIMEOUT = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30"))
LONG_POLLING_TIMEOUT = int(os.getenv("TELEGRAM_LONG_POLLING_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("TELEGRAM_MAX_RETRIES", "8"))
DROP_PENDING_UPDATES = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "true").lower() in {"1", "true", "yes", "on"}

MONITOR_API_URL = (os.getenv("MONITOR_API_URL") or "").strip().rstrip("/")
MONITOR_API_KEY = (os.getenv("MONITOR_API_KEY") or os.getenv("KOMARA_API_KEY") or "").strip()
HEARTBEAT_INTERVAL = max(30, int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "60")))
HEARTBEAT_STOP = threading.Event()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("komara.telegram")

if not TOKEN:
    raise RuntimeError("La variable d'environnement TELEGRAM_TOKEN est absente.")

bot = telebot.TeleBot(TOKEN)

# ---------------------------------------------------------------------------
# Détecteur de langue local (Optimisé avec intersections de sets)
# ---------------------------------------------------------------------------

LANGUAGE_MARKERS: dict[str, dict[str, Any]] = {
    "fr": {
        "words": {
            "le", "la", "les", "un", "une", "des", "de", "du", "et", "est",
            "sont", "je", "tu", "il", "nous", "vous", "ils", "mon", "ton",
            "son", "notre", "votre", "leur", "ce", "cette", "ces", "que",
            "qui", "quoi", "dont", "où", "comment", "pourquoi", "quand",
            "avec", "sans", "sur", "sous", "dans", "pour", "par", "mais",
            "oui", "non", "merci", "bonjour", "bonsoir", "prix", "tarif",
            "combien", "voulez", "pouvez", "faites", "proposez", "agence",
            "bot", "service", "créez", "développement", "site", "application",
            "salut", "salam", "coucou",
        },
        "patterns": ["'", "œ", "à", "é", "è", "ê", "ë", "î", "ï", "ô", "ù", "û", "ü", "ç"],
    },
    "en": {
        "words": {
            "the", "a", "an", "is", "are", "am", "was", "were", "be", "been",
            "i", "you", "he", "she", "it", "we", "they", "my", "your", "his",
            "her", "our", "their", "this", "that", "these", "those", "what",
            "which", "who", "whom", "how", "why", "when", "where", "with",
            "without", "on", "under", "in", "for", "by", "but", "yes", "no",
            "thanks", "hello", "goodbye", "price", "cost", "much",
            "want", "can", "do", "make", "create", "service", "bot", "app",
            "website", "development", "please", "would", "could", "should",
            "hi", "hey",
        },
        "patterns": [],
    },
    "ar": {
        "words": {
            "من", "في", "على", "إلى", "عن", "مع", "هذا", "هذه", "ذلك",
            "ما", "كيف", "لماذا", "متى", "أين", "نعم", "لا", "شكرا",
            "مرحبا", "سلام", "كم", "ثمن", "سعر", "خدمة", "هل", "أريد",
            "عندكم", "تقدمون", "تصنعون", "موقع", "تطبيق", "بوت",
        },
        "patterns": [],
        "unicode_ranges": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    },
    "es": {
        "words": {
            "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
            "y", "es", "son", "yo", "tú", "él", "nosotros", "ustedes", "ellos",
            "mi", "tu", "su", "nuestro", "este", "esta", "estos", "que", "qué",
            "quien", "cómo", "por", "cuándo", "dónde", "con", "sin", "para",
            "pero", "sí", "no", "gracias", "hola", "precio", "costo", "cuánto",
            "quiero", "puede", "hacen", "servicio", "bot", "sitio", "aplicación",
        },
        "patterns": ["ñ", "á", "é", "í", "ó", "ú", "ü", "¿", "¡"],
    },
}

DEFAULT_LANGUAGE = "fr"
MIN_CONFIDENCE = 2

def detect_language(text: str) -> str:
    if not text or not text.strip():
        return DEFAULT_LANGUAGE

    text_lower = text.lower()
    words_in_text = set(re.findall(r'\b\w+\b', text_lower))
    scores: dict[str, int] = {}

    for lang, config in LANGUAGE_MARKERS.items():
        score = 0
        lang_words = config.get("words", set())
        score += len(words_in_text.intersection(lang_words)) * 2

        for pattern in config.get("patterns", []):
            score += text_lower.count(pattern)

        for start, end in config.get("unicode_ranges", []):
            score += sum(1 for char in text if start <= ord(char) <= end)

        if score > 0:
            scores[lang] = score

    if not scores:
        return DEFAULT_LANGUAGE

    best_lang = max(scores, key=scores.get)
    return best_lang if scores[best_lang] >= MIN_CONFIDENCE else DEFAULT_LANGUAGE

def get_supported_languages() -> list[str]:
    return list(LANGUAGE_MARKERS.keys())

# ---------------------------------------------------------------------------
# Chargement multilingue des bases de connaissances
# ---------------------------------------------------------------------------

KB_PATH = BASE_DIR / "kb.json"
FAQ_PATH = BASE_DIR / "docs" / "faq.md"
DIALOGUES_DIR = BASE_DIR / "dialogues"
AYA2_DIALOGUES_PATH = BASE_DIR / "docs" / "aya2" / "dialogues.json"
LANG_DIR = BASE_DIR / "lang"

def load_knowledge_base() -> dict[str, Any]:
    if not KB_PATH.is_file():
        logger.error("Le fichier kb.json est absent : %s", KB_PATH)
        raise RuntimeError(f"Le fichier de base de connaissances {KB_PATH} est absent.")
    with KB_PATH.open("r", encoding="utf-8") as kb_file:
        data = json.load(kb_file)
        logger.info("Base de connaissances (kb.json) chargé avec succès.")
        return data

def _parse_markdown_sections(content: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    sections = re.split(r"(?m)^\s*###\s+(.*?)\s*\n", content)
    for i in range(1, len(sections), 2):
        if i + 1 < len(sections):
            question = sections[i].strip()
            answer = sections[i+1].strip()
            if question and answer:
                # ANTI-FUITE : un "dialogue" qui contient des marqueurs de
                # transcript (🗣️/👤/🤖 en alternance) est un script terrain
                # ou un scénario d'entraînement, pas une réponse à montrer
                # à un client. On ne le charge JAMAIS dans le moteur.
                if "🗣️" in answer or ("🤖" in answer and "👤" in answer):
                    continue
                items.append({"question": question, "answer": answer})
    return items

KNOWLEDGE_TXT_PATH = BASE_DIR / "knowledge.txt"


def load_knowledge_txt() -> str:
    """Lit le persona KOMARA IA (knowledge.txt). Le moteur est un moteur
    de recherche locale : le persona est loggé au démarrage (visible
    Railway) et ses points clés vivent dans la KB (fiches persona_*)."""
    if not KNOWLEDGE_TXT_PATH.is_file():
        logger.warning("knowledge.txt absent — persona KOMARA IA non chargée")
        return ""
    try:
        content = KNOWLEDGE_TXT_PATH.read_text(encoding="utf-8")
        sections = [ln.strip() for ln in content.splitlines()
                    if ln.startswith("###")]
        logger.info(
            "Persona KOMARA IA chargée (knowledge.txt) : %s directives [%s]",
            len(sections), " ; ".join(s.lstrip('# ')[:40] for s in sections[:4]),
        )
        return content
    except Exception as e:
        logger.error("Erreur lecture knowledge.txt : %s", e)
        return ""


def load_local_faq() -> list[dict[str, str]]:
    if not FAQ_PATH.is_file():
        logger.warning("FAQ locale absente : %s", FAQ_PATH)
        return []
    content = FAQ_PATH.read_text(encoding="utf-8")
    items = _parse_markdown_sections(content)
    logger.info("FAQ locale (docs/faq.md) chargée : %s questions", len(items))
    return items

def load_dialogues() -> list[dict[str, str]]:
    dialogues: list[dict[str, str]] = []
    if DIALOGUES_DIR.is_dir():
        for file_path in DIALOGUES_DIR.iterdir():
            if file_path.is_file() and file_path.suffix in {".md", ".txt"}:
                try:
                    content = file_path.read_text(encoding="utf-8")
                    dialogues.extend(_parse_markdown_sections(content))
                except Exception as e:
                    logger.error("Erreur lors de la lecture de %s : %s", file_path.name, e)
    # NB : le pack Aya2 est chargé par langue dans load_language_resources()
    logger.info("Dialogues chargés : %s questions", len(dialogues))
    return dialogues

def load_aya2_dialogues(lang_code: str = "fr") -> list[dict[str, str]]:
    """Dialogues 'ton africain pro' (Pack Aya2) au format JSON, par langue.

    FR = pack maître ; en/es/ar = packs traduits (docs/aya2/dialogues_<lg>.json).
    Repli automatique sur le pack FR si le fichier de la langue est absent.
    """
    path = AYA2_DIALOGUES_PATH
    if lang_code and lang_code != "fr":
        translated = AYA2_DIALOGUES_PATH.with_name(
            AYA2_DIALOGUES_PATH.name.replace("dialogues", f"dialogues_{lang_code}"))
        if translated.is_file():
            path = translated
    if not path.is_file():
        logger.warning("Fichier dialogues aya2 absent : %s", path)
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("dialogues", data) if isinstance(data, dict) else data
        dialogues = []
        for item in items:
            if not item.get("question"):
                continue
            # variantes (« answers » : liste tirée au hasard au moment de
            # répondre) ou réponse unique (« answer »)
            if isinstance(item.get("answers"), list) and item["answers"]:
                dialogues.append(
                    {"question": item["question"], "answers": item["answers"]})
            elif item.get("answer"):
                dialogues.append(
                    {"question": item["question"], "answer": item["answer"]})
        logger.info("Dialogues Aya2 (ton africain pro) chargés : %s questions", len(dialogues))
        return dialogues
    except Exception as e:
        logger.error("Erreur de lecture des dialogues Aya2 : %s", e)
        return []

def _boot_knowledge_sources() -> None:
    """Appelé au démarrage : trace TOUTES les sources de connaissances."""
    load_knowledge_txt()


def load_language_resources(lang_code: str) -> dict[str, Any]:
    lang_path = LANG_DIR / lang_code
    resources: dict[str, Any] = {"kb": [], "faq": [], "dialogues": []}
    # Pack Aya2 (ton africain pro) propre à chaque langue
    resources["dialogues"].extend(load_aya2_dialogues(lang_code))

    kb_path = lang_path / "kb.json"
    if kb_path.is_file():
        try:
            with kb_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                resources["kb"] = data.get("knowledge", [])
                logger.info("[%s](kb.json) chargé : %s fiches", lang_code, len(resources["kb"]))
        except Exception as e:
            logger.error("[%s] Erreur kb.json : %s", lang_code, e)

    faq_path = lang_path / "faq.md"
    if faq_path.is_file():
        try:
            content = faq_path.read_text(encoding="utf-8")
            resources["faq"] = _parse_markdown_sections(content)
            logger.info("[%s](faq.md) chargé : %s questions", lang_code, len(resources["faq"]))
        except Exception as e:
            logger.error("[%s] Erreur faq.md : %s", lang_code, e)

    # 1. Fichier unique dialogues.md (compatibilité)
    dialogues_path = lang_path / "dialogues.md"
    if dialogues_path.is_file():
        try:
            content = dialogues_path.read_text(encoding="utf-8")
            resources["dialogues"].extend(_parse_markdown_sections(content))
            logger.info("[%s](dialogues.md) chargé : %s dialogues", lang_code, len(resources["dialogues"]))
        except Exception as e:
            logger.error("[%s] Erreur dialogues.md : %s", lang_code, e)

    # 2. Dossier dialogues/ (fichiers multiples — FIX : était ignoré pour en/es/ar)
    dialogues_dir = lang_path / "dialogues"
    if dialogues_dir.is_dir():
        loaded = 0
        for file_path in sorted(dialogues_dir.iterdir()):
            if file_path.is_file() and file_path.suffix in {".md", ".txt"}:
                try:
                    sections = _parse_markdown_sections(file_path.read_text(encoding="utf-8"))
                    resources["dialogues"].extend(sections)
                    loaded += len(sections)
                except Exception as e:
                    logger.error("[%s] Erreur %s : %s", lang_code, file_path.name, e)
        if loaded:
            logger.info("[%s](dialogues/) chargé : %s dialogues de %s fichiers",
                        lang_code, loaded, len(list(dialogues_dir.iterdir())))

    return resources

# ---------------------------------------------------------------------------
# Chargement initial de toutes les ressources
# ---------------------------------------------------------------------------

KB_DATA = load_knowledge_base()
BRAND = KB_DATA.get("brand", "Komara Agency")
BRAIN = KB_DATA
KNOWLEDGE = KB_DATA.get("knowledge", [])
WHATSAPP = KB_DATA.get("contact", {}).get("whatsapp", "")
LOCAL_FAQ = load_local_faq()
LOCAL_DIALOGUES = load_dialogues()

LANG_RESOURCES: dict[str, dict[str, Any]] = {}
for _lang in get_supported_languages():
    LANG_RESOURCES[_lang] = load_language_resources(_lang)

_fr_root_kb = KNOWLEDGE
_fr_lang_kb = LANG_RESOURCES.get("fr", {}).get("kb", [])
_fr_root_faq = LOCAL_FAQ
_fr_lang_faq = LANG_RESOURCES.get("fr", {}).get("faq", [])
_fr_root_dialogues = LOCAL_DIALOGUES
_fr_lang_dialogues = LANG_RESOURCES.get("fr", {}).get("dialogues", [])

LANG_RESOURCES["fr"] = {
    "kb": _fr_lang_kb + _fr_root_kb,
    "faq": _fr_lang_faq + _fr_root_faq,
    "dialogues": _fr_lang_dialogues + _fr_root_dialogues,
}
logger.info("Ressources [fr] fusionnées : %s fiches KB", len(LANG_RESOURCES["fr"]["kb"]))


def refresh_resources(lang_code: str) -> None:
    """Recharge une langue (après /kb_import) en réappliquant la fusion fr."""
    lang_res = load_language_resources(lang_code)
    if lang_code == "fr":
        lang_res = {
            "kb": lang_res.get("kb", []) + load_knowledge_base().get("knowledge", []),
            "faq": lang_res.get("faq", []) + load_local_faq(),
            "dialogues": lang_res.get("dialogues", []) + load_dialogues(),
        }
    LANG_RESOURCES[lang_code] = lang_res
    logger.info("Ressources [%s] rechargées : %s fiches KB", lang_code, len(lang_res["kb"]))

# ---------------------------------------------------------------------------
# Mémoire conversationnelle locale (SQLite avec connexion persistante)
# ---------------------------------------------------------------------------

# 💾 Persistance : sur Railway, si un volume est monté sur /data, la mémoire
# SURVIT aux redéploiements (sinon le conteneur reconstruit l'efface →
# « le bot ne se souvient de rien »). Migration auto de l'ancienne base.
_PERSIST_ROOT = Path("/data")
_DEFAULT_MEM_DIR = _PERSIST_ROOT if _PERSIST_ROOT.is_dir() else BASE_DIR / "data"
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", _DEFAULT_MEM_DIR))
MEMORY_FILE = MEMORY_DIR / "memory.db"
if MEMORY_DIR != BASE_DIR / "data":
    try:
        import shutil as _shutil
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if not MEMORY_FILE.exists() and (BASE_DIR / "data" / "memory.db").exists():
            _shutil.copy2(BASE_DIR / "data" / "memory.db", MEMORY_FILE)
            logger.info("Mémoire migrée vers le volume persistant : %s", MEMORY_FILE)
    except Exception:
        logger.warning("Migration mémoire impossible", exc_info=True)
MEMORY_LIMIT = 100  # longue mémoire : 100 derniers échanges par client

# ---------------------------------------------------------------------------
# Transcription vocale 100% locale (faster-whisper, aucune IA externe)
# ---------------------------------------------------------------------------
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VOCALS_DIR = Path(os.getenv("VOCALS_DIR", BASE_DIR / "data" / "vocals"))
KEEP_VOCALS = 100        # derniers vocaux reçus conservés sur disque
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")  # tiny/base/small selon RAM
WHISPER_MODEL_OBJ = None
WHISPER_LOCK = threading.Lock()

def get_whisper_model():
    """Charge faster-whisper une seule fois (modèle local, sans API)."""
    global WHISPER_MODEL_OBJ
    if WHISPER_MODEL_OBJ is not None:
        return WHISPER_MODEL_OBJ
    with WHISPER_LOCK:
        if WHISPER_MODEL_OBJ is None:
            from faster_whisper import WhisperModel  # import paresseux
            WHISPER_MODEL_OBJ = WhisperModel(
                WHISPER_MODEL_NAME, device="cpu", compute_type="int8"
            )
            logger.info("Whisper local prêt (modèle %s)", WHISPER_MODEL_NAME)
    return WHISPER_MODEL_OBJ

def _prune_vocals() -> None:
    """Ne garde que les KEEP_VOCALS derniers vocaux reçus."""
    try:
        files = sorted(VOCALS_DIR.glob("vocal_*"), key=lambda p: p.name, reverse=True)
        for old_file in files[KEEP_VOCALS:]:
            old_file.unlink(missing_ok=True)
    except Exception:
        pass


def transcribe_voice(bot, message) -> str | None:
    """Transcrit un vocal/audio Telegram en local. Retourne le texte ou None."""
    chat_id = message.chat.id
    try:
        voice = message.voice or message.audio
        file_info = bot.get_file(voice.file_id)
        file_data = bot.download_file(file_info.file_path)
        suffix = ".ogg" if message.voice else ".mp3"
        # Stockage durable : un dossier dédié garde les vocaux reçus
        stored_in_dir = False
        try:
            VOCALS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            tmp_path = str(VOCALS_DIR / f"vocal_{chat_id}_{ts}{suffix}")
            Path(tmp_path).write_bytes(file_data)
            _prune_vocals()
            stored_in_dir = True
        except Exception as e:
            logger.warning("Stockage vocal impossible, repli temporaire : %s", e)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name
        try:
            model = get_whisper_model()
            segments, _info = model.transcribe(tmp_path, beam_size=2)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text if text else None
        finally:
            # le vocal archivé dans VOCALS_DIR est CONSERVÉ ;
            # seul le repli temporaire est effacé.
            if not stored_in_dir:
                Path(tmp_path).unlink(missing_ok=True)
    except ImportError:
        logger.warning("faster-whisper non installé : vocaux indisponibles")
        return None
    except Exception as e:
        logger.error("Transcription vocale impossible : %s", e)
        return None
DB_LOCK = threading.Lock()
DB_CONN: sqlite3.Connection | None = None

def init_memory_db() -> None:
    try:
        _boot_knowledge_sources()
    except Exception as e:
        logger.error("Chargement des sources knowledge impossible : %s", e)
    global DB_CONN
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    DB_CONN = sqlite3.connect(MEMORY_FILE, check_same_thread=False)
    with DB_LOCK:
        DB_CONN.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                chat_id TEXT PRIMARY KEY,
                history TEXT NOT NULL
            )
        """)
        DB_CONN.commit()
    logger.info("Base de mémoire SQLite initialisée : %s", MEMORY_FILE)

def remember(chat_id: int, role: str, content: str) -> list[dict[str, str]]:
    global DB_CONN
    key = str(chat_id)
    content = content[:4000]

    with DB_LOCK:
        row = DB_CONN.execute("SELECT history FROM memory WHERE chat_id =?", (key,)).fetchone()
        history = json.loads(row[0]) if row else []

        history.append({"role": role, "content": content})
        if len(history) > MEMORY_LIMIT:
            history = history[-MEMORY_LIMIT:]

        DB_CONN.execute(
            "INSERT OR REPLACE INTO memory (chat_id, history) VALUES (?,?)",
            (key, json.dumps(history, ensure_ascii=False))
        )
        DB_CONN.commit()
    return history

def forget(chat_id: int) -> None:
    global DB_CONN
    with DB_LOCK:
        DB_CONN.execute("DELETE FROM memory WHERE chat_id =?", (str(chat_id),))
        DB_CONN.commit()

def context_for(chat_id: int) -> list[dict[str, str]]:
    global DB_CONN
    with DB_LOCK:
        row = DB_CONN.execute("SELECT history FROM memory WHERE chat_id =?", (str(chat_id),)).fetchone()
        if row:
            return json.loads(row[0])[-MEMORY_LIMIT:]
    return []

# ---------------------------------------------------------------------------
# Recherche multilingue
# ---------------------------------------------------------------------------

def trouver_meilleure_reponse_multilingue(message: str, detected_lang: str) -> str | None:
    resources = LANG_RESOURCES.get(detected_lang, {"kb": [], "faq": [], "dialogues": []})
    result = trouver_meilleure_reponse(
        message, resources["kb"], resources["faq"], resources["dialogues"]
    )
    if result:
        return result

    # ANTI-MÉLANGE : on ne balaie JAMAIS les autres langues. Avant, un
    # client EN sans fiche EN pouvait recevoir une réponse ES ou AR —
    # « le bot mélange les réponses ». Désormais : langue détectée →
    # repli FR (langue de la marque) → message générique DANS LA LANGUE
    # du client. Une absence de fiche vaut mieux qu'une réponse à côté.
    fallback = LANG_RESOURCES.get(DEFAULT_LANGUAGE, {"kb": [], "faq": [], "dialogues": []})
    result = trouver_meilleure_reponse(
        message, fallback["kb"], fallback["faq"], fallback["dialogues"]
    )
    if result:
        return result
    return None

# ---------------------------------------------------------------------------
# Réponses et interface Telegram
# ---------------------------------------------------------------------------

KEYBOARDS: dict[str, list[tuple[str, ...]]] = {
    "fr": [
        ("💎 Voir les Tarifs", "📂 Portfolio"),
        ("🚀 Commander", "🤖 Chatbot IA"),
        ("📅 Rendez-vous", "📄 Devis", "⭐ Avis"),
        ("📦 Suivi commande", "🎟️ Code promo"),
        ("🛍️ Catalogue", "🛒 Panier"),
        ("👑 Parler à un humain",),
    ],
    "en": [
        ("💎 View Pricing", "📂 Portfolio"),
        ("🚀 Order", "🤖 AI Chatbot"),
        ("📅 Book a call", "📄 Quote", "⭐ Feedback"),
        ("📦 Order Tracking", "🎟️ Promo Code"),
        ("🛍️ Catalogue", "🛒 Cart"),
        ("👑 Talk to a human",),
    ],
    "ar": [
        ("💎 الأسعار", "📂 المعرض"),
        ("🚀 طلب", "🤖 مساعد ذكي"),
        ("📅 حجز موعد", "📄 تسعيرة", "⭐ تقييم"),
        ("📦 تتبع الطلب", "🎟️ كود الخصم"),
        ("🛍️ كتالوج", "🛒 سلة"),
        ("👑 التحدث مع مستشار",),
    ],
    "es": [
        ("💎 Ver Precios", "📂 Portafolio"),
        ("🚀 Ordenar", "🤖 Chatbot IA"),
        ("📅 Reservar", "📄 Presupuesto", "⭐ Opinión"),
        ("📦 Seguimiento", "🎟️ Código Promo"),
        ("🛍️ Catálogo", "🛒 Carrito"),
        ("👑 Hablar con un humano",),
    ],
}

# FIX : comparer un libellé de bouton à une liste de tuples est toujours faux.
# On aplatit tous les libellés dans un set pour un test d'appartenance correct.
BUTTON_LABELS: set[str] = {
    label
    for rows in KEYBOARDS.values()
    for row in rows
    for label in row
}

START_COMMANDS: set[str] = {"/start", "/star", "/menu", "/help", "/ayuda", "/inicio"}

RESET_COMMANDS: dict[str, set[str]] = {
    "fr": {"/reset", "/forget", "oublie", "oublie-moi"},
    "en": {"/reset", "/forget", "forget", "reset"},
    "ar": {"/reset", "/forget", "نسيان", "مسح"},
    "es": {"/reset", "/forget", "olvida", "reiniciar"},
}

MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "reset": "D'accord, j'ai effacé le contexte de cette conversation. Que souhaitez-vous faire?",
        "fallback": "Je n'ai pas bien compris votre demande. 🤔\n\nNous proposons : bots WhatsApp/Telegram, sites web, applications, logos et création digitale.\n\nTapez 'prix' pour les tarifs, 'services' pour nos offres, ou décrivez votre projet.",
        "human": f"Expert KOMARA vous contacte sur *{WHATSAPP}* sous 5 minutes.",
        "portfolio": "Tu veux des exemples pour quel domaine?",
        "pricing_intro": "Voici nos offres :",
        "commander": "Super! 🛒 Pour préparer votre devis, dites-moi :\n\n1️⃣ Quel service? (bot, site, logo, app...)\n2️⃣ Votre activité\n3️⃣ Votre délai souhaité\n\nJe vous écoute 👇",
        "chatbot": "🤖 Vous voulez un bot intelligent pour votre business?\n\nOn crée des bots WhatsApp, Telegram et TikTok sur mesure.\n\nQuel canal vous intéresse?",
        "start": "Salut 👋 Bienvenue chez Komara Agency 🇬🇳 !\n\nJe crée des solutions digitales : chatbots, sites web, logos, visuels IA.\n\nChoisis une option 👇 ou décris ton besoin.",
        "welcome_back": "Re-bonjour {name} 👋 Content de te revoir chez Komara Agency 🇬🇳 !\n\nChoisis une option 👇 ou décris ton besoin.",
        "promo": "🎟️ Nos codes promo s'appliquent automatiquement au moment du devis.\n\n1. Tape 'devis'\n2. Décris ton besoin\n3. Entre ton code à l'étape demandée\n\nLes codes actifs sont annoncés ici par l'équipe 🇬🇳",
        "voice_received": "🎤 Ton vocal : «{text}»\n\nJe m'occupe de ta demande 👇",
"crash_fallback": "🛠️ Petit raté technique de mon côté — ta demande n'est pas perdue.\n\nRenvoie ton message, ou écris juste : site, logo, bot, visuel ou formation. Aya revient tout de suite 💪",
        "bot_closed": "🌙 Komara Agency 🇬🇳 est fermée pour le moment.\nMais pas de stress : laisse ton message ici, on te répond à l'ouverture 🙏\n\nEn attendant, découvre nos réalisations : /menu 😊",
        "voice_unavailable": "🎤 Je n'ai pas pu écouter ce vocal pour l'instant. Écris-moi ton message 🙏",
        "error": "Désolé, une erreur temporaire est survenue. Un expert KOMARA vous contacte.",
    },
    "en": {
        "reset": "Done, I've cleared the context. What would you like to do?",
        "fallback": "I didn't quite catch that. 🤔\n\nWe offer: WhatsApp/Telegram bots, websites, apps, logos and digital creation.\n\nType 'pricing' for rates, 'services' for our offers, or describe your project.",
        "human": f"A KOMARA expert will contact you on *{WHATSAPP}* within 5 minutes.",
        "portfolio": "What kind of examples are you looking for?",
        "pricing_intro": "Here are our offers:",
        "commander": "Great! 🛒 To prepare your quote, tell me:\n\n1️⃣ Which service? (bot, website, logo, app...)\n2️⃣ Your business\n3️⃣ Your preferred timeline\n\nI'm listening 👇",
        "chatbot": "🤖 Want a smart bot for your business?\n\nWe create custom WhatsApp, Telegram and TikTok bots.\n\nWhich channel interests you?",
        "start": "Hi 👋 Welcome to Komara Agency 🇬🇳!\n\nI create digital solutions: chatbots, websites, logos, AI visuals.\n\nPick an option 👇 or describe your need.",
        "welcome_back": "Hello again {name} 👋 Great to see you back at Komara Agency 🇬🇳!\n\nPick an option 👇 or describe your need.",
        "promo": "🎟️ Our promo codes apply automatically at quote time.\n\n1. Type 'quote'\n2. Describe your need\n3. Enter your code when asked\n\nActive codes are announced here by the team 🇬🇳",
        "voice_received": "🎤 Your voice: '{text}'\n\nTaking care of your request 👇",
"crash_fallback": "🛠️ Small technical hiccup on my side — your request is not lost.\n\nSend your message again, or just type: site, logo, bot, visual or training. Aya is right back 💪",
        "bot_closed": "🌙 Komara Agency 🇬🇳 is closed right now.\nNo stress: leave your message here, we reply at opening 🙏\n\nMeanwhile, check our work: /menu 😊",
        "voice_unavailable": "🎤 I couldn't listen to this voice note yet. Please type your message 🙏",
        "error": "Sorry, a temporary error occurred. A KOMARA expert will contact you.",
    },
    "ar": {
        "reset": "تم مسح السياق. ماذا تريد أن تفعل؟",
        "fallback": "لم أفهم طلبك تماماً. 🤔\n\nنقدم: بوتات واتساب/تيليجرام، مواقع، تطبيقات، شعارات وإنشاء رقمي.\n\nاكتب 'السعر' للأسعار، 'الخدمات' لعروضنا، أو صف مشروعك.",
        "human": f"سيتواصل معك خبير KOMARA على *{WHATSAPP}* خلال 5 دقائق.",
        "portfolio": "ما نوع الأمثلة التي تبحث عنها؟",
        "pricing_intro": "إليك عروضنا:",
        "commander": "رائع! 🛒 لإعداد عرض السعر، أخبرني:\n\n1️⃣ أي خدمة؟ (بوت، موقع، شعار، تطبيق...)\n2️⃣ نشاطك\n3️⃣ الموعد النهائي المفضل\n\nأستمع إليك 👇",
        "chatbot": "🤖 تريد بوت ذكي لعملك؟\n\nننشئ بوتات واتساب وتيليجرام وتيك توك مخصصة.\n\nأي قناة تهمك؟",
        "start": "مرحبا 👋 أهلا بك في Komara Agency 🇬🇳!\n\nأنشئ حلولا رقمية: بوتات ذكية، مواقع، شعارات، صور بالذكاء الاصطناعي.\n\nاختر خيارا 👇 أو صف احتياجك.",
        "welcome_back": "مرحبا بك مجددا {name} 👋 سعداء بعودتك إلى Komara Agency 🇬🇳!\n\nاختر خيارا 👇 أو صف احتياجك.",
        "promo": "🎟️ أكواد الخصم تُطبق تلقائيا عند الطلب التقديري.\n\n1. اكتب 'تسعيرة'\n2. صف احتياجك\n3. أدخل الكود في الخطوة المطلوبة\n\nالأكواد النشطة تُعلن هنا من الفريق 🇬🇳",
        "voice_received": "🎤 رسالتك الصوتية: «{text}»\n\nأتولى طلبك 👇",
        "crash_fallback": "🛠️ عطل تقني بسيط من جهتي — طلبك لم يفقد.\n\nأعد إرسال رسالتك، أو اكتب فقط: موقع، شعار، بوت، تصميم أو تكوين. آيا تعود حالاً 💪",
        "bot_closed": "🌙 كومارا أجنسلي 🇬🇳 مغلقة حاليا.\nلا تقلق: اترك رسالتك هنا، نرد عند الفتح 🙏\n\nفي الانتظار، اكتشف أعمالنا: /menu 😊",
        "voice_unavailable": "🎤 لم أستطع الاستماع لهذه الرسالة الصوتية الآن. اكتب لي رسالتك 🙏",
        "error": "عذراً، حدث خطأ مؤقت. سيتواصل معك خبير من KOMARA.",
    },
    "es": {
        "reset": "Listo, he borrado el contexto. ¿Qué quieres hacer?",
        "fallback": "No entendí bien tu solicitud. 🤔\n\nOfrecemos: bots de WhatsApp/Telegram, sitios web, aplicaciones, logos y creación digital.\n\nEscribe 'precio' para tarifas, 'servicios' para nuestras ofertas, o describe tu proyecto.",
        "human": f"Un experto de KOMARA te contactará en *{WHATSAPP}* en menos de 5 minutos.",
        "portfolio": "¿Qué tipo de ejemplos estás buscando?",
        "pricing_intro": "Aquí están nuestras ofertas:",
        "commander": "¡Genial! 🛒 Para preparar tu presupuesto, dime:\n\n1️⃣ ¿Qué servicio? (bot, sitio, logo, app...)\n2️⃣ Tu negocio\n3️⃣ Tu plazo preferido\n\nTe escucho 👇",
        "chatbot": "🤖 ¿Quieres un bot inteligente para tu negocio?\n\nCreamos bots de WhatsApp, Telegram y TikTok personalizados.\n\n¿Qué canal te interesa?",
        "start": "Hola 👋 ¡Bienvenido a Komara Agency 🇬🇳!\n\nCreo soluciones digitales: chatbots, sitios web, logos, visuales IA.\n\nElige una opción 👇 o describe tu necesidad.",
        "welcome_back": "Hola de nuevo {name} 👋 ¡Qué gusto verte otra vez en Komara Agency 🇬🇳!\n\nElige una opción 👇 o describe tu necesidad.",
        "promo": "🎟️ Nuestros códigos promo se aplican automáticamente en el presupuesto.\n\n1. Escribe 'presupuesto'\n2. Describe tu necesidad\n3. Introduce tu código cuando te lo pida\n\nLos códigos activos los anuncia aquí el equipo 🇬🇳",
        "voice_received": "🎤 Tu voz: '{text}'\n\nMe encargo de tu solicitud 👇",
"crash_fallback": "🛠️ Pequeño fallo técnico de mi parte — tu solicitud no se perdió.\n\nReenvía tu mensaje, o escribe: sitio, logo, bot, visual o formación. Aya vuelve enseguida 💪",
        "bot_closed": "🌙 Komara Agency 🇬🇳 está cerrada ahora.\nTranquilo: deja tu mensaje aquí, respondemos a la apertura 🙏\n\nMientras tanto, descubre nuestros trabajos: /menu 😊",
        "voice_unavailable": "🎤 No pude escuchar esta nota de voz. Escríbeme tu mensaje 🙏",
        "error": "Lo siento, ocurrió un error temporal. Un experto de KOMARA te contactará.",
    },
}

# ---------------------------------------------------------------------------
# Variantes de messages (random.choice : jamais 2 fois la même phrase)
# ---------------------------------------------------------------------------

WELCOME_VARIANTS_FR = [
    "Salam {nom} 🙏 Bienvenue chez Komara Agency 🇬🇳 On est là pour booster ton business. Tu cherches quoi aujourd'hui ?",
    "Ah {nom} ! Ça fait plaisir 😊 Ici c'est Komara Agency 🇬🇳 Dis-moi, comment je peux t'aider ?",
    "Bienvenue {nom} ! Tu es au bon endroit 🔥 Formation IA, création de visuels, ou création de bot ?",
]

WELCOME_BACK_VARIANTS_FR = [
    "Re-bonjour {nom} 👋 Content de te revoir chez Komara Agency 🇬🇳 Qu'est-ce qu'on fait aujourd'hui ?",
    "Ah, {nom} de retour 😊 Ton business attend. Nouveau projet, suivi, ou devis ?",
    "Ça faisait longtemps {nom} 👋 Prêt·e à booster ton business ? Formation, visuels, bot : dis-moi tout.",
]

FALLBACK_VARIANTS_FR = [
    "Hmm j'ai pas bien saisi 😅 Tu peux reformuler ? Ou clique sur un bouton ci-dessous 👇",
    "Désolé, je suis encore un petit robot, j'apprends 🙏 Tu voulais parler de quel service ?",
    "Aïe, je me suis perdu 😅 Envoie un message vocal si tu veux, ce sera plus simple pour moi.",
]

def menu_for_lang(lang: str) -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for row in KEYBOARDS.get(lang, KEYBOARDS["fr"]):
        keyboard.add(*row)
    return keyboard

def msg(lang: str, key: str) -> str:
    return MESSAGES.get(lang, MESSAGES["fr"]).get(key, MESSAGES["fr"].get(key, ""))

# ---------------------------------------------------------------------------
# Recherche contextuelle sécurisée
# ---------------------------------------------------------------------------

def local_contextual_response(chat_id: int, user_text: str, detected_lang: str) -> str | None:
    user_text = user_text[:4000].strip()
    if not user_text:
        return None

    # 1. Recherche sémantique directe
    direct_answer = trouver_meilleure_reponse_multilingue(user_text, detected_lang)
    if direct_answer:
        return direct_answer

    # 2. RÈGLE D'OR : comprendre la réponse du client AVANT toute chose.
    # Si le bot vient de terminer par une question, la réponse du client
    # suit presque TOUJOURS cette question. On cherche donc avec
    # (question du bot + réponse du client) → réponse cohérente et
    # contextuelle, pas une réponse hors-sujet.
    history = context_for(chat_id)
    last_bot_msg = next(
        (h.get("content", "") for h in reversed(history)
         if h.get("role") == "assistant" and h.get("content")),
        "")

    if significant_token_count(user_text) < 2:
        if "?" in last_bot_msg:
            qa_combined = f"{last_bot_msg} {user_text}"
            qa_answer = trouver_meilleure_reponse_multilingue(qa_combined, detected_lang)
            if qa_answer and qa_answer != last_bot_msg:
                return qa_answer
        # message court/ambigu sans question en attente : aucun
        # re-match hasardeux (bug boucle accueil corrigé précédemment)
        return None

    # 3. Recherche avec contexte de conversation (si assez de contenu propre)
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {user_text}".strip()
    # RÈGLE D'OR : si le bot vient de poser une question, la réponse du
    # client s'interprète DANS CE CONTEXTE (même si elle est complète).
    if "?" in last_bot_msg:
        combined_text = f"{combined_text} {last_bot_msg}".strip()

    contextual_answer = trouver_meilleure_reponse_multilingue(combined_text, detected_lang)
    if contextual_answer:
        return contextual_answer

    return None

# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

PORTFOLIO_DIR = BASE_DIR / "portfolio"
PORTFOLIO_BUTTON_PREFIX = "📷 "
PORTFOLIO_SENTINEL = "__SHOW_PORTFOLIO__"
PORTFOLIO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

def portfolio_images() -> list[tuple[str, Path]]:
    images: list[tuple[str, Path]] = []
    if not PORTFOLIO_DIR.is_dir():
        return images
    for entry in sorted(PORTFOLIO_DIR.iterdir()):
        if entry.is_file() and entry.suffix.lower() in PORTFOLIO_EXTENSIONS:
            # Le préfixe numérique ("01_", "02_"...) sert seulement à fixer
            # l'ordre alphabétique de tri ; on le masque à l'affichage.
            stem = re.sub(r"^\d+[_\-]", "", entry.stem)
            display_name = stem.replace("_", " ").replace("-", " ").strip()
            images.append((display_name, entry))
    return images

def portfolio_keyboard() -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [f"{PORTFOLIO_BUTTON_PREFIX}{name}" for name, _ in portfolio_images()]
    if buttons:
        for i in range(0, len(buttons), 2):
            keyboard.add(*buttons[i:i + 2])
    else:
        keyboard.add("💎 Tarifs", "🚀 Commander")
    return keyboard

def send_portfolio(chat_id: int, lang: str) -> None:
    images = portfolio_images()
    slogan = BRAIN.get("slogan", "") if BRAIN else ""
    if not images:
        text = f"Portfolio KOMARA 💎 {slogan}\n{msg(lang, 'portfolio')}\n⚠️ Aucune réalisation disponible pour le moment."
        bot.send_message(chat_id, text, reply_markup=menu_for_lang(lang))
        return
    # Toujours proposer un choix explicite (numéro OU titre) pour que le
    # client valide avant l'envoi de l'image, plutôt que de deviner.
    lines = ["Portfolio KOMARA 💎", ""]
    for i, (name, _) in enumerate(images, start=1):
        lines.append(f"{i}️⃣ {name}" if i <= 9 else f"{i}. {name}")
    lines.append("")
    lines.append("👉 Réponds avec le numéro ou le titre pour voir la réalisation.")
    bot.send_message(chat_id, "\n".join(lines), reply_markup=portfolio_keyboard())

def send_portfolio_image_by_index(chat_id: int, index: int, lang: str) -> bool:
    """Envoie l'image du portfolio à la position `index` (1-based)."""
    images = portfolio_images()
    if not (1 <= index <= len(images)):
        return False
    name, path = images[index - 1]
    try:
        with path.open("rb") as image_file:
            bot.send_photo(chat_id, image_file, caption=f"📷 {name}")
        bot.send_message(
            chat_id,
            "Une autre réalisation t'intéresse ? Numéro ou titre 👇",
            reply_markup=portfolio_keyboard(),
        )
        return True
    except Exception:
        logger.exception("Échec d'envoi de l'image portfolio %s", path.name)
        return False

def send_portfolio_image(chat_id: int, display_name: str, lang: str) -> bool:
    for i, (name, _path) in enumerate(portfolio_images(), start=1):
        if name.lower() == display_name.lower():
            return send_portfolio_image_by_index(chat_id, i, lang)
    return False

# ---------------------------------------------------------------------------
# Handler principal des messages
# ---------------------------------------------------------------------------

def safe_typing(chat_id: int) -> None:
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        logger.debug("Impossible d'envoyer l'indicateur typing.", exc_info=True)

# ⏱️ PAUSE HUMAINE : un vrai vendeur ne répond pas à la microseconde.
# Indicateur « tape… » visible + réflexion aléatoire de 2 à 3,5 s →
# réponse totale toujours < 4 s. Le bot paraît naturel, pas machine.
def human_pause(chat_id: int) -> None:
    safe_typing(chat_id)
    time.sleep(random.uniform(2.0, 3.5))

def _handle_message(message: telebot.types.Message) -> None:
    chat_id = message.chat.id

    # 🔒 Pause admin (/pause) : le bot est fermé — seul l'admin passe
    # (pour /reprend ou toute commande de gestion). Les clients
    # reçoivent le message de fermeture, texte, vocal, doc : tout.
    if actions.is_paused() and chat_id != actions.ADMIN_CHAT_ID:
        try:
            lang = detect_language(getattr(message, "text", "") or "")
        except Exception:
            lang = "fr"
        safe_typing(chat_id)
        bot.send_message(chat_id, msg(lang, "bot_closed"),
                         reply_markup=menu_for_lang(lang))
        return

    # Position partagée → infos de livraison (OpenStreetMap, sans clé)
    if message.location:
        safe_typing(chat_id)
        osm_maps.handle_location(bot, message.location.latitude,
                                 message.location.longitude,
                                 _LAST_LANG.get(chat_id, "fr"))
        return

    # Document (admin) → /kb_import : enrichissement de la base de connaissances
    if message.document:
        detected_lang = detect_language(message.caption or "") if message.caption else "fr"
        safe_typing(chat_id)
        kb_import.handle_document(bot, message, detected_lang)
        return

    # Vocal/audio → transcription 100% locale (Whisper embarqué)
    if not message.text and (message.voice or message.audio):
        safe_typing(chat_id)
        text = transcribe_voice(bot, message) if VOICE_ENABLED else None
        if not text:
            bot.send_message(chat_id, msg(detect_language("fr"), "voice_unavailable"))
            return
        detected_lang = detect_language(text)
        bot.send_message(
            chat_id,
            msg(detected_lang, "voice_received").replace("{text}", text[:200]),
        )
        _process_text(chat_id, text, detected_lang, reply_voice=True)
        return

    if not message.text:
        return
    user_text = message.text.strip()
    # 🧠 MÉMOIRE CLIENT : le bot apprend qui parle (nom Telegram) dès le
    # premier message et le garde en base → il « se souvient du client ».
    try:
        _fu = getattr(message, "from_user", None)
        if _fu is not None and chat_id != actions.ADMIN_CHAT_ID:
            _known = actions.get_client(chat_id)
            if not (_known and _known.get("name")):
                actions.upsert_client(
                    chat_id,
                    name=(getattr(_fu, "first_name", "") or "")[:100],
                    event="première visite (auto)")
    except Exception:
        logger.debug("Enregistrement client impossible", exc_info=True)
    _process_text(chat_id, user_text, detect_language(user_text))


_LAST_LANG: dict[int, str] = {}


@bot.message_handler(func=lambda message: True, content_types=['text', 'voice', 'audio', 'document', 'location'])
def handle_message(message: telebot.types.Message) -> None:
    """Point d'entrée enregistré. ANTI-SILENCE : aucune exception ne sort
    jamais d'ici sans que le client reçoive une réponse de secours."""
    try:
        return _handle_message(message)
    except Exception as e:  # filet de sécurité absolu
        logger.error("CRASH géré dans handle_message : %s", e, exc_info=True)
        try:
            chat_id = message.chat.id
            lang = detect_language(getattr(message, "text", "") or "")
            remember(chat_id, "assistant", msg(lang, "crash_fallback"))
            bot.send_message(chat_id, msg(lang, "crash_fallback"),
                             reply_markup=menu_for_lang(lang))
        except Exception as fatal:
            # le fallback lui-même a échoué (réseau ?) : on log, le polling
            # continue de tourner — jamais de silence définitif.
            logger.error("Fallback final impossible (chat=%s) : %s",
                         getattr(getattr(message, "chat", None), "id", "?"), fatal)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call: telebot.types.CallbackQuery) -> None:
    """Clics sur les boutons inline (catalogue). ANTI-SILENCE : le spinner
    Telegram est toujours levé, même en cas d'erreur."""
    chat_id = call.message.chat.id if call.message else None
    lang = _LAST_LANG.get(chat_id, "fr") if chat_id else "fr"
    try:
        catalogue.handle_callback(bot, call, lang)
    except Exception as e:
        logger.error("CRASH géré dans handle_callback_query : %s", e, exc_info=True)
        try:
            bot.answer_callback_query(call.id, "⚠️ " + msg(lang, "crash_fallback")[:180])
        except Exception as fatal:
            logger.error("Fallback callback impossible (chat=%s) : %s", chat_id, fatal)


# 🧠 Clients déjà salués depuis ce redémarrage (une salutation perso par session)
_GREETED_SESSION: set[int] = set()

_RETURN_GREETING = {
    "fr": "Re-bonjour {name} 👋 Content de te revoir ! On parlait de quoi déjà ? Tape /menu si tu veux tout revoir.",
    "en": "Hello again {name} 👋 Great to see you back! What were we working on? Type /menu to see everything.",
    "es": "¡Hola de nuevo, {name} 👋 Me alegra verle otra vez! ¿En qué nos quedamos? Escriba /menu para ver todo.",
    "ar": "مرحباً بعودتك {name} 👋 سعيد برؤيتك مجدداً! على ماذا توقفنا؟ اكتب /menu لرؤية كل شيء.",
}

def _process_text(chat_id: int, user_text: str, detected_lang: str,
                    reply_voice: bool = False) -> None:
    _LAST_LANG[chat_id] = detected_lang
    # 🧠 MÉMOIRE CLIENT : un client connu qui salue reçoit une salutation
    # PERSONNALISÉE avec son prénom — le bot se souvient de lui.
    low_txt = user_text.lower().strip()
    if (chat_id not in _GREETED_SESSION
            and low_txt in actions.GREETING_WORDS):
        _GREETED_SESSION.add(chat_id)
        _client = actions.get_client(chat_id)
        if _client and _client.get("name"):
            remember(chat_id, "user", user_text)
            _greet = (_RETURN_GREETING.get(detected_lang)
                      or _RETURN_GREETING["fr"]).replace("{name}", _client["name"])
            remember(chat_id, "assistant", _greet)
            human_pause(chat_id)
            bot.send_message(chat_id, _greet,
                             reply_markup=menu_for_lang(detected_lang))
            return
    # 💬 Indicateur « tape… » + pause humaine 2-3,5 s AVANT toute réponse
    # (commandes, boutons, portfolio, KB, images) : le client voit le bot
    # « réfléchir » comme un vrai conseiller, et reçoit sa réponse < 4 s.
    human_pause(chat_id)
    # Livraison : "livraison <adresse>" → OpenStreetMap (gratuit, sans clé)
    delivery_addr = osm_maps.is_delivery_intent(user_text)
    if delivery_addr:
        osm_maps.handle_text(bot, chat_id, delivery_addr, detected_lang)
        return


    # 0. Commandes de démarrage/assistance (/start, /menu, /help)
    if user_text.lower() in START_COMMANDS:
        client = actions.get_client(chat_id)
        name = (client or {}).get("name") or ""
        if detected_lang == "fr":
            # random.choice : jamais 2 fois le même accueil
            pool = WELCOME_BACK_VARIANTS_FR if name else WELCOME_VARIANTS_FR
            welcome = random.choice(pool).replace("{nom}", name or "toi")
        elif client and client["name"]:
            welcome = msg(detected_lang, "welcome_back").replace("{name}", client["name"])
        else:
            welcome = msg(detected_lang, "start")
        bot.send_message(chat_id, welcome, reply_markup=menu_for_lang(detected_lang))
        return

    # 1. Commandes de reset
    if user_text.lower() in RESET_COMMANDS.get(detected_lang, set()):
        forget(chat_id)
        bot.send_message(chat_id, msg(detected_lang, "reset"), reply_markup=menu_for_lang(detected_lang))
        return

    # 1bis. Flux exécutables (commande, RDV, devis, lead, sondage) — 100% local
    if actions.handle(bot, chat_id, user_text, detected_lang):
        return

    # 1ter. Compétences locales 100% offline : calculatrice express,
    # date/heure — avant la recherche KB pour une réponse instantanée.
    if skills.handle(bot, chat_id, user_text, detected_lang):
        return

    # 2. Gestion des boutons rapides (FIX : test sur le set aplati BUTTON_LABELS)
    if user_text in BUTTON_LABELS:
        if "Suivi" in user_text or "Tracking" in user_text or "Seguimiento" in user_text or "تتبع" in user_text:
            actions.order_tracking(bot, chat_id, detected_lang)
            return
        if "promo" in user_text.lower() or "خصم" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "promo"), reply_markup=menu_for_lang(detected_lang))
            return
        if "Commander" in user_text or "Order" in user_text or "طلب" in user_text or "Ordenar" in user_text:
            # Panier non vide → tunnel catalogue
            if catalogue.start_checkout(bot, chat_id, detected_lang):
                return
            bot.send_message(chat_id, msg(detected_lang, "commander"), reply_markup=menu_for_lang(detected_lang))
            return
        if "Catalogue" in user_text or "Catálogo" in user_text or "كتالوج" in user_text:
            catalogue.show_catalogue(bot, chat_id, detected_lang)
            return
        if "Panier" in user_text or "Cart" in user_text or "Carrito" in user_text or "سلة" in user_text:
            catalogue.show_cart(bot, chat_id, detected_lang)
            return
        if "Chatbot" in user_text or "IA" in user_text or "ذكي" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "chatbot"), reply_markup=menu_for_lang(detected_lang))
            return
        if "humain" in user_text.lower() or "human" in user_text.lower() or "مستشار" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "human"), reply_markup=menu_for_lang(detected_lang), parse_mode="Markdown")
            return
        if "Portfolio" in user_text or "المعرض" in user_text or "Portafolio" in user_text:
            send_portfolio(chat_id, detected_lang)
            return
        if "Tarif" in user_text or "Pricing" in user_text or "السعر" in user_text or "Precio" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "pricing_intro"), reply_markup=menu_for_lang(detected_lang))
            return

    # 3. Gestion du Portfolio image — par titre (bouton 📷 nom)
    if user_text.startswith(PORTFOLIO_BUTTON_PREFIX):
        display_name = user_text[len(PORTFOLIO_BUTTON_PREFIX):]
        if not send_portfolio_image(chat_id, display_name, detected_lang):
            send_portfolio(chat_id, detected_lang)
        return

    # 3bis. Gestion du Portfolio image — par numéro simple ("1", "2️⃣ 2"...)
    # Permet au client de valider son choix en tapant juste le chiffre
    # affiché dans la liste envoyée par send_portfolio().
    numero_match = re.fullmatch(r"[1-9]️?⃣?\.?\s*", user_text)
    if numero_match and portfolio_images():
        digits = re.sub(r"[^\d]", "", user_text)
        if digits:
            index = int(digits)
            if send_portfolio_image_by_index(chat_id, index, detected_lang):
                return
            # numéro hors plage -> on réaffiche la liste plutôt que de deviner
            send_portfolio(chat_id, detected_lang)
            return

    # 4. Traitement normal
    safe_typing(chat_id)
    remember(chat_id, "user", user_text)

    # 5. Recherche locale uniquement (DeepSeek retiré définitivement) :
    # kb.json + FAQ + dialogues multilingues, mémoire SQLite pour le contexte.
    # Génération d'images simple (/image ou « génère une image de … »)
    if img_gen.handle_image_request(bot, chat_id, user_text, detected_lang):
        return

    # Garde anti-divulgation : jamais de clés, IDs, algorithme ou conception
    if is_secret_probe(user_text, detected_lang):
        secret_reply(bot, chat_id, detected_lang)
        return

    local_response = local_contextual_response(chat_id, user_text, detected_lang)
    if local_response is None:
        record_unrecognized(user_text, source="telegram")

    # BUG corrigé : un client qui tape "Portfolio"/"vos exemples" au clavier
    # (au lieu de cliquer le bouton "📂 Portfolio") recevait une réponse
    # texte générique au lieu des VRAIES images. Le kb.json marque ces
    # questions avec un sentinel PORTFOLIO_SENTINEL ; le scoring sémantique
    # existant (qui distingue déjà bien "vos exemples" de "montre moi un
    # exemple de bot") décide, puis on redirige ici vers le vrai portfolio.
    if local_response == PORTFOLIO_SENTINEL:
        send_portfolio(chat_id, detected_lang)
        return

    if local_response:
        response = local_response
    elif detected_lang == "fr":
        # random.choice : jamais 2 fois le même « je n'ai pas compris »
        response = random.choice(FALLBACK_VARIANTS_FR)
    else:
        response = msg(detected_lang, "fallback")

    # 6. Sauvegarde et envoi
    remember(chat_id, "assistant", response)

    if len(response) > 4096:
        for i in range(0, len(response), 4096):
            chunk = response[i:i + 4096]
            is_last = (i + 4096 >= len(response))
            bot.send_message(chat_id, chunk, reply_markup=menu_for_lang(detected_lang) if is_last else None)
    else:
        bot.send_message(chat_id, response, reply_markup=menu_for_lang(detected_lang))

    # Réponse parlée si le client a écrit en vocal (synthèse vocale locale)
    if reply_voice:
        tts.reply_with_voice(bot, chat_id, response, detected_lang)

# ---------------------------------------------------------------------------
# Garde anti-divulgation : jamais de clés, IDs, algorithme ou conception
# ---------------------------------------------------------------------------
SECRET_TRIGGERS: dict[str, set[str]] = {
    "fr": {
        "code source", "ton code source", "montre ton code", "montre moi ton code",
        "le code du bot", "code du bot", "ton algorithme", "ton prompt",
        "prompt système", "system prompt", "clé api", "api key", "ta clé api",
        "tes instructions", "tes instructions internes", "qui t'a programmé",
        "qui t'a créé", "qui t'a developpé", "t es programmé", "tu es programmé",
        "comment tu es programmé", "comment tu fonctionnes", "comment tu marches",
        "ton github", "github", "railway", "ton architecture", "ton back end",
        "ton backend", "ta base de données", "ton serveur", "ton modèle de langage",
        "tu utilises chatgpt", "tu utilises gemini", "tu utilises openai",
        "tu utilises gpt", "quelle ia tu utilises", "ton ia c est quoi",
    },
    "en": {
        "source code", "your source code", "show me your code", "the bot's code",
        "your algorithm", "your prompt", "system prompt", "api key",
        "your instructions", "who programmed you", "who built you",
        "how are you programmed", "how do you function", "how do you work internally",
        "your github", "github", "railway", "your architecture", "your backend",
        "your database", "your server", "your language model",
        "do you use chatgpt", "do you use gemini", "do you use openai",
        "which ai do you use",
    },
    "es": {
        "código fuente", "tu código fuente", "muéstrame tu código", "código del bot",
        "tu algoritmo", "tu prompt", "prompt del sistema", "clave api",
        "tus instrucciones", "quién te programó", "quién te creó",
        "cómo estás programado", "cómo funcionas por dentro", "cómo funcionas",
        "tu github", "github", "railway", "tu arquitectura", "tu backend",
        "tu base de datos", "tu servidor", "tu modelo de lenguaje",
        "usas chatgpt", "usas gemini", "usas openai", "qué ia usas",
    },
    "ar": {
        "الكود المصدري", "أرني كودك", "كود البوت", "خوارزميتك", "برمجتك",
        "من برمجك", "من صنعك", "مفتاح api", "تعليماتك الداخلية",
        "كيف تعمل داخليا", "كيف تمت برمجتك", "سيرفرك", "قاعدة بياناتك",
        "جيت هاب", "هل تستخدم شات جي بي تي", "أي ذكاء اصطناعي تستخدم",
    },
}

SECRET_REPLIES: dict[str, str] = {
    "fr": ("😄 Je suis Aya, l'assistante digitale de Komara Agency 🇬🇳\n\n"
           "Les coulisses techniques, c'est le secret de la maison — comme la "
           "recette d'un bon riz sauce 🍲\n\nCe que je peux faire pour toi, "
           "en revanche : sites, logos, visuels, chatbots, formation. "
           "Tu as un projet en tête ?"),
    "en": ("😄 I'm Aya, Komara Agency's digital assistant 🇬🇳\n\n"
           "The technical backstage is the house's secret — like a chef's "
           "signature recipe 🍲\n\nWhat I can do for you though: websites, "
           "logos, visuals, chatbots, training. Do you have a project in mind?"),
    "es": ("😄 Soy Aya, la asistente digital de Komara Agency 🇬🇳\n\n"
           "Los bastidores técnicos son el secreto de la casa — como la receta "
           "de un buen arroz con salsa 🍲\n\nLo que sí puedo hacer por ti: "
           "sitios, logos, visuales, chatbots, formación. ¿Tienes un proyecto en mente?"),
    "ar": ("😄 أنا آيا، المساعدة الرقمية لوكالة كومارا 🇬🇳\n\n"
           "التفاصيل التقنية سر البيت — مثل وصفة أرز بالصلصة اللذيذة 🍲\n\n"
           "لكن ما أستطيع فعله من أجلك: مواقع، شعارات، تصاميم، بوتات، تكوين. "
           "هل لديك مشروع في ذهنك؟"),
}


def is_secret_probe(text: str, lang: str) -> bool:
    """Vrai si le message cherche à extraire des internes techniques."""
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    triggers = SECRET_TRIGGERS.get(lang, SECRET_TRIGGERS["fr"])
    # aussi les déclencheurs des autres langues (client peut mélanger)
    for trigs in SECRET_TRIGGERS.values():
        if any(t in low for t in trigs):
            return True
    return False


def secret_reply(bot_, chat_id: int, lang: str) -> None:
    """Réponse de marque, chaleureuse, sans rien divulguer."""
    remember(chat_id, "assistant", SECRET_REPLIES.get(lang, SECRET_REPLIES["fr"]))
    bot_.send_message(
        chat_id,
        SECRET_REPLIES.get(lang, SECRET_REPLIES["fr"]),
        reply_markup=menu_for_lang(lang),
    )


# ---------------------------------------------------------------------------
# Monitoring et Cycle de vie
# ---------------------------------------------------------------------------

def heartbeat_loop() -> None:
    while not HEARTBEAT_STOP.is_set():
        if MONITOR_API_URL and MONITOR_API_KEY:
            try:
                payload = json.dumps({"status": "ok", "brand": BRAND}).encode()
                req = urllib.request.Request(
                    f"{MONITOR_API_URL}/heartbeat",
                    data=payload,
                    headers={"Authorization": f"Bearer {MONITOR_API_KEY}", "Content-Type": "application/json"},
                    method="POST"
                )
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                logger.debug("Échec du heartbeat : %s", e)
        HEARTBEAT_STOP.wait(HEARTBEAT_INTERVAL)

def prepare_polling() -> None:
    try:
        # drop_pending conforme à DROP_PENDING_UPDATES (conservé de l'ancienne version)
        bot.delete_webhook(drop_pending_updates=DROP_PENDING_UPDATES)
        logger.info("Webhook supprimé avec succès.")
    except Exception as e:
        logger.warning("Erreur lors de la suppression du webhook : %s", e)

# ---------------------------------------------------------------------------
# Boucle de Polling Robuste
# ---------------------------------------------------------------------------

_shutdown_requested = False

def is_conflict(error: ApiTelegramException) -> bool:
    description = str(getattr(error, "description", error)).casefold()
    return getattr(error, "error_code", None) == 409 or "terminated by other" in description

CONFLICT_BASE_DELAY = 15
CONFLICT_MAX_RETRIES = 8

def run() -> None:
    global _shutdown_requested

    init_memory_db()
    actions.start_background(bot)

    retry_count = 0
    conflict_count = 0
    threading.Thread(target=heartbeat_loop, name="worker-heartbeat", daemon=True).start()

    logger.info(
        "%s démarrage (multilingue : %s) ; polling_timeout=%ss, long_polling_timeout=%ss, "
        "drop_pending_updates=%s",
        BRAND, ", ".join(get_supported_languages()), POLL_TIMEOUT, LONG_POLLING_TIMEOUT, DROP_PENDING_UPDATES
    )

    while not _shutdown_requested:
        try:
            prepare_polling()
            logger.info("Worker Telegram actif avec une seule instance attendue.")
            bot.infinity_polling(
                timeout=POLL_TIMEOUT,
                long_polling_timeout=LONG_POLLING_TIMEOUT,
                skip_pending=DROP_PENDING_UPDATES,
                allowed_updates=["message", "callback_query"],
            )
            retry_count = 0
            if not _shutdown_requested:
                logger.warning("Le polling s'est arrêté sans exception ; nouvelle tentative différée.")
                time.sleep(5)

        except ApiTelegramException as error:
            if is_conflict(error):
                # Conflit 409 : pendant un redéploiement Railway, l'ancienne
                # instance garde le token ~1-2 min. On patiente (backoff
                # progressif) au lieu de crasher en boucle.
                conflict_count += 1
                if conflict_count > CONFLICT_MAX_RETRIES:
                    logger.critical(
                        "Conflit Telegram 409 persistant après %s tentatives. Arrêt.",
                        conflict_count - 1,
                    )
                    raise SystemExit(2) from error
                delay = min(120, CONFLICT_BASE_DELAY * conflict_count)
                logger.warning(
                    "Conflit Telegram 409 (tentative %s/%s) : reprise dans %ss.",
                    conflict_count, CONFLICT_MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue

            conflict_count = 0
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.critical("Trop d'erreurs Telegram consécutives ; arrêt du worker.")
                raise
            delay = min(60, 2 ** min(retry_count, 6))
            logger.exception(
                "Erreur Telegram transitoire (tentative %s/%s) ; reprise dans %ss.",
                retry_count, MAX_RETRIES, delay,
            )
            time.sleep(delay)

        except Exception:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.critical("Trop d'erreurs consécutives ; arrêt du worker.")
                raise
            delay = min(60, 2 ** min(retry_count, 6))
            logger.exception(
                "Erreur inattendue du polling (tentative %s/%s) ; reprise dans %ss.",
                retry_count, MAX_RETRIES, delay,
            )
            time.sleep(delay)

    logger.info("Worker Telegram arrêté proprement.")

def handle_sigterm(signum, frame):
    global _shutdown_requested
    logger.info("Signal d'arrêt reçu (%s). Fermeture en cours...", signum)
    _shutdown_requested = True
    HEARTBEAT_STOP.set()
    if DB_CONN:
        DB_CONN.close()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)
    # Serveur de callback OAuth Google (Sheets + Contacts) si configuré
    google_link.run_oauth_server()
    # Rapport hebdo chaque lundi 09h00 (heure Guinée) → admin
    threading.Thread(target=weekly_report.scheduler_loop, args=(bot,),
                     name="weekly-report", daemon=True).start()
    # Sauvegarde automatique de la base → Google Drive chaque nuit 03h00
    threading.Thread(target=backup_drive.scheduler_loop,
                     name="drive-backup", daemon=True).start()
    run()
