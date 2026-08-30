"""Worker Telegram Komara avec polling robuste pour Railway."""

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
# Chargement de la base de connaissances, FAQ et Dialogues
# ---------------------------------------------------------------------------

KB_PATH = BASE_DIR / "kb.json"
FAQ_PATH = BASE_DIR / "docs" / "faq.md"
DIALOGUES_DIR = BASE_DIR / "docs" / "dialogues"

def load_knowledge_base() -> dict[str, Any]:
    """Charge la base de connaissances depuis un fichier JSON."""
    if not KB_PATH.is_file():
        logger.error("Le fichier kb.json est absent : %s", KB_PATH)
        raise RuntimeError(f"Le fichier de base de connaissances {KB_PATH} est absent.")

    with KB_PATH.open("r", encoding="utf-8") as kb_file:
        data = json.load(kb_file)
        logger.info("Base de connaissances (kb.json) chargée avec succès.")
        return data

def load_local_faq() -> list[dict[str, str]]:
    """Charge les questions/réponses Markdown depuis docs/faq.md."""
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
    """Charge les questions/réponses depuis le dossier docs/dialogues."""
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

# Initialisation des données au démarrage
try:
    BRAIN = load_knowledge_base()
    LOCAL_FAQ = load_local_faq()
    LOCAL_DIALOGUES = load_dialogues()
except RuntimeError as e:
    logger.critical(e)
    sys.exit(1)

# Variables globales
WHATSAPP = BRAIN.get("contact", {}).get("whatsapp", "notre WhatsApp")
BRAND = BRAIN.get("brand", "Komara Agency")
KNOWLEDGE = BRAIN.get("knowledge", [])
PACKS = BRAIN.get("packs", [])
CONVERSATIONS = BRAIN.get("conversations", [])

MEMORY_FILE = Path(os.getenv("MEMORY_FILE", str(BASE_DIR / "data" / "memory.json")))
MEMORY_LIMIT = max(2, int(os.getenv("MEMORY_LIMIT", "12")))
MEMORY_LOCK = threading.Lock()

if not KNOWLEDGE:
    raise RuntimeError(f"La base de connaissances {KB_PATH} ne contient aucun élément.")

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
# Réponses et interface Telegram
# ---------------------------------------------------------------------------

def menu() -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add("💎 Voir les Tarifs", "📂 Portfolio")
    keyboard.add("🚀 Commander", " Chatbot IA")
    keyboard.add(" Parler à un humain")
    return keyboard

def chercher(message: str | None) -> str | None:
    """Retourne la réponse locale la plus spécifique (KB + FAQ + Dialogues)."""
    return trouver_meilleure_reponse(message, KNOWLEDGE, LOCAL_FAQ, LOCAL_DIALOGUES)

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

def local_contextual_response(chat_id: int, user_text: str) -> str | None:
    text = user_text.casefold().strip()
    history = context_for(chat_id)
    previous_user_messages = [
        item["content"] for item in history
        if item.get("role") == "user" and item.get("content")
    ]

    recent_context = " ".join(previous_user_messages[-3:])
    combined_text = f"{recent_context} {text}".strip()

    direct_answer = chercher(user_text)
    contextual_answer = chercher(combined_text)

    if direct_answer:
        return direct_answer
    if contextual_answer:
        return contextual_answer

    return None

def safe_typing(chat_id: int) -> None:
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        logger.debug("Impossible d'envoyer l'indicateur typing.", exc_info=True)

def send_portfolio(chat_id: int) -> None:
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

    prefix = "" if sent else "⚠️ Les photos sont temporairement indisponibles. "
    text = (
        f"{prefix}Portfolio KOMARA 💎 {BRAIN.get('slogan', '')}\n"
        "Tu veux des exemples pour quel domaine ?"
    )
    bot.send_message(chat_id, text, reply_markup=menu())

@bot.message_handler(commands=["start"])
def start(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    if (message.text or "").strip().casefold() == "/start":
        forget(chat_id)
    safe_typing(chat_id)
    time.sleep(1)
    welcome = KNOWLEDGE[0].get("answer", f"Bienvenue chez {BRAND}.")
    remember(chat_id, "assistant", welcome)
    bot.send_message(chat_id, welcome, reply_markup=menu())

@bot.message_handler(func=lambda message: True)
def handle(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    text = (message.text or "").strip()
    safe_typing(chat_id)

    if text.casefold() in {"/reset", "/forget", "oublie", "oublie-moi"}:
        forget(chat_id)
        response = "D'accord, j'ai effacé le contexte de cette conversation. Que souhaitez-vous faire ?"
        remember(chat_id, "user", text)
        remember(chat_id, "assistant", response)
        bot.send_message(chat_id, response, reply_markup=menu())
        return

    remember(chat_id, "user", text)

    try:
        if text in {"📂 Portfolio", "Portfolio"}:
            send_portfolio(chat_id)
            return

        if text == "💎 Voir les Tarifs":
            base_answer = chercher("prix") or "Voici nos offres :"
            packs_text = "\n".join(
                f"*{pack.get('nom', 'Pack')}*: {pack.get('prix', '')} - "
                f"{pack.get('contenu', '')}"
                for pack in PACKS
            )
            response = f"{base_answer}\n\n{packs_text}" if packs_text else base_answer
            bot.send_message(chat_id, response, reply_markup=menu())
            return

        if text == "👑 Parler à un humain":
            response = f"Expert KOMARA vous contacte sur *{WHATSAPP}* sous 5 minutes."
            remember(chat_id, "assistant", response)
            bot.send_message(chat_id, response, reply_markup=menu())
            return

        local_response = local_contextual_response(chat_id, text)
        if local_response is None:
            record_unrecognized(text, source="telegram")
        response = local_response or (
            "Pour mieux vous orienter, pouvez-vous me préciser votre activité et "
            "ce que vous souhaitez vendre ou automatiser ?"
        )
        remember(chat_id, "assistant", response)
        time.sleep(min(2, len(response) / 200))
        bot.send_message(chat_id, response, reply_markup=menu())

    except ApiTelegramException:
        raise
    except Exception:
        logger.exception("Erreur lors du traitement du message du chat %s", chat_id)
        try:
            bot.send_message(
                chat_id,
                "Désolé, une erreur temporaire est survenue. Un expert KOMARA vous contacte.",
                reply_markup=menu(),
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
        "%s démarrage ; polling_timeout=%ss, long_polling_timeout=%ss, "
        "drop_pending_updates=%s",
        BRAND,
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
