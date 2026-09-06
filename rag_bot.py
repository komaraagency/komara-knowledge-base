"""Worker Telegram Komara avec polling robuste, multilingue et mémoire locale (SQLite)."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
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

from deepseek_client import ask_deepseek, deepseek_available
from local_search import significant_token_count, trouver_meilleure_reponse
from local_stats import record_unrecognized

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
                items.append({"question": question, "answer": answer})
    return items

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
    if not DIALOGUES_DIR.is_dir():
        logger.warning("Dossier dialogues absent : %s", DIALOGUES_DIR)
        return dialogues
    for file_path in DIALOGUES_DIR.iterdir():
        if file_path.is_file() and file_path.suffix in {".md", ".txt"}:
            try:
                content = file_path.read_text(encoding="utf-8")
                dialogues.extend(_parse_markdown_sections(content))
            except Exception as e:
                logger.error("Erreur lors de la lecture de %s : %s", file_path.name, e)
    logger.info("Dialogues chargés : %s questions", len(dialogues))
    return dialogues

def load_language_resources(lang_code: str) -> dict[str, Any]:
    lang_path = LANG_DIR / lang_code
    resources: dict[str, Any] = {"kb": [], "faq": [], "dialogues": []}

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

    dialogues_path = lang_path / "dialogues.md"
    if dialogues_path.is_file():
        try:
            content = dialogues_path.read_text(encoding="utf-8")
            resources["dialogues"] = _parse_markdown_sections(content)
            logger.info("[%s](dialogues.md) chargé : %s dialogues", lang_code, len(resources["dialogues"]))
        except Exception as e:
            logger.error("[%s] Erreur dialogues.md : %s", lang_code, e)

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

# ---------------------------------------------------------------------------
# Mémoire conversationnelle locale (SQLite avec connexion persistante)
# ---------------------------------------------------------------------------

MEMORY_DIR = Path(os.getenv("MEMORY_DIR", BASE_DIR / "data"))
MEMORY_FILE = MEMORY_DIR / "memory.db"
MEMORY_LIMIT = 20
DB_LOCK = threading.Lock()
DB_CONN: sqlite3.Connection | None = None

def init_memory_db() -> None:
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

    if detected_lang != DEFAULT_LANGUAGE:
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
        ("👑 Parler à un humain",),
    ],
    "en": [
        ("💎 View Pricing", "📂 Portfolio"),
        ("🚀 Order", "🤖 AI Chatbot"),
        ("👑 Talk to a human",),
    ],
    "ar": [
        ("💎 الأسعار", "📂 المعرض"),
        ("🚀 طلب", "🤖 مساعد ذكي"),
        ("👑 التحدث مع مستشار",),
    ],
    "es": [
        ("💎 Ver Precios", "📂 Portafolio"),
        ("🚀 Ordenar", "🤖 Chatbot IA"),
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
        "error": "Lo siento, ocurrió un error temporal. Un experto de KOMARA te contactará.",
    },
}

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

    # BUG CORRIGÉ : un message court/ambigu ("Pour l'info", "oui") combiné à
    # l'historique re-matchait à tort une ancienne question (boucle accueil).
    if significant_token_count(user_text) < 2:
        return None

    # 2. Recherche avec contexte de conversation (si assez de contenu propre)
    history = context_for(chat_id)
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {user_text}".strip()

    contextual_answer = trouver_meilleure_reponse_multilingue(combined_text, detected_lang)
    if contextual_answer:
        return contextual_answer

    return None

# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

PORTFOLIO_DIR = BASE_DIR / "portfolio"
PORTFOLIO_BUTTON_PREFIX = "📷 "
PORTFOLIO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

def portfolio_images() -> list[tuple[str, Path]]:
    images: list[tuple[str, Path]] = []
    if not PORTFOLIO_DIR.is_dir():
        return images
    for entry in sorted(PORTFOLIO_DIR.iterdir()):
        if entry.is_file() and entry.suffix.lower() in PORTFOLIO_EXTENSIONS:
            display_name = entry.stem.replace("_", " ").replace("-", " ").strip()
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
    lines = ["Portfolio KOMARA 💎", ""]
    for name, _ in images:
        lines.append(f"📷 {name}")
    lines.append("")
    lines.append(msg(lang, "portfolio"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=portfolio_keyboard())

def send_portfolio_image(chat_id: int, display_name: str, lang: str) -> bool:
    for name, path in portfolio_images():
        if name.lower() == display_name.lower():
            try:
                with path.open("rb") as image_file:
                    bot.send_photo(chat_id, image_file)
                bot.send_message(chat_id, msg(lang, "portfolio"), reply_markup=portfolio_keyboard())
                return True
            except Exception:
                logger.exception("Échec d'envoi de l'image portfolio %s", path.name)
                return False
    return False

# ---------------------------------------------------------------------------
# Handler principal des messages
# ---------------------------------------------------------------------------

def safe_typing(chat_id: int) -> None:
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        logger.debug("Impossible d'envoyer l'indicateur typing.", exc_info=True)

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_text = message.text.strip()
    detected_lang = detect_language(user_text)

    # 1. Commandes de reset
    if user_text.lower() in RESET_COMMANDS.get(detected_lang, set()):
        forget(chat_id)
        bot.send_message(chat_id, msg(detected_lang, "reset"), reply_markup=menu_for_lang(detected_lang))
        return

    # 2. Gestion des boutons rapides (FIX : test sur le set aplati BUTTON_LABELS)
    if user_text in BUTTON_LABELS:
        if "Commander" in user_text or "Order" in user_text or "طلب" in user_text or "Ordenar" in user_text:
            bot.send_message(chat_id, msg(detected_lang, "commander"), reply_markup=menu_for_lang(detected_lang))
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

    # 3. Gestion du Portfolio image
    if user_text.startswith(PORTFOLIO_BUTTON_PREFIX):
        display_name = user_text[len(PORTFOLIO_BUTTON_PREFIX):]
        if not send_portfolio_image(chat_id, display_name, detected_lang):
            send_portfolio(chat_id, detected_lang)
        return

    # 4. Traitement normal
    safe_typing(chat_id)
    remember(chat_id, "user", user_text)

    # 5. Cascade : DeepSeek PRIORITAIRE (reformule la suggestion locale et
    # garde les chiffres exacts) ; kb.json devient le filet de secours.
    # Sans clé DeepSeek, comportement identique : la suggestion locale répond.
    local_suggestion = local_contextual_response(chat_id, user_text, detected_lang)
    if local_suggestion is None:
        record_unrecognized(user_text, source="telegram")

    response = None
    if deepseek_available():
        history = context_for(chat_id)
        try:
            # FIX : la signature est ask_deepseek(texte, lang, historique, suggestion)
            # (le brouillon passait l'historique à la place du texte du client)
            response = ask_deepseek(
                user_text, lang=detected_lang, history=history, suggestion=local_suggestion
            )
        except Exception:
            logger.exception("Erreur lors de l'appel à DeepSeek")
            response = None

    response = response or local_suggestion or msg(detected_lang, "fallback")

    # 6. Sauvegarde et envoi
    remember(chat_id, "assistant", response)

    if len(response) > 4096:
        for i in range(0, len(response), 4096):
            chunk = response[i:i + 4096]
            is_last = (i + 4096 >= len(response))
            bot.send_message(chat_id, chunk, reply_markup=menu_for_lang(detected_lang) if is_last else None)
    else:
        bot.send_message(chat_id, response, reply_markup=menu_for_lang(detected_lang))

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
                allowed_updates=["message"],
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
    run()
