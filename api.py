"""API HTTP locale de Komara Agency.

Ce module est indépendant du Worker Telegram : il ne crée aucun TeleBot et ne
lance jamais de polling. Il lit uniquement les ressources locales du dépôt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest

from local_search import score_match

BASE_DIR = Path(__file__).resolve().parent
KB_PATH = BASE_DIR / "kb.json"
FAQ_PATH = BASE_DIR / "docs" / "faq.md"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("komara.api")

with KB_PATH.open("r", encoding="utf-8") as kb_file:
    KB: dict[str, Any] = json.load(kb_file)

BRAND = KB.get("brand", "Komara Agency")
KNOWLEDGE = KB.get("knowledge", [])
PACKS = KB.get("packs", [])
CONVERSATIONS = KB.get("conversations", [])
API_KEY = os.getenv("KOMARA_API_KEY", "").strip()
HEARTBEAT_TIMEOUT = max(30, int(os.getenv("WORKER_HEARTBEAT_TIMEOUT", "180")))
WORKER_LAST_HEARTBEAT: dict[str, Any] = {}

if not KNOWLEDGE:
    raise RuntimeError(f"La base de connaissances {KB_PATH} ne contient aucun élément.")


def load_local_faq() -> list[dict[str, str]]:
    """Charge les questions/réponses Markdown depuis le dépôt local."""
    if not FAQ_PATH.is_file():
        logger.warning("FAQ locale absente : %s", FAQ_PATH)
        return []
    content = FAQ_PATH.read_text(encoding="utf-8")
    items: list[dict[str, str]] = []
    for section in re.split(r"^###\s+", content, flags=re.MULTILINE)[1:]:
        lines = section.splitlines()
        if not lines:
            continue
        question = lines[0].strip()
        answer = "\n".join(lines[1:]).strip()
        answer = re.sub(r"^\*\*Réponse\s*:\*\*\s*", "", answer, flags=re.IGNORECASE)
        if question and answer:
            items.append({"question": question, "answer": answer})
    logger.info("FAQ locale chargée : %s questions", len(items))
    return items


LOCAL_FAQ = load_local_faq()
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-zàâçéèêëîïôùûüÿœ0-9]+", value.casefold()))


def chercher(message: str | None) -> str | None:
    """Retourne la réponse locale la plus spécifique, avec tolérance linguistique."""
    candidates: list[tuple[int, str]] = []
    for item in KNOWLEDGE:
        best = max(
            (score_match(message, str(question)) for question in item.get("questions", [])),
            default=0,
        )
        if best:
            candidates.append((best, str(item.get("answer", ""))))
    if candidates:
        return max(candidates, key=lambda result: result[0])[1]

    faq_candidates = [
        (score_match(message, item["question"]), item["answer"])
        for item in LOCAL_FAQ
    ]
    faq_candidates = [candidate for candidate in faq_candidates if candidate[0]]
    if faq_candidates:
        return max(faq_candidates, key=lambda result: result[0])[1]

    conversation_candidates = [
        (score_match(message, str(example.get("user", ""))), str(example.get("assistant", "")))
        for example in CONVERSATIONS
    ]
    conversation_candidates = [candidate for candidate in conversation_candidates if candidate[0]]
    if conversation_candidates:
        return max(conversation_candidates, key=lambda result: result[0])[1]
    return None


def repondre(message: str) -> str:
    """Construit une réponse exclusivement à partir des ressources locales."""
    text = message.strip()
    if text.casefold() in {"prix", "tarif", "tarifs", "price"}:
        base_answer = chercher("prix") or "Nos tarifs dépendent du périmètre du projet."
        packs_text = "\n".join(
            f"*{pack.get('nom', 'Pack')}*: {pack.get('prix', '')} - {pack.get('contenu', '')}"
            for pack in PACKS
        )
        return f"{base_answer}\n\n{packs_text}" if packs_text else base_answer
    return chercher(text) or (
        "Je peux vous orienter vers un bot, un site ou une application, "
        "une automatisation, ou une création digitale. Quel est votre besoin ?"
    )


@app.before_request
def protect_chat_endpoint():
    """Active une protection uniquement lorsque KOMARA_API_KEY est configurée."""
    if request.path == "/chat" and API_KEY:
        supplied_key = request.headers.get("X-API-Key", "")
        if supplied_key != API_KEY:
            return jsonify({"error": "Clé API manquante ou invalide."}), 401
    return None


@app.get("/")
def home():
    return jsonify(
        {
            "status": "Komara Agency API OK",
            "version": KB.get("version"),
            "brand": BRAND,
            "telegram_polling": False,
            "local_sources": {"kb": True, "faq": bool(LOCAL_FAQ)},
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "knowledge_entries": len(KNOWLEDGE), "faq_entries": len(LOCAL_FAQ)})


@app.get("/worker-health")
def worker_health():
    """Indique si le Worker a envoyé un heartbeat récemment."""
    if not WORKER_LAST_HEARTBEAT:
        return jsonify({"status": "unknown", "message": "Aucun heartbeat reçu."}), 503
    age = time.time() - float(WORKER_LAST_HEARTBEAT["timestamp"])
    payload = {**WORKER_LAST_HEARTBEAT, "age_seconds": round(age, 1), "timeout_seconds": HEARTBEAT_TIMEOUT}
    return jsonify(payload), (200 if age <= HEARTBEAT_TIMEOUT else 503)


@app.post("/internal/heartbeat")
def worker_heartbeat():
    """Reçoit le signal de vie du Worker; cet endpoint ne contacte pas Telegram."""
    if API_KEY and request.headers.get("X-API-Key", "") != API_KEY:
        return jsonify({"error": "Clé API manquante ou invalide."}), 401
    data = request.get_json(silent=True) or {}
    worker = str(data.get("worker", "telegram"))[:80]
    WORKER_LAST_HEARTBEAT.clear()
    WORKER_LAST_HEARTBEAT.update({
        "status": "healthy",
        "worker": worker,
        "timestamp": time.time(),
    })
    return jsonify({"status": "recorded"})


@app.post("/chat")
def chat():
    try:
        data = request.get_json(silent=True)
    except BadRequest:
        return jsonify({"error": "Le corps de la requête doit être un JSON valide."}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Le corps de la requête doit être un objet JSON."}), 400
    user_message = data.get("message")
    if not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "Le champ 'message' doit être une chaîne non vide."}), 400
    try:
        return jsonify({"response": repondre(user_message)})
    except Exception:
        logger.exception("Erreur lors du traitement d'une requête /chat.")
        return jsonify({"error": "Une erreur interne est survenue."}), 500


@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Route introuvable."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"error": "Méthode HTTP non autorisée."}), 405


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Requête trop volumineuse."}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
