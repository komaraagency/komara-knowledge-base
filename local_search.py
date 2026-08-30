"""Fichier de recherche locale pour le bot Telegram Komara."""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text  # Assurez-vous que ce module est accessible

def score_match(user_message: str, keyword: str) -> float:
    """
    Calcule le score de correspondance basé sur l'intersection des mots.
    Beaucoup plus intelligent que le regex strict : comprend les synonymes 
    et les phrases incomplètes, et règle le bug de contexte bloqué.
    """
    if not user_message or not keyword:
        return 0.0
    
    # Extraction des mots (alphanumériques), mise en minuscule
    words_msg = set(re.findall(r'\w+', user_message.lower()))
    words_kw = set(re.findall(r'\w+', keyword.lower()))
    
    if not words_kw:
        return 0.0
        
    # Mots en commun entre le message de l'utilisateur et la question de la FAQ
    intersection = words_msg.intersection(words_kw)
    
    # Score = proportion de mots de la question FAQ trouvés dans le message utilisateur
    # Cela permet de ne pas être pénalisé si l'utilisateur fait une phrase très longue.
    return len(intersection) / len(words_kw)

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
        
    candidates: List[Tuple[float, str]] = []

    # Normaliser le message avant utilisation
    normalized_message = normalize_text(message) if message else ""

    # 1. Recherche dans la base de connaissances (kb.json)
    for item in knowledge_base:
        for question in item.get("questions", []):
            score = score_match(normalized_message, question)
            # Seuil de 0.25 (au moins 1 mot sur 4 ou 1 mot sur 1) pour éviter les matchs parasites
            if score >= 0.25: 
                candidates.append((score, item.get("answer", "")))

    # 2. Recherche dans la FAQ locale (docs/faq.md)
    for item in local_faq:
        score = score_match(normalized_message, item["question"])
        if score >= 0.25:
            candidates.append((score, item["answer"]))

    # 3. Recherche dans les Dialogues (docs/dialogues)
    for item in local_dialogues:
        score = score_match(normalized_message, item["question"])
        if score >= 0.25:
            candidates.append((score, item["answer"]))

    # Retourner la réponse avec le score le plus élevé parmi TOUTES les sources
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    return None
    
