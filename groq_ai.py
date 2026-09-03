"""Intégration Groq AI pour la compréhension sémantique des messages.

Quand le message utilisateur est une phrase complète (pas juste un mot-clé),
on utilise Groq pour comprendre l'intention et soit:
1. Trouver la meilleure réponse dans la KB
2. Générer une réponse contextuelle basée sur la KB
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("komara.groq")

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "15"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "300"))

# Brand context pour que l'IA réponde dans le ton Komara
BRAND_CONTEXT = """Tu es l'assistant IA de Komara Agency 🇬🇳, une agence digitale basée en Guinée qui crée des bots WhatsApp/Telegram, des sites web, des applications, des logos et du contenu IA.

RÈGLES:
1. Réponds TOUJOURS dans la langue du message utilisateur (FR, EN, AR, ES).
2. Sois bref, naturel et professionnel. Pas de robotisme.
3. Si la question correspond à une entrée de la base de connaissances, utilise-la comme réponse principale.
4. Si aucune entrée ne correspond, génère une réponse naturelle qui guide vers nos services.
5. Ne JAMAIS inventer des prix. Si on demande un prix et que tu n'as pas l'info, dis de contacter l'équipe.
6. Remplace toujours "Ndine Komara" par "Komara Agency 🇬🇳" dans tes réponses.
7. Maximum 4-5 lignes par réponse. Sois concis."""


def is_groq_available() -> bool:
    """Vérifie si Groq est configuré."""
    return bool(GROQ_API_KEY)


def _call_groq(messages: list[dict], temperature: float = 0.3) -> str | None:
    """Appel API Groq via urllib (aucune dépendance externe)."""
    if not GROQ_API_KEY:
        return None

    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": GROQ_MAX_TOKENS,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    req = urllib.request.Request(GROQ_URL, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except urllib.error.HTTPError as e:
        logger.warning("Erreur HTTP Groq %s: %s", e.code, e.read()[:200])
        return None
    except Exception as e:
        logger.warning("Erreur Groq: %s", e)
        return None


def find_best_kb_answer(
    user_message: str,
    kb_entries: list[dict[str, Any]],
    top_k: int = 8,
) -> str | None:
    """Utilise Groq pour trouver la meilleure réponse KB en comprenant le sens.

    Envoie les top_k entrées KB les plus pertinentes (pré-filtrées par keywords)
    et demande à Groq de choisir la meilleure ou de dire "AUCUNE".
    """
    if not GROQ_API_KEY or not kb_entries:
        return None

    # Construire le contexte avec les entrées KB
    kb_context = []
    for i, entry in enumerate(kb_entries[:top_k]):
        questions = entry.get("questions", [])
        if isinstance(questions, list):
            q_text = " | ".join(questions[:3])
        else:
            q_text = str(questions)
        answer = entry.get("answer", "")[:200]
        kb_context.append(f"[{i}] Q: {q_text}\n    R: {answer}")

    kb_text = "\n".join(kb_context)

    messages = [
        {
            "role": "system",
            "content": f"""{BRAND_CONTEXT}

Tu reçois un message utilisateur et une liste de réponses possibles de la base de connaissances.
Analyse le SENS du message, pas juste les mots.

Si une entrée correspond au sens de la question, réponds avec son numéro entre crochets: [numéro]
Si AUCUNE entrée ne correspond vraiment, réponds exactement: AUCUNE

Base de connaissances:
{kb_text}""",
        },
        {
            "role": "user",
            "content": f"Message utilisateur: \"{user_message}\"\n\nQuelle est la meilleure réponse ? Réponds avec [numéro] ou AUCUNE.",
        },
    ]

    result = _call_groq(messages, temperature=0.0)
    if not result:
        return None

    # Extraire le numéro entre crochets
    import re
    match = re.search(r'\[(\d+)\]', result)
    if match:
        idx = int(match.group(1))
        if 0 <= idx < len(kb_entries):
            return kb_entries[idx].get("answer", "")

    return None


def generate_contextual_response(
    user_message: str,
    kb_context: list[dict[str, Any]],
    conversation_history: list[dict[str, str]] | None = None,
) -> str | None:
    """Génère une réponse contextuelle en utilisant la KB comme contexte.

    Utilisé quand aucune entrée KB ne correspond directement mais qu'on
    peut quand même répondre avec le contexte de l'agence.
    """
    if not GROQ_API_KEY:
        return None

    # Construire le contexte KB pertinent
    kb_summaries = []
    for entry in kb_context[:10]:
        questions = entry.get("questions", [])
        if isinstance(questions, list):
            q_text = questions[0] if questions else ""
        else:
            q_text = str(questions)
        answer = entry.get("answer", "")[:150]
        kb_summaries.append(f"- {q_text}: {answer}")

    kb_text = "\n".join(kb_summaries) if kb_summaries else "Aucune info spécifique."

    # Historique de conversation
    history_text = ""
    if conversation_history:
        for msg in conversation_history[-4:]:
            role = "Client" if msg.get("role") == "user" else "Assistant"
            history_text += f"{role}: {msg.get('content', '')[:100]}\n"

    messages = [
        {
            "role": "system",
            "content": f"""{BRAND_CONTEXT}

Contexte de la base de connaissances de Komara Agency:
{kb_text}

Historique récent:
{history_text if history_text else "Nouvelle conversation."}

Réponds au message du client de façon naturelle et utile.""",
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    return _call_groq(messages, temperature=0.4)


def should_use_ai(user_message: str) -> bool:
    """Détermine si on doit utiliser l'IA (message complet) ou le keyword matching (court).

    - Messages courts (1-2 mots): keyword matching suffit
    - Phrases complètes (3+ mots avec verbes/sujet): IA pour comprendre le sens
    """
    if not GROQ_API_KEY:
        return False

    words = user_message.strip().split()
    word_count = len(words)

    # 1-2 mots = keyword matching direct
    if word_count <= 2:
        return False

    # 3+ mots = probablement une phrase, utiliser l'IA
    # Mais les salutations courtes restent en keyword
    greetings = {"bonjour", "salut", "salam", "hello", "hi", "bonsoir", "coucou",
                 "merci", "ok", "d'accord", "thanks", "choukran"}
    if word_count <= 4 and any(w.lower() in greetings for w in words):
        return False

    return True
