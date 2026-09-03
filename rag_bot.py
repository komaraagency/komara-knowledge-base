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
from groq_ai import should_use_ai, find_best_kb_answer, generate_contextual_response, is_groq_available
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
            "salut", "salam", "coucou", "hello", "hi",
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
DIALOGUES_DIR = BASE_DIR / "dialogues"
LANG_DIR = BASE_DIR / "lang"


def load_knowledge_base() -> dict[str, Any]:
    """Charge la base de connaissances française de secours (racine)."""
    if not KB_PATH.is_file():
        logger.error("Le fichier kb.json est absent : %s", KB_PATH)
        raise RuntimeError(f"Le fichier de base de connaissances {KB_PATH} est absent.")
    with KB_PATH.open("r", encoding="utf-8") as kb_file:
        data = json.load(kb_file)
        logger.info("Base de connaissances (kb.json) chargé avec succès.")
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

    # 3. dialogues de la langue
    dialogues_path = lang_path / "dialogues.md"
    if dialogues_path.is_file():
        try:
            content = dialogues_path.read_text(encoding="utf-8")
            items: list[dict[str, str]] = []
            for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
                lines = section.splitlines()
                if len(lines) < 2:
                    continue
                question = lines[0].strip()
                answer = "\n".join(lines[1:]).strip()
                if question and answer:
                    items.append({"question": question, "answer": answer})
            resources["dialogues"] = items
            logger.info("[%s](dialogues.md) chargé : %s dialogues", lang_code, len(items))
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
PACKS = KB_DATA.get("packs", [])
CONVERSATIONS = KB_DATA.get("conversations", [])
WHATSAPP = KB_DATA.get("contact", {}).get("whatsapp", "")
LOCAL_FAQ = load_local_faq()
LOCAL_DIALOGUES = load_dialogues()

LANG_RESOURCES: dict[str, dict[str, Any]] = {}
for _lang in get_supported_languages():
    LANG_RESOURCES[_lang] = load_language_resources(_lang)
    logger.info("Ressources [%s] chargées", _lang)

# FIX BUG #2: Le français doit AUSSI avoir accès aux ressources racine (kb.json, faq, dialogues)
# en plus des 10 items de lang/fr/kb.json. On fusionne les deux.
_fr_root_kb = KNOWLEDGE  # 311 items du kb.json racine (déjà au bon format)
_fr_lang_kb = LANG_RESOURCES.get("fr", {}).get("kb", [])  # 10 items de lang/fr/kb.json
_fr_root_faq = LOCAL_FAQ
_fr_lang_faq = LANG_RESOURCES.get("fr", {}).get("faq", [])
_fr_root_dialogues = LOCAL_DIALOGUES
_fr_lang_dialogues = LANG_RESOURCES.get("fr", {}).get("dialogues", [])

LANG_RESOURCES["fr"] = {
    "kb": _fr_lang_kb + _fr_root_kb,       # 10 items lang + 311 items racine
    "faq": _fr_lang_faq + _fr_root_faq,    # faq lang + faq racine
    "dialogues": _fr_lang_dialogues + _fr_root_dialogues,
}
logger.info(
    "Ressources [fr] fusionnées : %s fiches KB, %s FAQ, %s dialogues",
    len(LANG_RESOURCES["fr"]["kb"]),
    len(LANG_RESOURCES["fr"]["faq"]),
    len(LANG_RESOURCES["fr"]["dialogues"]),
)


# ---------------------------------------------------------------------------
# Mémoire conversationnelle locale
# ---------------------------------------------------------------------------

MEMORY_DIR = Path(os.getenv("MEMORY_DIR", BASE_DIR / "data"))
MEMORY_FILE = MEMORY_DIR / "memory.json"
MEMORY_LIMIT = 20
MEMORY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Recherche multilingue
# ---------------------------------------------------------------------------

def trouver_meilleure_reponse_multilingue(message: str, detected_lang: str) -> str | None:
    """
    Cherche la meilleure réponse dans la base de la langue détectée.
    Fallback sur la langue par défaut (fr) si aucun résultat.
    FIX BUG #2: Même pour le français, on cherche dans les ressources fusionnées
    (qui incluent maintenant le kb.json racine).
    """
    resources = LANG_RESOURCES.get(detected_lang, {"kb": [], "faq": [], "dialogues": []})
    result = trouver_meilleure_reponse(
        message, resources["kb"], resources["faq"], resources["dialogues"]
    )
    if result:
        return result

    # Fallback français (uniquement si la langue détectée n'est pas le français)
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
        "fallback": "Je n'ai pas bien compris votre demande. 🤔\n\nNous proposons : bots WhatsApp/Telegram, sites web, applications, logos et création digitale.\n\nTapez 'prix' pour les tarifs, 'services' pour nos offres, ou décrivez votre projet.",
        "human": f"Expert KOMARA vous contacte sur *{WHATSAPP}* sous 5 minutes.",
        "portfolio": "Tu veux des exemples pour quel domaine ?",
        "pricing_intro": "Voici nos offres :",
        "commander": "Super ! 🛒 Pour préparer votre devis, dites-moi :\n\n1️⃣ Quel service ? (bot, site, logo, app...)\n2️⃣ Votre activité\n3️⃣ Votre délai souhaité\n\nJe vous écoute 👇",
        "chatbot": "🤖 Vous voulez un bot intelligent pour votre business ?\n\nOn crée des bots WhatsApp, Telegram et TikTok sur mesure.\n\nQuel canal vous intéresse ?",
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
    """Recherche contextuelle avec compréhension sémantique IA.

    Flux optimisé:
    1. Messages courts (1-2 mots) → keyword matching direct (rapide)
    2. Phrases complètes (3+ mots) → keyword matching d'abord, puis IA si besoin
    3. Si keyword matching trouve une réponse ET que Groq est dispo → vérifier
       qu'elle est sémantiquement correcte (évite le faux "bot" matching)
    4. Si aucun match → IA génère une réponse contextuelle
    """
    history = context_for(chat_id)
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]
    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {user_text}".strip()

    # 1. Keyword matching direct (toujours, rapide)
    direct_answer = trouver_meilleure_reponse_multilingue(user_text, detected_lang)

    # 2. Si le message est une phrase complète et qu'on a l'IA
    if should_use_ai(user_text):
        # Récupérer les KB entries pertinents pour l'IA
        resources = LANG_RESOURCES.get(detected_lang, {"kb": [], "faq": [], "dialogues": []})
        all_kb = resources["kb"]

        # Si le keyword matching a trouvé une réponse, vérifier avec l'IA
        if direct_answer and is_groq_available():
            # Pré-filtrer les entrées KB par keyword matching pour donner du contexte à l'IA
            from local_search import score_match, normalize_text, _get_questions
            scored = []
            for entry in all_kb:
                questions = _get_questions(entry)
                best = max((score_match(user_text, q) for q in questions), default=0.0)
                if best > 0:
                    scored.append((best, entry))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_entries = [e for _, e in scored[:8]]

            if top_entries:
                ai_answer = find_best_kb_answer(user_text, top_entries)
                if ai_answer:
                    logger.info("IA: réponse KB sémantique trouvée (remplace keyword match)")
                    return ai_answer

        # 3. Si aucun match keyword, essayer l'IA avec toute la KB pré-filtrée
        if not direct_answer and is_groq_available():
            from local_search import score_match, _get_questions
            scored = []
            for entry in all_kb:
                questions = _get_questions(entry)
                best = max((score_match(user_text, q) for q in questions), default=0.0)
                if best > 0:
                    scored.append((best, entry))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_entries = [e for _, e in scored[:10]]

            # D'abord chercher une réponse KB exacte avec l'IA
            if top_entries:
                ai_answer = find_best_kb_answer(user_text, top_entries)
                if ai_answer:
                    logger.info("IA: réponse KB trouvée pour phrase sans keyword match")
                    return ai_answer

            # 4. Sinon, générer une réponse contextuelle
            ai_response = generate_contextual_response(
                user_text, top_entries or all_kb[:10], history
            )
            if ai_response:
                logger.info("IA: réponse contextuelle générée")
                return ai_response

    # 5. Fallback: retourner le keyword match si on en a un
    if direct_answer:
        return direct_answer

    # 6. Recherche contextuelle avec historique
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


# ---------------------------------------------------------------------------
# Bot Telegram
# ---------------------------------------------------------------------------

bot = telebot.TeleBot(TOKEN)

# Variable globale pour le shutdown propre
_shutdown_requested = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Signal %s reçu, arrêt en cours...", signum)
    HEARTBEAT_STOP.set()
    try:
        bot.stop_polling()
    except Exception:
        pass


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


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

    # Chercher le message de bienvenue dans la KB
    welcome = trouver_meilleure_reponse_multilingue("bonjour", "fr") or f"Bienvenue chez {BRAND} 🇬🇳"
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
        # FIX BUG #3: Boutons de menu — inclure Commander et Chatbot IA
        portfolio_labels = {"📂 Portfolio", "📂 المعرض", "📂 Portafolio"}
        pricing_labels = {"💎 Voir les Tarifs", "💎 View Pricing", "💎 الأسعار", "💎 Ver Precios"}
        human_labels = {"👑 Parler à un humain", "👑 Talk to a human", "👑 التحدث مع مستشار", "👑 Hablar con un humano"}
        commander_labels = {"🚀 Commander", "🚀 Order", "🚀 طلب", "🚀 Ordenar"}
        chatbot_labels = {"🤖 Chatbot IA", "🤖 AI Chatbot", "🤖 مساعد ذكي", "🤖 Chatbot IA"}

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

        # FIX BUG #3: Handler pour le bouton Commander
        if text in commander_labels:
            response = msg(lang, "commander")
            remember(chat_id, "assistant", response)
            bot.send_message(chat_id, response, reply_markup=menu_for_lang(lang))
            return

        # FIX: Handler pour le bouton Chatbot IA
        if text in chatbot_labels:
            response = msg(lang, "chatbot")
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
# Heartbeat (monitoring optionnel)
# ---------------------------------------------------------------------------

def send_heartbeat() -> None:
    if not MONITOR_API_URL:
        return
    try:
        headers = {"Content-Type": "application/json"}
        if MONITOR_API_KEY:
            headers["X-API-Key"] = MONITOR_API_KEY
        data = json.dumps({"worker": "telegram", "timestamp": time.time()}).encode()
        req = urllib.request.Request(
            f"{MONITOR_API_URL}/internal/heartbeat",
            data=data,
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        logger.debug("Échec d'envoi du heartbeat.", exc_info=True)


def heartbeat_loop() -> None:
    if not MONITOR_API_URL:
        logger.info("Monitoring désactivé : MONITOR_API_URL non configuré.")
        return
    logger.info("Monitoring activé : heartbeat toutes les %ss.", HEARTBEAT_INTERVAL)
    while not HEARTBEAT_STOP.is_set() and not _shutdown_requested:
        send_heartbeat()
        HEARTBEAT_STOP.wait(HEARTBEAT_INTERVAL)


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
