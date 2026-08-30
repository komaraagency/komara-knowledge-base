"""Fichier de recherche locale pour le bot Telegram Komara."""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text  # Assurez-vous que ce module est accessible

def score_match(user_message: str, keyword: str) -> int:
    """Calcule le score de correspondance entre le message de l'utilisateur et un mot-clé."""
    pattern = re.escape(keyword.lower())
    return len(re.findall(pattern, user_message.lower()))

def trouver_meilleure_reponse(message: str, knowledge_base: List[dict[str, Any]], local_faq: List[dict[str, str]]) -> str | None:
    """Retourne la meilleure réponse possible à partir de la base de connaissances et de la FAQ locale."""
    candidates: List[Tuple[int, str]] = []

    # Normaliser le message avant utilisation
    normalized_message = normalize_text(message)

    # Recherche dans la base de connaissances
    for item in knowledge_base:
        for question in item.get("questions", []):
            score = score_match(normalized_message, question)
            if score > 0:
                candidates.append((score, item.get("answer", "")))

    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    # Recherche dans la FAQ locale
    for item in local_faq:
        score = score_match(normalized_message, item["question"])
        if score > 0:
            candidates.append((score, item["answer"]))

    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    return None
                              
