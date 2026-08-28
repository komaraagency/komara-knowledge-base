"""Worker Telegram Komara avec polling robuste pour Railway.

Important : Telegram n'autorise qu'un seul processus en getUpdates par token.
Un conflit 409 est donc traité comme une erreur fatale et non comme une erreur
à relancer immédiatement en boucle.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import telebot
from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:  # Le mode local reste disponible sans LLM.
    OpenAI = None
from telebot.apihelper import ApiTelegramException
from telebot.types import ReplyKeyboardMarkup


# ---------------------------------------------------------------------------
# Configuration générale
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip()
POLL_TIMEOUT = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30"))
LONG_POLLING_TIMEOUT = int(os.getenv("TELEGRAM_LONG_POLLING_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("TELEGRAM_MAX_RETRIES", "8"))
DROP_PENDING_UPDATES = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("komara.telegram")


if not TOKEN:
    raise RuntimeError(
        "La variable d'environnement TELEGRAM_TOKEN est absente. "
        "Ajoutez-la dans Railway avant de démarrer le worker."
    )


# ---------------------------------------------------------------------------
# Chargement de la base de connaissances
# ---------------------------------------------------------------------------

KB_PATH = BASE_DIR / "kb.json"
with KB_PATH.open("r", encoding="utf-8") as kb_file:
    BRAIN: dict[str, Any] = json.load(kb_file)


WHATSAPP = BRAIN.get("contact", {}).get("whatsapp", "notre WhatsApp")
BRAND = BRAIN.get("brand", "Komara Agency")
KNOWLEDGE = BRAIN.get("knowledge", [])
PACKS = BRAIN.get("packs", [])

# La mémoire est stockée dans un fichier JSON. Sur Railway, montez un Volume
# et définissez MEMORY_FILE=/data/komara_memory.json pour la rendre durable.
MEMORY_FILE = Path(os.getenv("MEMORY_FILE", str(BASE_DIR / "data" / "memory.json")))
MEMORY_LIMIT = max(2, int(os.getenv("MEMORY_LIMIT", "12")))
MEMORY_LOCK = threading.Lock()
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
LLM_ENABLED = bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None
LLM_CLIENT = OpenAI() if LLM_ENABLED else None

if not KNOWLEDGE:

    raise RuntimeError(f"La base de connaissances {KB_PATH} ne contient aucun élément.")


bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
_shutdown_requested = False


# ---------------------------------------------------------------------------
# Arrêt propre
# ---------------------------------------------------------------------------


def request_shutdown(signum: int, _frame: Any) -> None:
    """Demande au polling de s'arrêter quand Railway envoie SIGTERM/SIGINT."""

    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Signal %s reçu : arrêt propre demandé.", signum)


signal.signal(signal.SIGTERM, request_shutdown)
signal.signal(signal.SIGINT, request_shutdown)


# ---------------------------------------------------------------------------
# Réponses et interface Telegram
# ---------------------------------------------------------------------------


def menu() -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add("💎 Voir les Tarifs", "📂 Portfolio")
    keyboard.add("🚀 Commander", "🤖 Chatbot IA")
    keyboard.add("👑 Parler à un humain")
    return keyboard


def chercher(message: str | None) -> str | None:
    """Retourne la première réponse dont une question correspond au message."""

    normalized = (message or "").casefold()
    for item in KNOWLEDGE:
        questions = item.get("questions", [])
        if any(str(question).casefold() in normalized for question in questions):
            return item.get("answer")
    return None


def _load_memory() -> dict[str, list[dict[str, str]]]:
    """Charge la mémoire; une mémoire absente ou invalide est réinitialisée."""

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
    """Sauvegarde atomiquement la mémoire pour éviter un fichier JSON partiel."""

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = MEMORY_FILE.with_suffix(".tmp")
    with MEMORY_LOCK:
        with temporary_file.open("w", encoding="utf-8") as memory_file:
            json.dump(memory, memory_file, ensure_ascii=False, indent=2)
        temporary_file.replace(MEMORY_FILE)


def remember(chat_id: int, role: str, content: str) -> list[dict[str, str]]:
    """Ajoute un échange et conserve seulement les derniers messages du chat."""

    memory = _load_memory()
    key = str(chat_id)
    history = deque(memory.get(key, []), maxlen=MEMORY_LIMIT)
    history.append({"role": role, "content": content[:4000]})
    memory[key] = list(history)
    _save_memory(memory)
    return memory[key]


def forget(chat_id: int) -> None:
    """Efface volontairement la mémoire d'un utilisateur."""

    memory = _load_memory()
    memory.pop(str(chat_id), None)
    _save_memory(memory)


def context_for(chat_id: int) -> list[dict[str, str]]:
    return _load_memory().get(str(chat_id), [])[-MEMORY_LIMIT:]


def build_context(chat_id: int, user_text: str) -> list[dict[str, str]]:
    """Construit le contexte LLM avec les faits métier et l'historique du chat."""

    knowledge = "\n".join(
        f"- {item.get('id', 'info')}: {item.get('answer', '')}"
        for item in KNOWLEDGE
    )
    services = "\n".join(
        f"- {service.get('nom', '')}: {service.get('prix', '')} — {service.get('description', '')}"
        for service in BRAIN.get("services", [])
    )
    system = (
        f"Tu es l'assistant commercial de {BRAND}. Réponds en français, de façon naturelle, "
        "chaleureuse et concise. Comprends les pronoms et les références au message précédent. "
        "N'invente jamais de prix, de délai ou de service; utilise uniquement les informations "
        "ci-dessous. Si l'information manque, pose une question de clarification. "
        "Ne prétends pas être humain.\n\nSERVICES:\n"
        f"{services}\n\nBASE DE CONNAISSANCES:\n{knowledge}"
    )
    return [{"role": "system", "content": system}, *context_for(chat_id), {"role": "user", "content": user_text}]


def generate_contextual_response(chat_id: int, user_text: str) -> str | None:
    """Utilise le LLM si configuré; sinon renvoie None pour activer le repli local."""

    if not LLM_CLIENT:
        return None
    try:
        result = LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=build_context(chat_id, user_text),
            max_completion_tokens=500,
        )
        answer = (result.choices[0].message.content or "").strip()
        return answer or None
    except Exception:
        logger.exception("Échec du LLM; utilisation du repli local.")
        return None


def safe_typing(chat_id: int) -> None:
    """L'indicateur de saisie est facultatif : son échec ne doit pas tuer le bot."""

    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        logger.debug("Impossible d'envoyer l'indicateur typing.", exc_info=True)


def send_portfolio(chat_id: int) -> None:
    """Envoie les deux fichiers portfolio s'ils existent, puis le texte associé."""

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
    """Traite un message sans laisser une erreur utilisateur arrêter le worker."""

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

        # Le LLM comprend le contexte; la base locale garantit un fonctionnement
        # même si OPENAI_API_KEY n'est pas configurée ou si l'API est indisponible.
        response = generate_contextual_response(chat_id, text) or chercher(text) or (
            "Pour mieux vous orienter, pouvez-vous me préciser votre activité et "
            "ce que vous souhaitez vendre ou automatiser ?"
        )
        remember(chat_id, "assistant", response)
        time.sleep(min(2, len(response) / 200))
        bot.send_message(chat_id, response, reply_markup=menu())

    except ApiTelegramException:
        # Cette exception est relancée pour permettre au niveau supérieur de
        # distinguer un conflit 409 d'une erreur applicative de message.
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
    """Supprime un éventuel webhook avant de passer en long polling."""

    logger.info("Suppression du webhook Telegram avant le polling.")
    bot.delete_webhook(drop_pending_updates=DROP_PENDING_UPDATES)


def is_conflict(error: ApiTelegramException) -> bool:
    """Détecte le conflit Telegram 409, quelle que soit sa formulation."""

    description = str(getattr(error, "description", error)).casefold()
    return getattr(error, "error_code", None) == 409 or "terminated by other" in description


def run() -> None:
    """Démarre le worker et ne relance pas agressivement un conflit 409."""

    global _shutdown_requested
    retry_count = 0

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
