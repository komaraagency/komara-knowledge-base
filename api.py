"""API HTTP de Komara.

Cette API est volontairement séparée du worker Telegram : elle ne crée pas de
TeleBot et n'appelle jamais infinity_polling(). Elle peut donc être déployée
comme service Web sans provoquer de conflit Telegram 409.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest


BASE_DIR = Path(__file__).resolve().parent
KB_PATH = BASE_DIR / "kb.json"

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

if not KNOWLEDGE:
    raise RuntimeError(f"La base de connaissances {KB_PATH} ne contient aucun élément.")


app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


def chercher(message: str | None) -> str | None:
    """Retourne la première réponse correspondant aux questions connues."""

    normalized = (message or "").casefold()
    for item in KNOWLEDGE:
        questions = item.get("questions", [])
        if any(str(question).casefold() in normalized for question in questions):
            return item.get("answer")
    return None


def repondre(message: str) -> str:
    """Construit une réponse HTTP à partir du message reçu."""

    text = message.strip()
    if text.casefold() in {"prix", "tarif", "tarifs", "price"}:
        base_answer = chercher("prix") or "Voici nos offres :"
        packs_text = "\n".join(
            f"*{pack.get('nom', 'Pack')}*: {pack.get('prix', '')} - "
            f"{pack.get('contenu', '')}"
            for pack in PACKS
        )
        return f"{base_answer}\n\n{packs_text}" if packs_text else base_answer

    return chercher(text) or (
        "Parmi nos services : Agent IA, Site Web, Vidéo UGC... "
        "lequel t'intéresse ?"
    )


@app.get("/")
def home():
    return jsonify(
        {
            "status": "Komara Agency API OK",
            "version": KB.get("version"),
            "brand": BRAND,
            "telegram_polling": False,
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


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
        response = repondre(user_message)
        return jsonify({"response": response})
    except Exception:
        logger.exception("Erreur lors du traitement d'une requête /chat.")
        return jsonify({"error": "Une erreur interne est survenue."}), 500


@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Route introuvable."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"error": "Méthode HTTP non autorisée."}), 405


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
