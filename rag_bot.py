"""Worker Telegram Komara avec polling robuste, multilingue et mémoire locale."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

import telebot
from dotenv import load_dotenv
from telebot.apihelper import ApiTelegramException
from telebot.types import ReplyKeyboardMarkup

from local_search import trouver_meilleure_reponse
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

# ---------------------------------------------------------------------------
# Détecteur de langue local (100% hors-ligne, aucune dépendance externe)
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
    """Détecte la langue d'un texte à partir de mots-clés caractéristiques."""
    if not text or not text.strip():
        return DEFAULT_LANGUAGE

    text_lower = text.lower()
    scores: dict[str, int] = {}

    for lang, config in LANGUAGE_MARKERS.items():
        score = 0
        for word in config.get("words", set()):
            if f" {word} " in f" {text_lower} ":
                score += 2
            elif text_lower.startswith(f"{word} ") or text_lower.endswith(f" {word}"):
                score += 2
        for pattern in config.get("patterns", []):
            score += text_lower.count(pattern)
        for start, end in config.get("unicode_ranges", []):
            score += sum(1 for char in text if start <= ord(char) <= end)
        if score > 0:
            scores[lang] = score

    if not scores:
        return DEFAULT_LANGUAGE

    best_lang = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best_lang] < MIN_CONFIDENCE:
        return DEFAULT_LANGUAGE
    return best_lang


def get_supported_languages() -> list[str]:
    """Retourne la liste des langues supportées."""
    return list(LANGUAGE_MARKERS.keys())


# ---------------------------------------------------------------------------
# Chargement multilingue des bases de connaissances
# ---------------------------------------------------------------------------

KB_PATH = BASE_DIR / "kb.json"
FAQ_PATH = BASE_DIR / "docs" / "faq.md"
DIALOGUES_DIR = BASE_DIR / "docs" / "dialogues"
LANG_DIR = BASE_DIR / "lang"


def load_knowledge_base() -> dict[str, Any]:
    """Charge la base de connaissances française de secours (racine)."""
    if not KB_PATH.is_file():
        logger.error("Le fichier kb.json est absent : %s", KB_PATH)
        raise RuntimeError(f"Le fichier de base de connaissances {KB_PATH} est absent.")
    with KB_PATH.open("r", encoding="utf-8") as kb_file:
        data = json.load(kb_file)
        logger.info("Base de connaissances (kb.json) chargée avec succès.")
        return data


def load_local_faq() -> list[dict[str, str]]:
    """Charge la FAQ française de secours (racine)."""
    if not FAQ_PATH.is_file():
        logger.warning("FAQ locale absente : %s", FAQ_PATH)
        return []
    content = FAQ_PATH.read_text(encoding="utf-8")
    items: list[dict[str, str]] = []
    for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
        lines = section.splitlines()
        if len(lines) < 2:
            continue
        question = lines[0].strip()
        answer = "\n".join(lines[1:]).strip()
        if question and answer:
            items.append({"question": question, "answer": answer})
    logger.info("FAQ locale (docs/faq.md) chargée : %s questions", len(items))
    return items


def load_dialogues() -> list[dict[str, str]]:
    """Charge les dialogues français de secours (racine)."""
    dialogues: list[dict[str, str]] = []
    if not DIALOGUES_DIR.is_dir():
        logger.warning("Dossier dialogues absent : %s", DIALOGUES_DIR)
        return dialogues
    for file_path in DIALOGUES_DIR.iterdir():
        if file_path.is_file() and file_path.suffix in {".md", ".txt"}:
            try:
                content = file_path.read_text(encoding="utf-8")
                for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
                    lines = section.splitlines()
                    if len(lines) < 2:
                        continue
                    question = lines[0].strip()
                    answer = "\n".join(lines[1:]).strip()
                    if question and answer:
                        dialogues.append({"question": question, "answer": answer})
            except Exception as e:
                logger.error("Erreur lors de la lecture de %s : %s", file_path.name, e)
    logger.info("Dialogues (docs/dialogues) chargés : %s questions", len(dialogues))
    return dialogues


def load_language_resources(lang_code: str) -> dict[str, Any]:
    """Charge kb.json, faq.md et dialogues pour une langue donnée depuis lang/{code}/."""
    lang_path = LANG_DIR / lang_code
    resources: dict[str, Any] = {"kb": [], "faq": [], "dialogues": []}

    # 1. kb.json de la langue
    kb_path = lang_path / "kb.json"
    if kb_path.is_file():
        try:
            with kb_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                resources["kb"] = data.get("knowledge", [])
                logger.info("[%s](kb.json) chargé : %s fiches", lang_code, len(resources["kb"]))
        except Exception as e:
            logger.error("[%s] Erreur kb.json : %s", lang_code, e)

    # 2. faq.md de la langue
    faq_path = lang_path / "faq.md"
    if faq_path.is_file():
        try:
            content = faq_path.read_text(encoding="utf-8")
            items: list[dict[str, str]] = []
            for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
                lines = section.splitlines()
                if len(lines) < 2:
                    continue
                question = lines[0].strip()
                answer = "\n".join(lines[1:]).strip()
                if question and answer:
                    items.append({"question": question, "answer": answer})
            resources["faq"] = items
            logger.info("[%s](faq.md) chargé : %s questions", lang_code, len(items))
        except Exception as e:
            logger.error("[%s] Erreur faq.md : %s", lang_code, e)

    # 3. dialogues/ de la langue
    dialogues_path = lang_path / "dialogues"
    if dialogues_path.is_dir():
        dialogues: list[dict[str, str]] = []
        for file_path in dialogues_path.iterdir():
            if file_path.is_file() and file_path.suffix in {".md", ".txt"}:
                try:
                    content = file_path.read_text(encoding="utf-8")
                    for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
                        lines = section.splitlines()
                        if len(lines) < 2:
                            continue
                        question = lines[0].strip()
                        answer = "\n".join(lines[1:]).strip()
                        if question and answer:
                            dialogues.append({"question": question, "answer": answer})
                except Exception as e:
                    logger.error("[%s] Erreur dialogue %s : %s", lang_code, file_path.name, e)
        resources["dialogues"] = dialogues
        logger.info("[%s] Dialogues chargés : %s questions", lang_code, len(dialogues))

    return resources


# ---------------------------------------------------------------------------
# Initialisation des ressources au démarrage
# ---------------------------------------------------------------------------

LANG_RESOURCES: dict[str, dict[str, Any]] = {}

for _lang in get_supported_languages():
    LANG_RESOURCES[_lang] = load_language_resources(_lang)

# Fallback français : si lang/fr/ est vide, on utilise l'ancienne structure à la racine
_fr = LANG_RESOURCES.get("fr", {"kb": [], "faq": [], "dialogues": []})
if not _fr["kb"]:
    try:
        BRAIN = load_knowledge_base()
        LANG_RESOURCES["fr"] = {
            "kb": BRAIN.get("knowledge", []),
            "faq": load_local_faq(),
            "dialogues": load_dialogues(),
        }
        logger.info("Base française de secours (racine) chargée en fallback.")
    except RuntimeError as e:
        logger.critical(e)
        sys.exit(1)

# Variables globales (compatibilité avec le français)
BRAIN = BRAIN if "BRAIN" in globals() else (load_knowledge_base() if KB_PATH.is_file() else {})
WHATSAPP = BRAIN.get("contact", {}).get("whatsapp", "+212701986219")
BRAND = BRAIN.get("brand", "Komara Agency")
PACKS = BRAIN.get("packs", [])

MEMORY_FILE = Path(os.getenv("MEMORY_FILE", str(BASE_DIR / "data" / "memory.json")))
MEMORY_LIMIT = max(2, int(os.getenv("MEMORY_LIMIT", "12")))
MEMORY_LOCK = threading.Lock()

# Vérification qu'au moins une langue a du contenu
if not any(res["kb"] for res in LANG_RESOURCES.values()):
    raise RuntimeError("Aucune base de connaissances trouvée (ni dans lang/ ni à la racine).")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
_shutdown_requested = False

# ---------------------------------------------------------------------------
# Arrêt propre
# ---------------------------------------------------------------------------

def request_shutdown(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    HEARTBEAT_STOP.set()
    logger.info("Signal %s reçu : arrêt propre demandé.", signum)

signal.signal(signal.SIGTERM, request_shutdown)
signal.signal(signal.SIGINT, request_shutdown)


def send_heartbeat() -> None:
    if not MONITOR_API_URL:
        return
    payload = json.dumps({"worker": "telegram"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if MONITOR_API_KEY:
        headers["X-API-Key"] = MONITOR_API_KEY
    request = urllib.request.Request(
        f"{MONITOR_API_URL}/internal/heartbeat",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status >= 300:
                logger.warning("Heartbeat refusé par le monitoring : HTTP %s", response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.warning("Heartbeat monitoring indisponible : %s", error)


def heartbeat_loop() -> None:
    if not MONITOR_API_URL:
        logger.info("Monitoring désactivé : MONITOR_API_URL non configurée.")
        return
    logger.info("Monitoring activé : heartbeat toutes les %ss.", HEARTBEAT_INTERVAL)
    while not HEARTBEAT_STOP.is_set() and not _shutdown_requested:
        send_heartbeat()
        HEARTBEAT_STOP.wait(HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Recherche multilingue
# ---------------------------------------------------------------------------

def trouver_meilleure_reponse_multilingue(message: str, detected_lang: str) -> str | None:
    """
    Cherche la meilleure réponse dans la base de la langue détectée.
    Fallback sur la langue par défaut (fr) si aucun résultat.
    """
    resources = LANG_RESOURCES.get(detected_lang, {"kb": [], "faq": [], "dialogues": []})
    result = trouver_meilleure_reponse(
        message, resources["kb"], resources["faq"], resources["dialogues"]
    )
    if result:
        return result

    # Fallback français
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

# Claviers adaptés à chaque langue
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

# Commandes de reset par langue
RESET_COMMANDS: dict[str, set[str]] = {
    "fr": {"/reset", "/forget", "oublie", "oublie-moi"},
    "en": {"/reset", "/forget", "forget", "reset"},
    "ar": {"/reset", "/forget", "نسيان", "مسح"},
    "es": {"/reset", "/forget", "olvida", "reiniciar"},
}

# Messages de réponse par langue
MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "reset": "D'accord, j'ai effacé le contexte de cette conversation. Que souhaitez-vous faire ?",
        "fallback": "Pour mieux vous orienter, pouvez-vous me préciser votre activité et ce que vous souhaitez vendre ou automatiser ?",
        "human": f"Expert KOMARA vous contacte sur *{WHATSAPP}* sous 5 minutes.",
        "portfolio": "Tu veux des exemples pour quel domaine ?",
        "pricing_intro": "Voici nos offres :",
        "error": "Désolé, une erreur temporaire est survenue. Un expert KOMARA vous contacte.",
    },
    "en": {
        "reset": "Done, I've cleared the context. What would you like to do?",
        "fallback": "To better assist you, could you tell me more about your business and what you'd like to sell or automate?",
        "human": f"A KOMARA expert will contact you on *{WHATSAPP}* within 5 minutes.",
        "portfolio": "What kind of examples are you looking for?",
        "pricing_intro": "Here are our offers:",
        "error": "Sorry, a temporary error occurred. A KOMARA expert will contact you.",
    },
    "ar": {
        "reset": "تم مسح السياق. ماذا تريد أن تفعل؟",
        "fallback": "لمساعدتك بشكل أفضل، هل يمكنك إخباري المزيد عن نشاطك وما تريد بيعه أو أتمتته؟",
        "human": f"سيتواصل معك خبير KOMARA على *{WHATSAPP}* خلال 5 دقائق.",
        "portfolio": "ما نوع الأمثلة التي تبحث عنها؟",
        "pricing_intro": "إليك عروضنا:",
        "error": "عذراً، حدث خطأ مؤقت. سيتواصل معك خبير من KOMARA.",
    },
    "es": {
        "reset": "Listo, he borrado el contexto. ¿Qué quieres hacer?",
        "fallback": "Para orientarte mejor, ¿puedes decirme más sobre tu negocio y qué te gustaría vender o automatizar?",
        "human": f"Un experto de KOMARA te contactará en *{WHATSAPP}* en menos de 5 minutos.",
        "portfolio": "¿Qué tipo de ejemplos estás buscando?",
        "pricing_intro": "Aquí están nuestras ofertas:",
        "error": "Lo siento, ocurrió un error temporal. Un experto de KOMARA te contactará.",
    },
}


def menu_for_lang(lang: str) -> ReplyKeyboardMarkup:
    """Retourne le clavier adapté à la langue détectée."""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for row in KEYBOARDS.get(lang, KEYBOARDS["fr"]):
        keyboard.add(*row)
    return keyboard


def msg(lang: str, key: str) -> str:
    """Retourne un message localisé, fallback français."""
    return MESSAGES.get(lang, MESSAGES["fr"]).get(key, MESSAGES["fr"].get(key, ""))


def _load_memory() -> dict[str, list[dict[str, str]]]:
    try:
        with MEMORY_LOCK:
            if not MEMORY_FILE.exists():
                return {}
            with MEMORY_FILE.open("r", encoding="utf-8") as memory_file:
                data = json.load(memory_file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Mémoire illisible; démarrage avec une mémoire vide.", exc_info=True)
        return {}


def _save_memory(memory: dict[str, list[dict[str, str]]]) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = MEMORY_FILE.with_suffix(".tmp")
    with MEMORY_LOCK:
        with temporary_file.open("w", encoding="utf-8") as memory_file:
            json.dump(memory, memory_file, ensure_ascii=False, indent=2)
        temporary_file.replace(MEMORY_FILE)


def remember(chat_id: int, role: str, content: str) -> list[dict[str, str]]:
    memory = _load_memory()
    key = str(chat_id)
    history = deque(memory.get(key, []), maxlen=MEMORY_LIMIT)
    history.append({"role": role, "content": content[:4000]})
    memory[key] = list(history)
    _save_memory(memory)
    return memory[key]


def forget(chat_id: int) -> None:
    memory = _load_memory()
    memory.pop(str(chat_id), None)
    _save_memory(memory)


def context_for(chat_id: int) -> list[dict[str, str]]:
    return _load_memory().get(str(chat_id), [])[-MEMORY_LIMIT:]


def local_contextual_response(chat_id: int, user_text: str, detected_lang: str) -> str | None:
    """Recherche directe puis contextuelle, dans la langue détectée puis fallback."""
    history = context_for(chat_id)
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {user_text}".strip()

    direct_answer = trouver_meilleure_reponse_multilingue(user_text, detected_lang)
    if direct_answer:
        return direct_answer

    contextual_answer = trouver_meilleure_reponse_multilingue(combined_text, detected_lang)
    if contextual_answer:
        return contextual_answer

    return None


def safe_typing(chat_id: int) -> None:
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        logger.debug("Impossible d'envoyer l'indicateur typing.", exc_info=True)


def send_portfolio(chat_id: int, lang: str) -> None:
    sent = 0
    for filename in ("portfolio_01", "portfolio_02"):
        image_path = BASE_DIR / filename
        if not image_path.is_file():
            logger.warning("Fichier portfolio absent : %s", image_path)
            continue
        try:
            with image_path.open("rb") as image_file:
                bot.send_photo(chat_id, image_file)
            sent += 1
            time.sleep(0.5)
        except Exception:
            logger.exception("Échec d'envoi du portfolio %s", image_path.name)

    prefix = "" if sent else "⚠️ "
    slogan = BRAIN.get("slogan", "") if BRAIN else ""
    text = f"{prefix}Portfolio KOMARA 💎 {slogan}\n{msg(lang, 'portfolio')}"
    bot.send_message(chat_id, text, reply_markup=menu_for_lang(lang))


@bot.message_handler(commands=["start"])
def start(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    text = (message.text or "").strip()
    lang = detect_language(text)

    if text.casefold() == "/start":
        forget(chat_id)
        lang = DEFAULT_LANGUAGE

    safe_typing(chat_id)
    time.sleep(1)

    fr_kb = LANG_RESOURCES.get("fr", {}).get("kb", [])
    welcome = fr_kb[0].get("answer", f"Bienvenue chez {BRAND}.") if fr_kb else f"Bienvenue chez {BRAND}."
    remember(chat_id, "assistant", welcome)
    bot.send_message(chat_id, welcome, reply_markup=menu_for_lang(lang))


@bot.message_handler(func=lambda message: True)
def handle(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    text = (message.text or "").strip()
    safe_typing(chat_id)

    # Détection de la langue
    lang = detect_language(text)
    logger.debug("Langue détectée : %s | message : %s", lang, text[:50])

    # Commandes de reset multilingues
    all_reset_commands = set()
    for cmds in RESET_COMMANDS.values():
        all_reset_commands |= cmds
    if text.casefold() in all_reset_commands:
        forget(chat_id)
        response = msg(lang, "reset")
        remember(chat_id, "user", text)
        remember(chat_id, "assistant", response)
        bot.send_message(chat_id, response, reply_markup=menu_for_lang(lang))
        return

    remember(chat_id, "user", text)

    try:
        # Boutons de menu (reconnaissables dans toutes les langues)
        portfolio_labels = {"📂 Portfolio", "📂 المعرض", "📂 Portafolio"}
        pricing_labels = {"💎 Voir les Tarifs", "💎 View Pricing", "💎 الأسعار", "💎 Ver Precios"}
        human_labels = {"👑 Parler à un humain", "👑 Talk to a human", "👑 التحدث مع مستشار", "👑 Hablar con un humano"}

        if text in portfolio_labels:
            send_portfolio(chat_id, lang)
            return

        if text in pricing_labels:
            base_answer = trouver_meilleure_reponse_multilingue("prix", lang) or msg(lang, "pricing_intro")
            packs_text = "\n".join(
                f"*{pack.get('nom', 'Pack')}*: {pack.get('prix', '')} - "
                f"{pack.get('contenu', '')}"
                for pack in PACKS
            )
            response = f"{base_answer}\n\n{packs_text}" if packs_text else base_answer
            bot.send_message(chat_id, response, reply_markup=menu_for_lang(lang))
            return

        if text in human_labels:
            response = msg(lang, "human")
            remember(chat_id, "assistant", response)
            bot.send_message(chat_id, response, reply_markup=menu_for_lang(lang))
            return

        # Recherche multilingue
        local_response = local_contextual_response(chat_id, text, lang)
        if local_response is None:
            record_unrecognized(text, source="telegram")

        response = local_response or msg(lang, "fallback")
        remember(chat_id, "assistant", response)
        time.sleep(min(2, len(response) / 200))
        bot.send_message(chat_id, response, reply_markup=menu_for_lang(lang))

    except ApiTelegramException:
        raise
    except Exception:
        logger.exception("Erreur lors du traitement du message du chat %s", chat_id)
        try:
            bot.send_message(
                chat_id,
                msg(lang, "error"),
                reply_markup=menu_for_lang(lang),
            )
        except Exception:
            logger.exception("Impossible d'envoyer le message de secours.")


# ---------------------------------------------------------------------------
# Polling et stratégie de reprise
# ---------------------------------------------------------------------------

def prepare_polling() -> None:
    logger.info("Suppression du webhook Telegram avant le polling.")
    bot.delete_webhook(drop_pending_updates=DROP_PENDING_UPDATES)


def is_conflict(error: ApiTelegramException) -> bool:
    description = str(getattr(error, "description", error)).casefold()
    return getattr(error, "error_code", None) == 409 or "terminated by other" in description


def run() -> None:
    global _shutdown_requested
    retry_count = 0
    threading.Thread(target=heartbeat_loop, name="worker-heartbeat", daemon=True).start()
    logger.info(
        "%s démarrage (multilingue : %s) ; polling_timeout=%ss, long_polling_timeout=%ss, "
        "drop_pending_updates=%s",
        BRAND,
        ", ".join(get_supported_languages()),
        POLL_TIMEOUT,
        LONG_POLLING_TIMEOUT,
        DROP_PENDING_UPDATES,
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
                logger.critical(
                    "Conflit Telegram 409 : une autre instance utilise déjà ce token. "
                    "Arrêt sans boucle de redémarrage. Vérifiez Railway, les replicas et "
                    "les autres hébergeurs avant de relancer. Détail : %s",
                    error,
                )
                raise SystemExit(2) from error

            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.critical("Trop d'erreurs Telegram consécutives ; arrêt du worker.")
                raise

            delay = min(60, 2 ** min(retry_count, 6))
            logger.exception(
                "Erreur Telegram transitoire (tentative %s/%s) ; reprise dans %ss.",
                retry_count,
                MAX_RETRIES,
                delay,
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
                retry_count,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)

    logger.info("Worker Telegram arrêté proprement.")


if __name__ == "__main__":
    run()
