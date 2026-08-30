"""Fichier de recherche locale pour le bot Telegram Komara."""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text  # Assurez-vous que ce module est accessible

def score_match(user_message: str, keyword: str) -> int:
    """Calcule le score de correspondance entre le message de l'utilisateur et un mot-clé."""
    # Protection cruciale : si le mot-clé est vide (ex: erreur de parsing markdown), on retourne 0
    if not user_message or not keyword:
        return 0
        
    pattern = re.escape(keyword.lower())
    return len(re.findall(pattern, user_message.lower()))

def trouver_meilleure_reponse(
    message: str, 
    knowledge_base: List[dict[str, Any]], 
    local_faq: List[dict[str, str]],
    local_dialogues: List[dict[str, str]] = None
) -> str | None:
    """
    Retourne la meilleure réponse possible à partir de la base de connaissances, 
    de la FAQ locale et des dialogues.
    """
    if local_dialogues is None:
        local_dialogues = []
        
    candidates: List[Tuple[int, str]] = []

    # Normaliser le message avant utilisation
    normalized_message = normalize_text(message) if message else ""

    # 1. Recherche dans la base de connaissances (kb.json)
    for item in knowledge_base:
        for question in item.get("questions", []):
            score = score_match(normalized_message, question)
            if score > 0:
                candidates.append((score, item.get("answer", "")))

    # 2. Recherche dans la FAQ locale (docs/faq.md)
    for item in local_faq:
        score = score_match(normalized_message, item["question"])
        if score > 0:
            candidates.append((score, item["answer"]))

    # 3. Recherche dans les Dialogues (docs/dialogues)
    for item in local_dialogues:
        score = score_match(normalized_message, item["question"])
        if score > 0:
            candidates.append((score, item["answer"]))

    # Retourner la réponse avec le score le plus élevé parmi TOUTES les sources confondues
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    return None
    
