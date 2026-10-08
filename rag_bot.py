"""Worker Telegram Komara avec polling robuste, multilingue et mémoire locale (SQLite)."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import signal
import sqlite3
import string
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
import list_context
import knowledge_store
import qr_module
import relances
import commercial_cron
import catalogue
import kb_import
import google_link
import portfolio_drive
import aya_seed
import osm_maps
import backup_drive
import weekly_report
import aya_eval_cron
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

# 🚫 RÈGLE D'OR (29/09) : le bot n'envoie jamais 2 messages à la fois.
# La notice « bureau fermé » (1x/jour, actions.maybe_off_hours_notice) ne
# doit donc jamais partir seule : ce wrapper la FUSIONNE automatiquement
# dans le TOUT PROCHAIN texte envoyé à ce chat, quel que soit le module
# (actions.py, catalogue.py, rag_bot.py...) qui appelle bot.send_message —
# un seul point de passage, donc une seule protection suffit pour tous.
_ORIGINAL_SEND_MESSAGE = bot.send_message

def _send_message_merge_offhours(chat_id, text=None, *args, **kwargs):
    try:
        notice = actions._PENDING_OFFHOURS.pop(str(chat_id), None)
    except Exception:
        notice = None
    if notice and isinstance(text, str) and text:
        text = f"{notice}\n\n{text}"
    return _ORIGINAL_SEND_MESSAGE(chat_id, text, *args, **kwargs)

bot.send_message = _send_message_merge_offhours

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
            # LETTRE MASTER (29/09) — fix détection : « J'ai plusieurs
            # articles a vendre » partait en EN (« a » = mot anglais x2).
            # Mots français ultra-courants + contractions apostrophe.
            "ai", "suis", "est", "plusieurs", "besoin", "vendre", "vends",
            "souhaite", "voudrais", "veux", "peux", "article", "articles",
            "produit", "produits", "commander", "acheter", "fais", "fait",
        },
        "patterns": ["'", "œ", "à", "é", "è", "ê", "ë", "î", "ï", "ô", "ù", "û", "ü", "ç",
                      # contractions typiquement françaises (j'ai, n', qu'...)
                      "j'", "n'", "qu'", "l'", "d'", "c'", "s'", "t'", "m'"],
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
            # Lot 21 : "tu" SANS accent retiré — mot français identique
            # (« tu es humain ? ») qui faisait basculer en espagnol par
            # erreur. "tú" (accentué) reste un marqueur ES fiable.
            "mi", "su", "nuestro", "este", "esta", "estos", "que", "qué",
            "quien", "cómo", "por", "cuándo", "dónde", "con", "sin", "para",
            "pero", "sí", "no", "gracias", "hola", "precio", "costo", "cuánto",
            "quiero", "puede", "hacen", "servicio", "bot", "sitio", "aplicación",
        },
        "patterns": ["ñ", "á", "é", "í", "ó", "ú", "ü", "¿", "¡"],
    },
}

DEFAULT_LANGUAGE = "fr"
MIN_CONFIDENCE = 2

# Lot 21 : question PERSONNELLE vers le bot (« comment tu vas », « ça
# va ? », « how are you »...) — jamais mélangée au contexte : elle parle
# du bot/client, pas du sujet en cours. On la laisse au match direct
# (la KB a ses fiches) ; sans fiche → fallback honnête, pas un match
# hasardeux sur le message précédent (bug « Comment tu vas » répondait
# une fiche bot WhatsApp sans aucun rapport).
PERSONAL_QUESTION_RE = re.compile(
    r"comment\s+tu\s+vas|vas[- ]tu|tu\s+vas\s+(bien|aujourd|ce)|"
    r"\bça\s+va\b|\bca\s+va\b|\bcv\b|tu\s+dors|tu\s+manges|"
    r"tu\s+fais\s+quoi|ton\s+(nom|prénom|âge)|"
    r"how\s+are\s+you|how\s+ru|hru\b|what\s+s\s+your\s+name|"
    r"c[oó]mo\s+est[aá]s|qu[eé]\s+tal|"
    r"كيف\s+حالك|أخبارك|شو\s+أخبارك|عامل\s+إيه",
    re.IGNORECASE,
)


# Lot 21 : caractères invisibles fréquents dans le texte copié-collé
# depuis WhatsApp/iOS (marqueurs de direction, espaces zéro-largeur, BOM).
# Sans nettoyage ils cassent la reconnaissance des commandes (« /apprends »
# devient « \u200e/apprends », plus jamais reconnu) et corrompent le
# scoring KB. Retiré au tout premier point d'entrée du texte utilisateur.
_INVISIBLE_CHARS_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\ufeff\u2060]")

def strip_invisible_chars(text: str) -> str:
    if not text:
        return text
    return _INVISIBLE_CHARS_RE.sub("", text)


def detect_language_confident(text: str) -> str | None:
    """Détection avec signal réel. Retourne None si le message ne porte
    AUCUN marqueur fiable (émoji seul, « ok », ponctuation...) : l'appelant
    réutilise alors la dernière langue du chat au lieu de repartir en FR
    (LETTRE MASTER : la langue choisie par le client PERSISTE)."""
    if not text or not text.strip():
        return None

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
        return None

    best_lang = max(scores, key=scores.get)
    return best_lang if scores[best_lang] >= MIN_CONFIDENCE else None


def detect_language(text: str) -> str:
    """API historique : FR par défaut si aucun signal (comportement
    inchangé pour les 344 tests existants)."""
    return detect_language_confident(text) or DEFAULT_LANGUAGE


# LETTRE MASTER — moteur langue (29/09) : marqueurs darija « salam /
# khoya » → arabe, « hola / quiero » → espagnol — MAIS sans casser les
# clients francophones qui saluent en « Salam » : le basculement AR ne
# se fait que si le message ne contient AUCUN mot français à part
# « salam/khoya » eux-mêmes (« Salam » seul → ar ; « Salam, je veux un
# site » → reste fr).
_DARIJA_MARKERS = {"khoya", "salam", "nta", "siri", "wlah", "walid", "bghit"}


def detect_language_session(text: str, fallback: str | None = None) -> str:
    """Détection pour la conversation : priorise le message, sinon garde
    la langue persistée du chat (fallback), sinon FR.

    Darija PRIORITAIRE (lettre : « salam/khoya » -> arabe) mais sans casser
    les clients francophones qui saluent en « Salam » : le basculement AR
    ne se fait que si le message ne contient AUCUN mot français réel à part
    les marqueurs darija et les emprunts internationaux (bot, site...).
    « Salam » seul -> ar ; « Salam, je veux un site web » -> reste fr."""
    low_words = set(re.findall(r"\b\w+\b", text.lower()))
    fr_words = LANGUAGE_MARKERS["fr"].get("words", set())
    # emprunts internationaux : présents dans toutes les langues, ils ne
    # comptent ni comme signal FR ni comme signal darija
    loan_words = {"bot", "site", "app", "service", "whatsapp", "telegram",
                  "logo", "menu", "web"}
    darija_hit = bool(low_words & _DARIJA_MARKERS)
    strong_fr = len((low_words & fr_words) - loan_words - _DARIJA_MARKERS)
    if darija_hit and strong_fr == 0:
        return "ar"
    detected = detect_language_confident(text)
    if detected is not None:
        return detected
    if "hola" in low_words or "quiero" in low_words:
        return "es"
    return fallback or DEFAULT_LANGUAGE

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
    """RÈGLE BOSS (02/10) : le corpus de dialogues a été ENTIÈREMENT retiré
    du repo (aucune donnée sur GitHub). kb.json peut être absent : la base
    est alors vide, le boss enseigne tout via /apprends (Google Sheets)."""
    if not KB_PATH.is_file():
        logger.warning("kb.json absent — base vide : le bot s'apprend via /apprends")
        return {"knowledge": []}
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



def _load_merged_resources(lang_code: str) -> dict[str, Any]:
    """Build a fresh language resource set for the shared knowledge store."""
    lang_res = load_language_resources(lang_code)
    if lang_code == "fr":
        lang_res = {
            "kb": lang_res.get("kb", []) + load_knowledge_base().get("knowledge", []),
            "faq": lang_res.get("faq", []) + load_local_faq(),
            "dialogues": lang_res.get("dialogues", []) + load_dialogues(),
        }
    return lang_res


LANG_RESOURCES = knowledge_store.initialize_resources(LANG_RESOURCES, _load_merged_resources)
try:
    _seed = aya_seed.ensure_seed("fr")
    logger.info("Seed Aya : %s Q/R chargées (%s persistées)",
                _seed["loaded"], _seed["persisted"])
except Exception:
    logger.warning("Seed Aya non chargée", exc_info=True)



def refresh_resources(lang_code: str) -> None:
    knowledge_store.refresh_resources(lang_code)

# ---------------------------------------------------------------------------
# Mémoire conversationnelle locale (SQLite avec connexion persistante)
# ---------------------------------------------------------------------------

# 💾 RÈGLE BOSS (02/10) : PLUS AUCUNE donnée client sur le disque Railway
# (adieu le volume /data). La mémoire SQLite n'est qu'un CACHE ÉPHÉMÈRE du
# conteneur — la vérité durable vit dans le Google Sheet « Komara Bot -
# Mémoire » (memory_sheets), hydratée au démarrage puis miroitée tour par
# tour. Un redéploiement Railway ne perd RIEN et ne stocke RIEN.
_MEMORY_ENV = os.getenv("MEMORY_DIR", "")
if _MEMORY_ENV:
    MEMORY_DIR = Path(_MEMORY_ENV)   # override explicite (tests, local)
else:
    MEMORY_DIR = Path(tempfile.gettempdir()) / "komara_memory"  # éphémère
MEMORY_FILE = MEMORY_DIR / "memory.db"
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
        # 🛡️ ANTI-DOUBLON (règle d'or : jamais 2 messages à la fois) :
        # un même update Telegram (message ou clic bouton) ne doit JAMAIS
        # être traité deux fois. Cas réel observé : lors d'un redéploiement
        # Railway, l'ancien worker peut encore répondre à un message pendant
        # que le nouveau worker démarre et reçoit CE MÊME message via
        # getUpdates (Telegram ne "consomme" un update qu'au getUpdates
        # SUIVANT avec un offset supérieur — pas au moment de la réception).
        # Résultat observé : 2 réponses différentes (ou identiques) envoyées
        # pour UN SEUL message client. Cette table persiste la clé unique de
        # chaque événement déjà traité, même à travers un redémarrage.
        DB_CONN.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_key TEXT PRIMARY KEY,
                ts REAL NOT NULL
            )
        """)
        DB_CONN.commit()
    logger.info("Cache mémoire éphémère initialisé : %s", MEMORY_FILE)
    threading.Thread(target=_hydrate_memory_from_sheets,
                     name="hydrate-memory", daemon=True).start()


def _hydrate_memory_from_sheets() -> None:
    """RÈGLE BOSS (02/10) : la mémoire durable vit dans le Google Sheet
    « Conversations ». Au démarrage on réhydrate le cache éphémère depuis
    les derniers échanges (bornés). Aucune donnée n'est perdue quand
    Railway reconstruit le conteneur, et rien ne dort sur le disque."""
    global DB_CONN
    try:
        from memory_sheets import read_rows
        rows = read_rows("Conversations", limit=2000)
        if not rows:
            return
        by_chat: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if len(row) < 5:
                continue
            _, chat, _name, role, content = row[0], row[1], row[2], row[3], row[4]
            if not str(chat).lstrip("-").isdigit() or not role or not content:
                continue
            by_chat.setdefault(str(chat), []).append(
                {"role": "client" if role != "bot" else "bot", "content": content[:4000]})
        with DB_LOCK:
            for chat, history in by_chat.items():
                history = history[-MEMORY_LIMIT:]
                DB_CONN.execute(
                    "INSERT OR REPLACE INTO memory (chat_id, history) VALUES (?,?)",
                    (chat, json.dumps(history, ensure_ascii=False)))
            DB_CONN.commit()
        logger.info("Mémoire réhydratée depuis Google Sheets : %s chats", len(by_chat))
    except Exception:
        logger.exception("Hydratation mémoire depuis Sheets impossible")


def _mark_processed_or_duplicate(event_key: str) -> bool:
    """Enregistre event_key comme traité et renvoie True s'il l'était DÉJÀ
    (doublon à ignorer silencieusement), False s'il est nouveau (à traiter).
    Anti-doublon persistant : protège même contre un chevauchement de deux
    process (ex. redéploiement) qui recevraient le même update Telegram."""
    global DB_CONN
    if DB_CONN is None:
        return False  # DB pas encore prête (ne doit jamais bloquer une réponse)
    now = time.time()
    with DB_LOCK:
        try:
            row = DB_CONN.execute(
                "SELECT 1 FROM processed_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if row:
                return True
            DB_CONN.execute(
                "INSERT INTO processed_events (event_key, ts) VALUES (?, ?)",
                (event_key, now),
            )
            # Purge légère (≈1% des appels) : on ne garde que 48h d'historique
            if random.random() < 0.01:
                DB_CONN.execute(
                    "DELETE FROM processed_events WHERE ts < ?", (now - 172800,)
                )
            DB_CONN.commit()
        except sqlite3.IntegrityError:
            # Course entre deux threads sur la même clé : la clé existe déjà
            # → c'est bien un doublon.
            return True
    return False

def has_prior_history(chat_id: int) -> bool:
    """True si ce chat a déjà un historique en base (client REVENANT).
    Sert à ne PAS dire « Re-bonjour, content de te revoir » à un client
    qui vient d'arriver : son prénom Telegram est mémorisé dès le 1er
    message, mais il n'a encore rien dit avant (LETTRE MASTER)."""
    global DB_CONN
    if DB_CONN is None:
        return False
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT history FROM memory WHERE chat_id =?", (str(chat_id),)
        ).fetchone()
        return bool(row and json.loads(row[0]))


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


# Fix routage (Boss 03/10) : les captions des images générées (pitch
# « Tu veux que je lance la version pro ? ») entrent dans la MÉMOIRE du
# bot — sinon le « Oui » du client tombe sur une fiche KB sans rapport.
img_gen.set_history_recorder(remember)


def _last_assistant_msg(chat_id: int) -> str:
    """Dernière réponse ENVOYÉE par le bot à ce client (mémoire SQLite).
    Sert à img_gen pour détecter un contexte image périmé (fix Boss 04/10 :
    un « Oui » ne doit plus relancer une vieille image après une fiche
    KB ou une démo sans rapport)."""
    try:
        for m in reversed(context_for(chat_id)):
            if m.get("role") == "assistant":
                return str(m.get("content", ""))
    except Exception:
        pass
    return ""


img_gen.set_history_reader(_last_assistant_msg)

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

# BUG CLIENT (02/10, screenshot) : « Menu » tapé SANS slash (capitale ou
# minuscule) n'était PAS reconnu comme commande (seul « /menu » l'était).
# Le mot tombait alors jusqu'à la recherche floue KB/dialogues, qui
# matchait un vieux dialogue hors-sujet (« menu de restaurant tu peux
# designer » → réponse resto totalement incohérente). FIX : les mots nus
# équivalents (menu/aide/help...) sont ajoutés, dans les 4 langues, pour
# ne JAMAIS atteindre la recherche floue.
START_COMMANDS: set[str] = {
    "/start", "/star", "/menu", "/help", "/ayuda", "/inicio",
    "menu", "aide", "help", "ayuda", "menú", "القائمة", "قائمة",
}

RESET_COMMANDS: dict[str, set[str]] = {
    "fr": {"/reset", "/forget", "oublie", "oublie-moi"},
    "en": {"/reset", "/forget", "forget", "reset"},
    "ar": {"/reset", "/forget", "نسيان", "مسح"},
    "es": {"/reset", "/forget", "olvida", "reiniciar"},
}

MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "reset": "D'accord, j'ai effacé le contexte de cette conversation. Que souhaitez-vous faire?",
        "fallback": "Je n'ai pas encore cette connaissance dans ma base de données 🙏\n\nJe note votre question pour l'équipe Komara.\n\nTapez 'menu' pour voir nos services : bots WhatsApp/Telegram, sites web, applications, logos et création digitale.\n\nTapez 'prix' pour les tarifs, 'services' pour nos offres, ou décrivez votre projet.",
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
        "lang_switched": "✅ Parfait, on continue en français 🇫🇷",
    },

    "en": {
        "reset": "Done, I've cleared the context. What would you like to do?",
        "fallback": "I don't have that knowledge in my database yet 🙏\n\nI'm noting your question for the Komara team.\n\nType 'menu' to see our services: WhatsApp/Telegram bots, websites, apps, logos and digital creation.\n\nType 'pricing' for rates, 'services' for our offers, or describe your project.",
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
        "lang_switched": "✅ Great, let us continue in English 🇬🇧",
    },
    "ar": {
        "reset": "تم مسح السياق. ماذا تريد أن تفعل؟",
        "fallback": "لا أملك هذه المعلومة بعد في قاعدتي 🙏\n\nسأدوّن سؤالك لفريق كومارا.\n\nاكتب 'menu' لرؤية خدماتنا: بوتات واتساب/تيليجرام، مواقع، تطبيقات، شعارات وإنشاء رقمي.\n\nاكتب 'السعر' للأسعار، 'الخدمات' لعروضنا، أو صف مشروعك.",
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
        "lang_switched": "✅ ممتاز، نكمل بالعربية 🇸🇦",
    },
    "es": {
        "reset": "Listo, he borrado el contexto. ¿Qué quieres hacer?",
        "fallback": "Aún no tengo ese conocimiento en mi base de datos 🙏\n\nAnoto su pregunta para el equipo Komara.\n\nEscriba 'menu' para ver nuestros servicios: bots de WhatsApp/Telegram, sitios web, aplicaciones, logos y creación digital.\n\nEscribe 'precio' para tarifas, 'servicios' para nuestras ofertas, o describe tu proyecto.",
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
        "lang_switched": "✅ Perfecto, seguimos en español 🇪🇸",
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

# FALLBACK HONNÊTE : après avoir consulté toute la base (kb.json, FAQ,
# dialogues, portfolio, 4 langues), si la réponse n'y est pas, on le dit
# avec politesse au lieu d'inventer un coq-à-l'âne. La question est
# journalisée (record_unrecognized) pour enrichir la base plus tard.
FALLBACK_VARIANTS_FR = [
    "Je n'ai pas encore cette connaissance dans ma base de données 🙏 Je note ta question pour l'équipe Komara. En attendant, tape 'menu' pour voir tout ce que je peux faire pour toi.",
    "Bonne question, mais elle dépasse mes connaissances actuelles 😅 Je l'ai notée pour m'améliorer. Tu peux décrire ton projet, ou cliquer sur un bouton ci-dessous 👇",
    "Je ne connais pas encore ce sujet 🙏 Ma base est spécialisée Komara Agency : bots, sites, logos, visuels, vidéo IA. Tape 'menu' pour explorer mes services.",
]

def menu_for_lang(lang: str) -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for row in KEYBOARDS.get(lang, KEYBOARDS["fr"]):
        keyboard.add(*row)
    return keyboard

def msg(lang: str, key: str) -> str:
    return MESSAGES.get(lang, MESSAGES["fr"]).get(key, MESSAGES["fr"].get(key, ""))

def _handle_menu_button(chat_id: int, label: str, lang: str) -> bool:
    """RÈGLE D'OR (02/10) : un BOUTON du menu n'est JAMAIS avalé comme
    réponse à une question d'un flux actif (devis, rdv, commande...).
    Un bouton est un geste EXPLICITE : il exécute toujours sa propre
    action, même pendant un flux. Bug corrigé : pendant un devis, taper
    « 🛍️ Catalogue » ou « 💎 Voir les Tarifs » répondait « Tape le
    numéro du service ».
    Seul « 👑 Parler à un humain » n'est PAS routé ici : il doit rester
    intercepté par actions.handle (_wants_human, priorité absolue) pour
    interrompre TOUT flux et demander le numéro WhatsApp.
    """
    # 1. Suivi commande (avant les autres : « تتبع الطلب » contient « طلب »)
    if any(k in label for k in ("Suivi", "Tracking", "Seguimiento", "تتبع")):
        actions.order_tracking(bot, chat_id, lang)
        return True
    # 2. Code promo
    if "promo" in label.lower() or "خصم" in label:
        bot.send_message(chat_id, msg(lang, "promo"), reply_markup=menu_for_lang(lang))
        return True
    # 3. Commander / Order / Ordenar / طلب — panier non vide → tunnel
    if any(k in label for k in ("Commander", "Order", "Ordenar", "طلب")):
        if not catalogue.start_checkout(bot, chat_id, lang):
            actions.start_flow(bot, chat_id, "order", lang, trigger_text=label)
        return True
    # 4. Devis / Quote / Presupuesto / تسعيرة — priorité panier (lettre)
    if any(k in label for k in ("Devis", "Quote", "Presupuesto", "تسعيرة")):
        if not catalogue.start_checkout(bot, chat_id, lang):
            actions.start_flow(bot, chat_id, "devis", lang, trigger_text=label)
        return True
    # 5. Avis / Feedback / Opinión / تقييم
    if any(k in label for k in ("Avis", "Feedback", "Opinión", "تقييم")):
        actions.start_flow(bot, chat_id, "survey", lang)
        return True
    # 6. Rendez-vous / Book a call / Reservar / حجز موعد
    if any(k in label for k in ("Rendez-vous", "Book a call", "Reservar", "حجز")):
        actions.start_flow(bot, chat_id, "rdv", lang, trigger_text=label)
        return True
    # 7. Catalogue — VISUEL (photo bannière + boutons cliquables) depuis
    # le 03/10 : le boss exige le catalogue avec l'image, pas la liste texte.
    if any(k in label for k in ("Catalogue", "Catálogo", "كتالوج")):
        catalogue.show_catalogue_inline(bot, chat_id, lang)
        return True
    # 8. Panier / Cart / Carrito / سلة
    if any(k in label for k in ("Panier", "Cart", "Carrito", "سلة")):
        catalogue.show_cart(bot, chat_id, lang)
        return True
    # 9. Chatbot IA / AI Chatbot / مساعد ذكي
    # FIX BOSS (06/10, screenshot) : vrai flux de qualification, pas juste
    # un message KB — les réponses du client sont capturées par étapes.
    if any(k in label for k in ("Chatbot", "ذكي")):
        bot.send_message(chat_id, msg(lang, "chatbot"), reply_markup=menu_for_lang(lang))
        actions.start_chatbot_qualify_flow(chat_id)
        return True
    # 10. Portfolio / المعرض / Portafolio
    if any(k in label for k in ("Portfolio", "Portafolio", "المعرض")):
        send_portfolio(chat_id, lang)
        return True
    # 11. Tarifs / Pricing / Precios / الأسعار — FIX BOSS (03/10) :
    # d'abord le stub vide « Voici nos offres : », puis la liste texte.
    # Maintenant : le catalogue VISUEL complet (photo bannière + un bouton
    # « Commander <service> — <prix> » par offre + Voir Panier + Support),
    # identique au bouton Catalogue — même tunnel vers la commande.
    if any(k in label for k in ("Tarif", "Pricing", "Precio", "الأسعار")):
        catalogue.show_catalogue_inline(bot, chat_id, lang)
        return True
    return False

# ---------------------------------------------------------------------------
# Recherche contextuelle sécurisée
# ---------------------------------------------------------------------------

# PRÉCISION OBLIGATOIRE : mots courts ambigus qui, seuls, ne disent pas
# l'intention du client. Le bot demande une précision au lieu de répondre
# au hasard (règle d'or anti coq-à-l'âne).
AMBIGUOUS_WORDS: dict[str, str] = {
    # fr
    "bot": "bot", "chatbot": "bot", "robot": "bot", "bots": "bot",
    "automatique": "auto", "automatisation": "auto", "automatiser": "auto",
    "automatise": "auto", "auto": "auto",
    "ia": "ia", "intelligence": "ia",
    "site": "site", "web": "site", "site web": "site",
    "app": "app", "application": "app", "appli": "app",
    "logo": "logo", "logos": "logo",
    "video": "video", "vidéo": "video", "videos": "video",
    "visuel": "visuel", "visuels": "visuel", "affiche": "visuel",
    "design": "visuel", "graphique": "visuel",
    "marketing": "marketing", "pub": "marketing", "publicite": "marketing",
    "formation": "formation", "formations": "formation",
    "faire": "faire", "créer": "faire", "creer": "faire", "veux": "faire",
    "cree": "faire", "fait": "faire",
    # en
    "automatic": "auto", "automation": "auto", "ai": "ia",
    "website": "site", "visual": "visuel", "visuals": "visuel",
    "video2": "video", "training": "formation", "apps": "app",
    # es
    "sitio": "site", "vídeo": "video", "diseño": "visuel",
    "formación": "formation", "automatización": "auto", "automático": "auto",
    # ar
    "بوت": "bot", "روبوت": "bot", "ذكاء": "ia", "موقع": "site",
    "شعار": "logo", "فيديو": "video", "تصميم": "visuel", "تسويق": "marketing",
    "تكوين": "formation", "تطبيق": "app", "أتمتة": "auto", "تطبيقات": "app",
}

_AMBIGUOUS_CLARIFY: dict[str, dict[str, list[str]]] = {
    "bot": {
        "fr": ["Bot ? Dis-moi tout 😊 Tu penses à un bot WhatsApp, un bot Telegram, ou tu veux qu'on crée un bot pour ton business ?",
               "Bot ? Précise-moi un peu 😄 Tu veux commander un bot, voir une démo, ou tu demandes comment ça marche ?"],
        "en": ["A bot? Tell me more 😊 Do you mean a WhatsApp bot, a Telegram bot, or do you want us to create a bot for your business?",
               "A bot? A bit more detail 😄 Do you want to order a bot, see a demo, or ask how it works?"],
        "es": ["¿Un bot? Cuénteme 😊 ¿Se refiere a un bot de WhatsApp, un bot de Telegram, o quiere que creemos un bot para su negocio?",
               "¿Un bot? Un poco más de detalle 😄 ¿Quiere pedir un bot, ver una demo o preguntar cómo funciona?"],
        "ar": ["بوت؟ أخبرني أكثر 😊 هل تقصد بوت واتساب، بوت تيليغرام، أم تريد أن نخلق بوتاً لعملك؟",
               "بوت؟ مزيداً من التفصيل 😄 أتريد طلب بوت، رؤية عرض، أم تسأل كيف يعمل؟"],
    },
    "faire": {
        "fr": ["Faire quoi ? 😊 Précise ton idée : un logo, un site, un bot, un visuel ?",
               "Dis-moi ce que tu veux créer 😄 Un bot, un site, un logo, une affiche ?"],
        "en": ["Do what? 😊 Tell me your idea: a logo, a website, a bot, a visual?",
               "What would you like to create 😄 A bot, a website, a logo, a poster?"],
        "es": ["¿Hacer qué? 😊 Dígame su idea: ¿un logo, una web, un bot, un visual?",
               "¿Qué quieres crear 😄 ¿Un bot, una web, un logo, un afiche?"],
        "ar": ["أن تفعل ماذا؟ 😊 حدد فكرتك: شعار، موقع، بوت، تصميم؟",
               "ماذا تريد أن تنشئ 😄 بوت، موقع، شعار، ملصق؟"],
    },
    "site": {
        "fr": ["Un site ? Précise-moi 😊 Tu veux un site vitrine pour te présenter, ou une boutique en ligne pour vendre ?",
               "Site web ? Dis-moi ton objectif 😄 Présenter ton business ou vendre tes produits en ligne ?"],
        "en": ["A website? Tell me 😊 Do you want a showcase site to present yourself, or an online shop to sell?",
               "Website? What's your goal 😄 Presenting your business or selling your products online?"],
        "es": ["¿Una web? Dígame 😊 ¿Quiere un sitio vitrina para presentarse, o una tienda online para vender?",
               "¿Web? ¿Cuál es su objetivo 😄 Presentar su negocio o vender sus productos online?"],
        "ar": ["موقع؟ أخبرني 😊 أتريد موقعاً تعريفياً لتقديم نفسك أم متجراً إلكترونياً للبيع؟",
               "موقع؟ ما هدفك 😄 تقديم عملك أم بيع منتجاتك أونلاين؟"],
    },
    "logo": {
        "fr": ["Un logo ? Avec plaisir 🎨 C'est pour une nouvelle marque, un relooking, ou un logo pour ton business ?",
               "Logo ? Dis-moi 😊 Tu pars de zéro ou tu modernises un logo existant ?"],
        "en": ["A logo? With pleasure 🎨 Is it for a new brand, a refresh, or a logo for your business?",
               "Logo? Tell me 😊 Starting from scratch or modernizing an existing logo?"],
        "es": ["¿Un logo? Con gusto 🎨 ¿Es para una marca nueva, un rediseño, o un logo para su negocio?",
               "¿Logo? Cuénteme 😊 ¿Parte de cero o moderniza un logo existente?"],
        "ar": ["شعار؟ بكل سرور 🎨 هل هو لعلامة جديدة، تجديد، أم شعار لعملك؟",
               "شعار؟ أخبرني 😊 تبدأ من الصفر أم تحدّث شعاراً موجوداً؟"],
    },
    "video": {
        "fr": ["Vidéo ? Précise 😊 Une vidéo publicitaire pour tes réseaux, ou une vidéo pour présenter ton business ?",
               "Vidéo ? Dis-moi 😄 C'est pour une promo, une présentation, ou les vœux de fin d'année ?"],
        "en": ["Video? Tell me 😊 An ad video for your networks, or a video presenting your business?",
               "Video? More detail 😄 Is it for a promo, a presentation, or end-of-year greetings?"],
        "es": ["¿Vídeo? Precise 😊 ¿Un vídeo publicitario para sus redes, o un vídeo para presentar su negocio?",
               "¿Vídeo? Dígame 😄 ¿Es para una promo, una presentación o felicitaciones de fin de año?"],
        "ar": ["فيديو؟ وضّح 😊 فيديو إعلاني لشبكاتك أم فيديو لتقديم عملك؟",
               "فيديو؟ أخبرني 😄 هل هو لعرض ترويجي، تقديم، أم تهاني نهاية السنة؟"],
    },
    "visuel": {
        "fr": ["Design ? Dis-moi 😊 Un visuel pour tes réseaux, une affiche promo, ou une identité complète ?",
               "Visuel ? Précise 🎨 Une affiche pour une promo, tes statuts WhatsApp, ou un pack complet ?"],
        "en": ["Design? Tell me 😊 A visual for your networks, a promo poster, or a full identity?",
               "Visual? More detail 🎨 A promo poster, your WhatsApp status, or a full pack?"],
        "es": ["¿Diseño? Dígame 😊 ¿Un visual para sus redes, un cartel promo, o una identidad completa?",
               "¿Visual? Precise 🎨 ¿Un cartel para una promo, sus estados de WhatsApp, o un pack completo?"],
        "ar": ["تصميم؟ أخبرني 😊 تصميم لشبكاتك، منشور ترويجي، أم هوية كاملة؟",
               "تصميم؟ وضّح 🎨 منشور لعرض، حالاتك على الواتساب، أم حزمة كاملة؟"],
    },
    "marketing": {
        "fr": ["Marketing ? Précisons 😊 Tu veux vendre sur WhatsApp, faire des pubs Facebook, ou organiser tes réseaux ?",
               "Marketing ? Dis-moi ton objectif 😄 Trouver plus de clients ou vendre plus aux clients actuels ?"],
        "en": ["Marketing? Let's clarify 😊 Do you want to sell on WhatsApp, run Facebook ads, or organize your networks?",
               "Marketing? What's your goal 😄 Finding more clients or selling more to current ones?"],
        "es": ["¿Marketing? Concretemos 😊 ¿Quiere vender por WhatsApp, hacer anuncios de Facebook, u organizar sus redes?",
               "¿Marketing? ¿Su objetivo 😄 Conseguir más clientes o vender más a los actuales?"],
        "ar": ["تسويق؟ لنوضح 😊 أتريد البيع على الواتساب، إعلانات فيسبوك، أم تنظيم شبكاتك؟",
               "تسويق؟ ما هدفك 😄 إيجاد زبائن أكثر أم البيع أكثر للزبائن الحاليين؟"],
    },
    "auto": {
        "fr": ["Automatisation ? Bonne piste ⚙️ Tu veux automatiser tes réponses clients, tes relances, ou tes commandes ?",
               "Automatique ? Précise 😊 Tu parles d'un bot qui répond tout seul, ou d'automatiser tes ventes ?"],
        "en": ["Automation? Good lead ⚙️ Do you want to automate client replies, follow-ups, or your orders?",
               "Automatic? Clarify 😊 Do you mean a bot that answers by itself, or automating your sales?"],
        "es": ["¿Automatización? Buena pista ⚙️ ¿Quiere automatizar sus respuestas a clientes, sus seguimientos o sus pedidos?",
               "¿Automático? Precise 😊 ¿Habla de un bot que responde solo, o de automatizar sus ventas?"],
        "ar": ["أتمتة؟ مسار جيد ⚙️ أتريد أتمتة الردود على الزبائن، المتابعات، أم الطلبات؟",
               "تلقائي؟ وضّح 😊 هل تقصد بوتاً يرد بنفسه أم أتمتة مبيعاتك؟"],
    },
    "ia": {
        "fr": ["L'IA ? 😊 Tu veux un chatbot IA, une formation IA, ou créer des images avec l'IA ?",
               "IA ? Précise 😄 Un bot intelligent pour ton business, ou apprendre à utiliser l'IA ?"],
        "en": ["AI? 😊 Do you want an AI chatbot, AI training, or creating images with AI?",
               "AI? Clarify 😄 A smart bot for your business, or learning to use AI?"],
        "es": ["¿IA? 😊 ¿Quiere un chatbot IA, formación IA, o crear imágenes con IA?",
               "¿IA? Precise 😄 ¿Un bot inteligente para su negocio, o aprender a usar la IA?"],
        "ar": ["الذكاء الاصطناعي؟ 😊 أتريد بوتاً ذكياً، تكويناً في الذكاء، أم إنشاء صور بالذكاء؟",
               "الذكاء؟ وضّح 😄 بوت ذكي لعملك أم تعلم استخدام الذكاء الاصطناعي؟"],
    },
    "app": {
        "fr": ["Une app ? Précise 😊 Une application web pour tes clients, ou un bot qui remplace une app ?",
               "App ? Dis-moi 😄 Tu veux vendre dans une app, ou gérer ton business depuis ton téléphone ?"],
        "en": ["An app? Clarify 😊 A web app for your clients, or a bot that works like an app?",
               "App? Tell me 😄 Do you want to sell in an app, or manage your business from your phone?"],
        "es": ["¿Una app? Precise 😊 ¿Una aplicación web para sus clientes, o un bot que hace de app?",
               "¿App? Dígame 😄 ¿Quiere vender en una app, o gestionar su negocio desde su móvil?"],
        "ar": ["تطبيق؟ وضّح 😊 تطبيق ويب لزبائنك أم بوت يقوم مقام التطبيق؟",
               "تطبيق؟ أخبرني 😄 أتريد البيع في تطبيق أم تدبير عملك من هاتفك؟"],
    },
    "formation": {
        "fr": ["Formation ? 😊 Tu veux apprendre l'IA pour ton business, ou former ton équipe ?",
               "Formation ? Précise 😄 C'est pour toi ou pour ton équipe ?"],
        "en": ["Training? 😊 Do you want to learn AI for your business, or train your team?",
               "Training? Clarify 😄 Is it for you or for your team?"],
        "es": ["¿Formación? 😊 ¿Quiere aprender IA para su negocio, o formar a su equipo?",
               "¿Formación? Precise 😄 ¿Es para usted o para su equipo?"],
        "ar": ["تكوين؟ 😊 أتريد تعلم الذكاء لعملك أم تكوين فريقك؟",
               "تكوين؟ وضّح 😄 هل هو لك أم لفريقك؟"],
    },
}

def clarif(group: str, lang: str) -> str:
    """Message de demande de précision, dans la langue du client."""
    pool = (_AMBIGUOUS_CLARIFY.get(group) or {}).get(lang) \
        or (_AMBIGUOUS_CLARIFY.get(group) or {}).get("fr") \
        or ["Peux-tu préciser ta demande 😊"]
    return random.choice(pool)

# Mots courts de confirmation/refus : sur ces mots-là, un match direct
# "au hasard" (ex : "oui" matchant à tort la fiche "oui j en ai") est un
# vrai risque de boucle. La RÈGLE D'OR (contexte de la question du bot)
# passe donc AVANT le matching direct pour ces mots précis.
_CONFIRM_WORDS: set[str] = {
    "oui", "ouais", "oui merci", "d'accord", "daccord", "ok", "okay", "yes",
    "yeah", "sure", "sí", "si", "vale", "claro", "نعم", "أكيد", "تمام",
    "non", "no", "nope", "لا",
}

# Mots interrogatifs : « où ? », « quand ? », « combien ? »… un suivi
# court qui DEMANDE une précision sur le sujet en cours (fil rouge).
_QUESTION_WORD_RE = re.compile(
    r"^\s*(o[uù]|quand|comment|pourquoi|qui|quel(?:le)?s?|combien|"
    r"where|when|how|why|who|which|"
    r"c[oó]mo|d[oó]nde|cu[aá]ndo|cu[aá]l|cu[aá]nto|"
    r"لماذا|كيف|متى|أين|من)\b|\?\s*$",
    re.IGNORECASE,
)


def _user_word_in_fiche(qa_answer: str, user_text: str, lang: str) -> bool:
    """Le message court du client contient-il un mot LIÉ à la fiche
    matchée ? (« autonome » après la F1 → la fiche inscription contient
    « autonome » ; « à dakar » → la fiche livraison contient « dakar ».)
    Un mot hors sujet (« sante », « te ») ne se retrouve pas dans la
    fiche → le match était porté uniquement par le message du bot."""
    from normalize_text import normalize_text as _norm
    tokens = re.findall(r"\b\w{3,}\b", _norm(user_text or ""))
    if not tokens:
        return False
    for _lang in ({lang, DEFAULT_LANGUAGE} if lang != DEFAULT_LANGUAGE else {lang}):
        resources = LANG_RESOURCES.get(_lang) or {}
        for src in ("kb", "faq", "dialogues"):
            for fiche in resources.get(src) or []:
                if fiche.get("answer") != qa_answer:
                    continue
                _qs = fiche.get("questions", [])
                if isinstance(_qs, str):
                    _qs = [_qs]
                # dialogues aya2 : entrées plates {question, answer}
                _q = fiche.get("question")
                if isinstance(_q, str):
                    _qs = list(_qs) + [_q]
                hay = _norm(
                    " ".join(_qs)
                    + " " + " ".join(fiche.get("tags", []) or [])
                    + " " + " ".join(fiche.get("keywords", []) or [])
                    # la réponse elle-même compte (« à dakar » → la fiche
                    # livraison répond « beaucoup de clients à Dakar »)
                    + " " + str(fiche.get("answer", ""))
                )
                if any(f" {tok} " in f" {hay} " for tok in tokens):
                    return True
    return False


def _ghost_free_blend(last_bot_msg: str, user_text: str, detected_lang: str) -> str | None:
    """Combine le dernier message du bot + le texte du client, MAIS avec
    un filtre anti-fantôme (lot 21) : si le message du bot SEUL produit
    déjà la même réponse, le mot du client n'a rien apporté au match —
    c'est le bot qui se re-matche lui-même (bug « sante », « te »
    répondaient des fiches sans rapport). ON ACCEPTE pourtant l'égalité
    dans les deux seuls cas légitimes :
    • le client pose un SUIVI interrogatif (« où ? », « à dakar » après
      une réponse livraison) — le sujet vient du contexte, c'est le fil
      rouge voulu ;
    • un mot du client figure dans la fiche matchée (« autonome » →
      fiche inscription) — le mot a réellement guidé le match.
    Sinon : fallback honnête plutôt qu'une réponse hasardeuse."""
    if not last_bot_msg:
        return None
    qa_combined = f"{last_bot_msg} {user_text}"
    qa_answer = trouver_meilleure_reponse_multilingue(qa_combined, detected_lang)
    if not qa_answer or qa_answer == last_bot_msg:
        return None
    ghost_answer = trouver_meilleure_reponse_multilingue(last_bot_msg, detected_lang)
    if qa_answer != ghost_answer:
        return qa_answer
    low = user_text.strip().lower()
    if low.endswith("?") or _QUESTION_WORD_RE.match(low):
        return qa_answer
    if _user_word_in_fiche(qa_answer, user_text, detected_lang):
        return qa_answer
    return None


def local_contextual_response(chat_id: int, user_text: str, detected_lang: str) -> str | None:
    user_text = user_text[:4000].strip()
    if not user_text:
        return None

    history = context_for(chat_id)
    last_bot_msg = next(
        (h.get("content", "") for h in reversed(history)
         if h.get("role") == "assistant" and h.get("content")),
        "")

    # RÈGLE D'OR EN PRIORITÉ : mot de confirmation + question du
    # bot en attente → on répond DANS CE CONTEXTE avant tout autre
    # matching (fini la boucle "Oui" → réponse d'une fiche sans rapport).
    # « oui » mais aussi « oui exactement », « oui explique », « non pas
    # de logo » : toute réponse courte qui COMMENCE par oui/non est une
    # réponse à la question du bot, pas une question autonome.
    _low = user_text.strip().lower()
    _first = _low.split(" ", 1)[0].strip(" .?!…'’")
    is_confirm_word = (_low in _CONFIRM_WORDS) or (
        _first in ("oui", "ouais", "yes", "yeah", "sí", "si", "نعم", "أكيد", "تمام",
                   "non", "no", "nope", "لا")
        and significant_token_count(user_text) <= 3)
    if is_confirm_word and "?" in last_bot_msg:
        # Lot 21 : pour une CONFIRMATION (« oui » à une question du bot),
        # le self-match est LÉGITIME — la question du bot vient de sa
        # propre fiche, donc « Oui » doit retourner la fiche suivante de
        # cette même question (« tu veux voir un exemple ? » → portfolio).
        # Le filtre fantôme (conçu pour « sante »/« te ») ne doit donc
        # PAS s'appliquer ici ; la seule protection nécessaire reste
        # l'anti-écho (ne pas renvoyer mot pour mot le dernier message).
        qa_combined = f"{last_bot_msg} {user_text}"
        qa_answer = trouver_meilleure_reponse_multilingue(qa_combined, detected_lang)
        if qa_answer and qa_answer != last_bot_msg:
            return qa_answer

    # RÈGLE D'OR (message court) : un mot court (« où ? », « femme »,
    # « le prix »...) se comprend DANS LE FIL de la conversation, pas
    # seul. Le client dit « quel délai ? » puis « où ? » : le bot se
    # souvient du sujet et répond sur la LOCALISATION, jamais au hasard.
    # On combine donc avec le dernier message du bot, question ou pas.
    is_short = significant_token_count(user_text) < 2
    _is_personal = bool(PERSONAL_QUESTION_RE.search(_low))
    if is_short and last_bot_msg and not is_confirm_word and not _is_personal:
        qa_answer = _ghost_free_blend(last_bot_msg, user_text, detected_lang)
        if qa_answer:
            return qa_answer

    # PRÉCISION OBLIGATOIRE : un mot court AMBIGU (« bot », « chatbot »,
    # « automatique »...) ne reçoit JAMAIS de réponse au hasard. Le
    # contexte a été essayé juste au-dessus ; s'il n'a rien donné, le
    # bot demande de préciser l'intention du client.
    if is_short:
        _norm = user_text.strip().lower().strip(" .?!…")
        _grp = AMBIGUOUS_WORDS.get(_norm)
        if _grp:
            return clarif(_grp, detected_lang)

    # 1. Recherche sémantique directe (l'intention de la PHRASE ENTIÈRE,
    # pas un mot isolé — le scoring bidirectionnel lit toute la question).
    # Une phrase complète qui a déjà une bonne réponse toute seule
    # (« Comment tu vas ? », « Tu es humain ? ») ne doit JAMAIS être
    # mélangée avec un message précédent sans rapport.
    direct_answer = trouver_meilleure_reponse_multilingue(user_text, detected_lang)
    if direct_answer:
        return direct_answer

    # 2. Message court sans contexte exploitable : aucun re-match
    # hasardeux (bug boucle accueil corrigé précédemment)
    if is_short:
        return None

    # 2bis. Confirmation longue (≤3 tokens) sans match contextuel : on
    # ne laisse JAMAIS un « oui... » tomber sur une fiche au hasard.
    if is_confirm_word:
        return None

    # 3. Recherche avec contexte de conversation complet : le dernier
    # message du bot alimente TOUJOURS la recherche (question ou pas),
    # pour que la réponse du client s'interprète dans son contexte.
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {user_text}".strip()
    if last_bot_msg:
        combined_text = f"{combined_text} {last_bot_msg}".strip()

    # Test d'exclusion (lot 21) : on calcule le match SANS le nouveau
    # message du client (contexte seul). Si le contexte seul produit déjà
    # la même réponse, le nouveau message n'a RIEN apporté — le match
    # vient uniquement des messages précédents. On refuse alors de
    # répondre à côté (bug signalé : « il me faut un visuel pour demain
    # matin » renvoyait la réponse « réservation resto » du message
    # précédent). Fallback honnête plutôt qu'une réponse sans rapport.
    contextual_answer = trouver_meilleure_reponse_multilingue(combined_text, detected_lang)
    if contextual_answer:
        base_text = f"{recent_context} {last_bot_msg}".strip()
        base_answer = (trouver_meilleure_reponse_multilingue(base_text, detected_lang)
                       if base_text else None)
        if contextual_answer != base_answer:
            return contextual_answer
        return None

    return None

# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

PORTFOLIO_DIR = BASE_DIR / "portfolio"
PORTFOLIO_BUTTON_PREFIX = "📷 "
PORTFOLIO_SENTINEL = "__SHOW_PORTFOLIO__"

# Lot 20 : invitation à s'inscrire à la Formation IA. Le texte réel part de
# actions.FORMATION_MSGS (4 langues) et le bot mémorise que le prochain
# message du client = infos d'inscription → notification admin.
FORMATION_SENTINEL = "__FORMATION_INSCRIPTION__"


# Compatibility exports point to the same store used by admin actions.
similar_question_exists = knowledge_store.similar_question_exists
add_custom_kb_entry = knowledge_store.add_custom_kb_entry

PORTFOLIO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

def portfolio_images() -> list[tuple[str, object]]:
    """Liste le portfolio : Drive d'abord (source durable), dossier local
    en fallback (dev). Chaque entrée = (nom_affiché, source) où source est
    ("drive", file_id) ou ("local", Path)."""
    images: list[tuple[str, object]] = []
    # 1. Google Drive (source principale, persistante)
    try:
        for f in portfolio_drive.list_images():
            name = f.get("name", "image")
            stem = re.sub(r"^\d+[_\-]", "", name.rsplit(".", 1)[0])
            display = stem.replace("_", " ").replace("-", " ").strip() or name
            images.append((display, ("drive", f.get("id", ""))))
    except Exception:
        logger.debug("Portfolio Drive indisponible", exc_info=True)
    # 2. Dossier local (fallback dev / hors-ligne)
    if PORTFOLIO_DIR.is_dir():
        for entry in sorted(PORTFOLIO_DIR.iterdir()):
            if entry.is_file() and entry.suffix.lower() in PORTFOLIO_EXTENSIONS:
                stem = re.sub(r"^\d+[_\-]", "", entry.stem)
                display_name = stem.replace("_", " ").replace("-", " ").strip()
                images.append((display_name, ("local", entry)))
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
    list_context.set_context(chat_id, "portfolio")
    bot.send_message(chat_id, "\n".join(lines), reply_markup=portfolio_keyboard())

_PORTFOLIO_WORDS = {"portfolio", "portefolio", "porte folio", "portfolios", "portfolio komara",
                    "le portfolio", "ton portfolio", "votre portfolio", "mon portfolio",
                    "voir le portfolio", "voir portfolio", "montre le portfolio",
                    "realisations", "réalisations", "vos realisations", "vos réalisations"}

_SERVICES_MENU_RE = re.compile(
    r"1\ufe0f?\u20e3[^\n]*\n\s*2\ufe0f?\u20e3[^\n]*\n\s*3\ufe0f?\u20e3[^\n]*\n\s*4\ufe0f?\u20e3[^\n]*\s*$")

_SERVICES_REPLIES = {
    2: ("Site web 🌐 Vitrine ou boutique en ligne ? Dis-moi ton activité et je te "
        "prépare une proposition. Tarif de départ : 500 000 GNF (7 jours)."),
    3: ("Logo / visuel 🎨 Logo : 300 000 à 500 000 GNF, livré en 24h pour la V1. "
        "Dis-moi ton activité et le style que tu aimes 👇"),
}


def _route_services_choice(chat_id: int, index: int, lang: str) -> bool:
    """Chiffre 1-4 tapé après le menu des services (pitch KB)."""
    if index == 1:
        bot.send_message(chat_id, msg(lang, "chatbot"), reply_markup=menu_for_lang(lang))
        actions.start_chatbot_qualify_flow(chat_id)
        return True
    if index == 4:
        return bool(actions.start_flow(bot, chat_id, "devis", lang))
    reply = _SERVICES_REPLIES.get(index)
    if not reply:
        return False
    remember(chat_id, "assistant", reply)
    bot.send_message(chat_id, reply, reply_markup=menu_for_lang(lang))
    return True


def send_portfolio_image_by_index(chat_id: int, index: int, lang: str) -> bool:
    """Envoie l'image du portfolio à la position `index` (1-based).
    Gère les sources Drive (téléchargement) et locale (fichier)."""
    images = portfolio_images()
    if not (1 <= index <= len(images)):
        return False
    name, source = images[index - 1]
    try:
        import io
        if isinstance(source, tuple) and source[0] == "drive":
            data = portfolio_drive.download_image(source[1])
            if not data:
                bot.send_message(chat_id, "⚠️ Image indisponible sur Drive.")
                return False
            bot.send_photo(chat_id, io.BytesIO(data), caption=f"📷 {name}")
        else:  # local : source = ("local", Path)
            # BUG BOSS 07/10 (screenshot « 📷 founder cafe laptop » → la liste
            # revenait au lieu de l'image) : on appelait .open() sur le TUPLE
            # ("local", Path) entier -> AttributeError à chaque envoi.
            path = source[1] if isinstance(source, tuple) else source
            with path.open("rb") as image_file:
                bot.send_photo(chat_id, image_file, caption=f"📷 {name}")
        bot.send_message(
            chat_id,
            "Une autre réalisation t'intéresse ? Numéro ou titre 👇",
            reply_markup=portfolio_keyboard(),
        )
        return True
    except Exception:
        logger.exception("Échec d'envoi de l'image portfolio %s", name)
        return False

def send_portfolio_image(chat_id: int, display_name: str, lang: str) -> bool:
    for i, (name, _src) in enumerate(portfolio_images(), start=1):
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

    # ── Feature #3 : photo reçue → scan du QR de paiement.
    # RÈGLE ANTI-CONFLIT : le TYPE du message décide. Une photo ne
    # génère JAMAIS un QR, elle est TOUJOURS scannée (et inversement
    # pour 'payer' en texte). Les 2 ne tournent jamais ensemble.
    if getattr(message, "photo", None):
        # ── IMG2IMG (Boss 04/10) : photo + texte → retouche IA. Une photo
        # AVEC caption est TOUJOURS une demande de retouche (client ou
        # boss) — le prompt client est respecté, visage préservé, rendu
        # réaliste sauf demande cartoon explicite. Sans caption, la photo
        # suit son chemin habituel (portfolio admin / scan reçu client).
        _img_caption = strip_invisible_chars(message.caption or "") if message.caption else ""
        if _img_caption:
            detected_lang = detect_language(_img_caption) or "fr"
            safe_typing(chat_id)
            if img_gen.handle_photo_request(bot, chat_id, _img_caption, message, detected_lang):
                return
        # ADMIN : la photo est une réalisation → portfolio Drive.
        if actions.ADMIN_CHAT_ID and chat_id == actions.ADMIN_CHAT_ID:
            safe_typing(chat_id)
            try:
                _f = bot.get_file(message.photo[-1].file_id)
                _data = bot.download_file(_f.file_path)
                from datetime import datetime
                _name = f"portfolio_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
                _id = portfolio_drive.upload_image(_name, _data)
                if _id:
                    bot.send_message(chat_id,
                        f"📸 Enregistrée dans le portfolio Drive ✅\n"
                        f"Fichier : {_name}\n"
                        f"Elle apparaîtra dans le bouton 📂 Portfolio.")
                else:
                    bot.send_message(chat_id,
                        "⚠️ Google non lié — lance /google pour activer le "
                        "portfolio Drive. Photo non sauvegardée.")
            except Exception as e:
                logger.error("Upload portfolio admin impossible : %s", e)
                bot.send_message(chat_id, msg("fr", "crash_fallback"))
            return
        # CLIENT : scan du QR de paiement.
        safe_typing(chat_id)
        try:
            _f = bot.get_file(message.photo[-1].file_id)
            _data = bot.download_file(_f.file_path)
            _path = f"/tmp/komara_recu_{chat_id}.png"
            with open(_path, "wb") as fh:
                fh.write(_data)
            lang = _LAST_LANG.get(chat_id, "fr")
            qr_module.handle_receipt_scan(bot, chat_id, _path, lang)
        except Exception as e:
            logger.error("Scan reçu impossible : %s", e)
            bot.send_message(chat_id, msg("fr", "crash_fallback"))
        return

    # Document : admin → /kb_import (enrichit la base de connaissances)
    # Document : client → transféré à l'équipe + relance vers le devis
    # (FIX BOSS 03/10 : avant, un client qui envoyait un PDF/brief se
    # heurtait à un "réservé à l'admin" sec et la conversation s'arrêtait).
    if message.document:
        _caption = strip_invisible_chars(message.caption or "") if message.caption else ""
        detected_lang = detect_language(_caption) if _caption else "fr"
        safe_typing(chat_id)
        if kb_import.is_admin(chat_id):
            kb_import.handle_document(bot, message, detected_lang)
        else:
            actions.handle_client_document(bot, chat_id, message, detected_lang)
        return

    # Vocal/audio → transcription 100% locale (Whisper embarqué)
    if not message.text and (message.voice or message.audio):
        safe_typing(chat_id)
        text = transcribe_voice(bot, message) if VOICE_ENABLED else None
        text = strip_invisible_chars(text) if text else text
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
    # Lot 21 : nettoyage des caractères invisibles (LRM/RLM \u200e-\u200f,
    # zero-width \u200b, BOM \ufeff...) copiés-collés depuis WhatsApp/iOS.
    # Sans ça, "/apprends" devient "\u200e/apprends" : la commande n'est
    # plus reconnue et le message tombe dans la recherche KB normale, qui
    # répond n'importe quoi (bug signalé : "j'ajoute une connaissance et
    # le bot envoie n'importe quoi").
    user_text = strip_invisible_chars(message.text).strip()
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
    # LETTRE MASTER — la langue PERSISTE : un message sans signal clair
    # (émoji, « ok »...) garde la langue de la conversation ; sinon le
    # moteur (darija salam/khoya → ar, hola/quiero → es) décide.
    _LAST_LANG[chat_id] = detect_language_session(
        user_text, _LAST_LANG.get(chat_id, DEFAULT_LANGUAGE))
    # 💾 RÈGLE BOSS (02/10) : miroir de TOUT ce que le bot collecte vers le
    # Google Sheet dédié (jamais de fichier local, jamais GitHub/Railway).
    try:
        from memory_sheets import log_conversation
        log_conversation(chat_id,
                         (getattr(_fu, "first_name", "") or "")[:100],
                         "client", user_text, _LAST_LANG[chat_id])
    except Exception:
        logger.debug("Miroir conversation entrante impossible", exc_info=True)
    _process_text(chat_id, user_text, _LAST_LANG[chat_id])


_LAST_LANG: dict[int, str] = {}


@bot.message_handler(func=lambda message: True, content_types=['text', 'voice', 'audio', 'document', 'location', 'photo'])
def handle_message(message: telebot.types.Message) -> None:
    """Point d'entrée enregistré. ANTI-SILENCE : aucune exception ne sort
    jamais d'ici sans que le client reçoive une réponse de secours.
    ANTI-DOUBLON : règle d'or — jamais 2 messages pour un seul événement,
    même si Telegram (ou un chevauchement de redéploiement) délivre le
    même update deux fois."""
    _event_key = f"msg:{message.chat.id}:{message.message_id}"
    if _mark_processed_or_duplicate(_event_key):
        logger.warning("Doublon ignoré (anti-double-envoi) : %s", _event_key)
        return
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
    Telegram est toujours levé, même en cas d'erreur.
    ANTI-DOUBLON : même garde que handle_message, sur call.id."""
    chat_id = call.message.chat.id if call.message else None
    if _mark_processed_or_duplicate(f"cb:{call.id}"):
        logger.warning("Doublon de clic ignoré (anti-double-envoi) : cb:%s", call.id)
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return
    lang = _LAST_LANG.get(chat_id, "fr") if chat_id else "fr"
    try:
        if img_gen.handle_callback(bot, call, lang):
            return
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

# 🚫 Message « juste de la ponctuation » (règle d'or du 29/09) : « [[ », « / »,
# « . », « '», « // », « ... » etc. — pas un mot, pas une phrase. Le bot ne
# tente PAS de deviner : il répond une seule fois et propose les boutons.
_PUNCT_CHARS = set(string.punctuation) | {"…", "«", "»", "‘", "’", "“", "”", "–", "—", "،", "؛", "؟"}

def _is_punctuation_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    has_punct = False
    for ch in stripped:
        if ch.isspace():
            continue
        if ch in _PUNCT_CHARS:
            has_punct = True
            continue
        return False  # lettre, chiffre ou emoji -> pas "ponctuation seule"
    return has_punct

# LETTRE MASTER — mots qui changent la langue de la conversation.
LANG_SWITCH_WORDS: dict[str, str] = {
    "english": "en", "en": "en",
    "espanol": "es", "español": "es", "es": "es",
    "arabe": "ar", "ar": "ar", "العربية": "ar",
    "francais": "fr", "français": "fr", "fr": "fr",
}

_PUNCT_ONLY_REPLY = {
    "fr": "J'ai pas compris votre demande boss!! Ou cliquez sur un bouton ci-dessous 👇🏾👇🏾",
    "en": "I didn't get your request boss!! Or tap a button below 👇🏾👇🏾",
    "es": "No entendí su solicitud jefe!! O toque un botón abajo 👇🏾👇🏾",
    "ar": "لم أفهم طلبك يا رئيس!! أو اضغط على زر أدناه 👇🏾👇🏾",
}

def _process_text(chat_id: int, user_text: str, detected_lang: str,
                    reply_voice: bool = False) -> None:
    _LAST_LANG[chat_id] = detected_lang

    # 0. RÈGLE D'OR : message composé UNIQUEMENT de ponctuation (pas un mot,
    # pas une phrase) → une seule réponse fixe + boutons. Priorité absolue,
    # avant même la salutation personnalisée, pour ne jamais empiler 2 msg.
    if _is_punctuation_only(user_text):
        reply = _PUNCT_ONLY_REPLY.get(detected_lang, _PUNCT_ONLY_REPLY["fr"])
        remember(chat_id, "user", user_text)
        remember(chat_id, "assistant", reply)
        human_pause(chat_id)
        bot.send_message(chat_id, reply, reply_markup=menu_for_lang(detected_lang))
        return
    # 🧠 MÉMOIRE CLIENT : un client connu qui salue reçoit une salutation
    # PERSONNALISÉE avec son prénom — le bot se souvient de lui.
    low_txt = user_text.lower().strip()
    if (chat_id not in _GREETED_SESSION
            and low_txt in actions.GREETING_WORDS):
        _GREETED_SESSION.add(chat_id)
        _client = actions.get_client(chat_id)
        # LETTRE MASTER : « Re-bonjour, content de te revoir » est réservé
        # aux clients REVENANTS (historique en base). Un NOUVEAU client,
        # même avec un prénom Telegram, continue vers l'accueil normal.
        if _client and _client.get("name") and has_prior_history(chat_id):
            remember(chat_id, "user", user_text)
            _greet = (_RETURN_GREETING.get(detected_lang)
                      or _RETURN_GREETING["fr"]).replace("{name}", _client["name"])
            remember(chat_id, "assistant", _greet)
            human_pause(chat_id)
            bot.send_message(chat_id, _greet,
                             reply_markup=menu_for_lang(detected_lang))
            return
    # 0b. LETTRE MASTER — moteur langue : le client change de langue
    # d'un mot (« english », « espanol », « العربية », « francais »...).
    # La langue persiste ensuite pour toute la conversation (les messages
    # courts sans signal clair ne repartent plus en FR par défaut).
    lang_switch = LANG_SWITCH_WORDS.get(low_txt)
    if lang_switch:
        _LAST_LANG[chat_id] = lang_switch
        remember(chat_id, "user", user_text)
        confirm = msg(lang_switch, "lang_switched")
        remember(chat_id, "assistant", confirm)
        bot.send_message(chat_id, confirm, reply_markup=menu_for_lang(lang_switch))
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

    # 1a. FIX PRIORITÉ PANIER (lettre finale — Screen 1) : les commandes
    # panier ne sont JAMAIS avalées par un flux en cours.
    low_text = user_text.strip().lower()
    if (catalogue.ADD_RE.match(low_text)
            or low_text in catalogue.CART_TRIGGERS
            or low_text in catalogue.CLEAR_TRIGGERS):
        if catalogue.handle_client(bot, chat_id, user_text, detected_lang):
            return

    # 1ab. « payer » : boutons PayPal/Stripe si activés (/paiement on),
    # sinon message config € fixe + acompte 30€ + JE COMMENCE (lettre,
    # Screen 7). Toujours AVANT les flux : jamais avalé.
    if low_text in qr_module.PAY_TRIGGERS:
        safe_typing(chat_id)
        try:
            import payment_links
            if payment_links.send_payment_card(bot, chat_id, detected_lang):
                return
        except Exception as e:
            logger.error("Carte paiement multi-mode : %s", e)
        if qr_module.handle_pay_request(bot, chat_id, detected_lang):
            return

    # 1aaa. RÈGLE D'OR (02/10) : les BOUTONS du menu ne sont JAMAIS avalés
    # par un flux actif. Le routeur _handle_menu_button est appelé AVANT
    # actions.handle : sans lui, un bouton d'info (Catalogue, Tarifs,
    # Portfolio...) était mangé comme « réponse » à l'étape du devis/rdv.
    if user_text in BUTTON_LABELS and _handle_menu_button(chat_id, user_text, detected_lang):
        return

    # 1abb. « devis » / « je commence » avec un PANIER NON VIDE → tunnel
    # panier prioritaire (lettre : Priorité panier). Sans panier, le flux
    # devis normal prend le relais plus bas.
    if low_text in ("devis", "quote", "presupuesto", "عرض سعر",
                   "je commence", "j'achète"):
        if catalogue.start_checkout(bot, chat_id, detected_lang):
            return

    # 1bis. Flux exécutables (commande, RDV, devis, lead, sondage) — 100% local
    if actions.handle(bot, chat_id, user_text, detected_lang):
        return

    # 1sex. Feature #4 : machine commerciale — ordre strict
    # qualification > devis > downsell > bump > docs, pilotée par client_step
    import commercial_pack
    if commercial_pack.handle(bot, chat_id, user_text, detected_lang):
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
            catalogue.show_catalogue_inline(bot, chat_id, detected_lang)
            return
        if "Panier" in user_text or "Cart" in user_text or "Carrito" in user_text or "سلة" in user_text:
            catalogue.show_cart(bot, chat_id, detected_lang)
            return
        if "Chatbot" in user_text or "IA" in user_text or "ذكي" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "chatbot"), reply_markup=menu_for_lang(detected_lang))
            actions.start_chatbot_qualify_flow(chat_id)
            return
        if "humain" in user_text.lower() or "human" in user_text.lower() or "مستشار" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "human"), reply_markup=menu_for_lang(detected_lang), parse_mode="Markdown")
            return
        if "Portfolio" in user_text or "المعرض" in user_text or "Portafolio" in user_text:
            send_portfolio(chat_id, detected_lang)
            return
        if "Tarif" in user_text or "Pricing" in user_text or "السعر" in user_text or "Precio" in user_text:
            catalogue.show_catalogue_inline(bot, chat_id, detected_lang)
            return

    # 3. Gestion du Portfolio image — par titre (bouton 📷 nom)
    if user_text.startswith(PORTFOLIO_BUTTON_PREFIX):
        display_name = user_text[len(PORTFOLIO_BUTTON_PREFIX):]
        if not send_portfolio_image(chat_id, display_name, detected_lang):
            send_portfolio(chat_id, detected_lang)
        return

    # 3ter. BUG BOSS 07/10 (screenshot) : le mot « Portfolio » tapé au clavier
    # recevait une réponse texte générique du Sheet au lieu de la liste
    # numérotée ; et le TITRE seul (« owner portrait office ») n'était pas
    # reconnu alors que la règle est « numéro OU titre ». Ces deux cas sont
    # désormais déterministes, sans dépendre d'une fiche du Sheet.
    _norm_in = re.sub(r"[^\w\s]", " ", user_text.casefold()).strip()
    _norm_in = re.sub(r"\s+", " ", _norm_in)
    if _norm_in in _PORTFOLIO_WORDS:
        send_portfolio(chat_id, detected_lang)
        return
    if portfolio_images() and len(_norm_in) >= 4:
        for _i, (_name, _src) in enumerate(portfolio_images(), start=1):
            if re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", _name.casefold())).strip() == _norm_in:
                if send_portfolio_image_by_index(chat_id, _i, detected_lang):
                    return
                break

    # 3bis. Chiffre nu après une LISTE NUMÉROTÉE (catalogue OU portfolio).
    # FIX BOSS (03/10, screenshot) : "3" tapé juste après le catalogue
    # ("3. Agent IA Premium — 150€ ... tape 'ajouter <numéro>'") partait
    # systématiquement sur le portfolio (règle aveugle), le client ne
    # pouvait jamais ajouter un produit au panier juste avec le chiffre.
    # list_context retient QUELLE liste a été envoyée en dernier à CE
    # client et route le chiffre vers la bonne action.
    numero_match = re.fullmatch(r"[1-9]️?⃣?\.?\s*", user_text)
    if numero_match:
        digits = re.sub(r"[^\d]", "", user_text)
        if digits:
            index = int(digits)
            ctx = list_context.get_context(chat_id)
            if ctx == "catalogue":
                if catalogue.handle_client(bot, chat_id, f"ajouter {index}", detected_lang):
                    return
            elif ctx == "services" and index in (1, 2, 3, 4):
                # BUG BOSS 07/10 (screenshot) : le pitch KB promet « Tape 1️⃣
                # pour BOT / 2️⃣ SITE / 3️⃣ LOGO / 4️⃣ DEVIS » mais le « 1 »
                # tombait sur la liste Portfolio. Le chiffre suit le menu
                # qui vient d'être envoyé.
                list_context.clear_context(chat_id)
                remember(chat_id, "user", user_text)
                if _route_services_choice(chat_id, index, detected_lang):
                    return
            elif portfolio_images():
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

    # Fix routage (Boss 03/10) : les RÉPONSES au pitch image (« Oui »,
    # « lance la version pro », « variante », « non ») sont consommées ICI,
    # dans leur contexte — jamais de fiche KB au hasard après une image.
    if img_gen.handle_pro_followup(bot, chat_id, user_text, detected_lang):
        return

    # Garde anti-divulgation : jamais de clés, IDs, algorithme ou conception
    if is_secret_probe(user_text, detected_lang):
        secret_reply(bot, chat_id, detected_lang)
        return

    local_response = local_contextual_response(chat_id, user_text, detected_lang)
    if local_response is None:
        record_unrecognized(user_text, source="telegram")
        # Lot 20 : chaque question sans réponse part dans le Google Sheet
        # dédié (memory_sheets) ET est notifiée à l'admin.
        try:
            actions.notify_unanswered(bot, chat_id, user_text, detected_lang)
        except Exception:
            logger.exception("notify_unanswered a échoué")

    # BUG corrigé : un client qui tape "Portfolio"/"vos exemples" au clavier
    # (au lieu de cliquer le bouton "📂 Portfolio") recevait une réponse
    # texte générique au lieu des VRAIES images. Le kb.json marque ces
    # questions avec un sentinel PORTFOLIO_SENTINEL ; le scoring sémantique
    # existant (qui distingue déjà bien "vos exemples" de "montre moi un
    # exemple de bot") décide, puis on redirige ici vers le vrai portfolio.
    if local_response == PORTFOLIO_SENTINEL:
        send_portfolio(chat_id, detected_lang)
        return

    # Lot 20 : invitation Formation IA → texte réel + le prochain message
    # du client est capturé comme infos d'inscription (notif admin).
    if local_response == FORMATION_SENTINEL:
        _msg = actions.FORMATION_MSGS.get(detected_lang, actions.FORMATION_MSGS["fr"])
        bot.send_message(chat_id, _msg)
        remember(chat_id, "assistant", _msg)
        actions.set_pending_formation(chat_id)
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
    if local_response and _SERVICES_MENU_RE.search(response.rstrip()):
        list_context.set_context(chat_id, "services")

    if len(response) > 4096:
        for i in range(0, len(response), 4096):
            chunk = response[i:i + 4096]
            is_last = (i + 4096 >= len(response))
            bot.send_message(chat_id, chunk, reply_markup=menu_for_lang(detected_lang) if is_last else None)
    else:
        bot.send_message(chat_id, response, reply_markup=menu_for_lang(detected_lang))
    if local_response:
        _send_demo_image_for(chat_id, response)

    # Réponse parlée si le client a écrit en vocal (synthèse vocale locale)
    if reply_voice:
        tts.reply_with_voice(bot, chat_id, response, detected_lang)


# ── Image de démo jointe à certaines réponses (Boss 08/10) ─────────────
# « c'est quoi un bot » -> la fiche promet « je t'en montre un en action » :
# on joint l'image de démo du portfolio (avant/après workflow WhatsApp).
# Clé = début de la réponse du bot ; valeur = mot du nom de fichier du
# portfolio. Modifiable ici sans toucher à la logique.
DEMO_IMAGE_BY_ANSWER: dict[str, str] = {
    "Un bot 🤖 c'est ton employé digital": "avant_apres_whatsapp",
}


def _send_demo_image_for(chat_id: int, response: str) -> None:
    """Joint l'image de démo si la réponse correspond. Silencieux en cas
    d'échec : l'image est un bonus, la réponse texte est déjà partie."""
    try:
        keyword = next((kw for start, kw in DEMO_IMAGE_BY_ANSWER.items()
                        if response.startswith(start)), None)
        if not keyword:
            return
        for name, source in portfolio_images():
            if keyword in name.lower().replace(" ", "_"):
                import io
                if isinstance(source, tuple) and source[0] == "drive":
                    data = portfolio_drive.download_image(source[1])
                    if data:
                        bot.send_photo(chat_id, io.BytesIO(data), caption="🤖 Un bot en action")
                else:
                    path = source[1] if isinstance(source, tuple) else source
                    with path.open("rb") as image_file:
                        bot.send_photo(chat_id, image_file, caption="🤖 Un bot en action")
                return
    except Exception:
        logger.debug("Image de démo non envoyée", exc_info=True)

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
    text = SECRET_REPLIES.get(lang, SECRET_REPLIES["fr"])
    remember(chat_id, "assistant", text)
    # FIX (03/10) : la phrase finit sur "Tu as un projet en tête ?" — un
    # "oui" qui suit doit démarrer la commande, pas retomber sur un vieux
    # "oui" appris pour un tout autre contexte (voir post_cta).
    actions.offer_order_cta(bot_, chat_id, lang, text, reply_markup=menu_for_lang(lang))


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

def _install_reply_mirroring() -> None:
    """Wrap bot.send_message : chaque réponse envoyée est miroitée vers
    l'onglet « Conversations » du Google Sheet dédié. Jamais bloquant."""
    global bot
    _orig = bot.send_message
    if getattr(_orig, "_komara_mirror", False):
        return  # déjà wrappé (run() rappelé après crash)

    def _send_and_mirror(chat_id, text=None, *args, **kwargs):
        result = _orig(chat_id, text, *args, **kwargs)
        try:
            from memory_sheets import log_conversation
            log_conversation(chat_id, "", "bot", str(text), _LAST_LANG.get(chat_id, ""))
        except Exception:
            pass
        return result

    _send_and_mirror._komara_mirror = True
    bot.send_message = _send_and_mirror
    logger.info("Miroir des réponses vers Google Sheets activé")


def run() -> None:
    global _shutdown_requested

    init_memory_db()
    _install_reply_mirroring()
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
    # Évaluation hebdo mémoire Aya chaque lundi 08h00 UTC (Boss 05/10)
    threading.Thread(target=aya_eval_cron.scheduler_loop, args=(bot,),
                     name="aya-eval", daemon=True).start()
    # Sauvegarde automatique de la base → Google Drive chaque nuit 03h00
    threading.Thread(target=backup_drive.scheduler_loop,
                     name="drive-backup", daemon=True).start()
    # Lot 22 (F2/F5/F6) : crons commerciaux locaux — relances J+1/J+3/J+7,
    # parrainage/recouvrement/upsell/winback, assurance MRR mensuelle.
    commercial_cron.start_all(bot)
    run()
