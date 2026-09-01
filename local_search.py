"""Fichier de recherche locale pour le bot Telegram Komara."""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text  # Assurez-vous que ce module est accessible

def score_match(user_message: str, keyword: str) -> float:
    """
    Calcule le score de correspondance basé sur l'intersection des mots.
    Utilise le max des deux ratios pour éviter les faux négatifs quand
    l'utilisateur fait une phrase courte avec un mot-clé important.
    Normalise les deux côtés (accents retirés) pour un matching insensible
    aux accents.
    """
    if not user_message or not keyword:
        return 0.0
    
    # Normaliser les deux côtés (retire les accents)
    norm_msg = normalize_text(user_message)
    norm_kw = normalize_text(keyword)
    
    # Extraction des mots (alphanumériques)
    words_msg = set(re.findall(r'\w+', norm_msg))
    words_kw = set(re.findall(r'\w+', norm_kw))
    
    if not words_kw or not words_msg:
        return 0.0
        
    intersection = words_msg.intersection(words_kw)
    
    if not intersection:
        return 0.0
    
    # Ratio 1: proportion des mots-clés trouvés dans le message
    ratio_kw = len(intersection) / len(words_kw)
    # Ratio 2: proportion du message couverte par les mots-clés
    ratio_msg = len(intersection) / len(words_msg)
    
    # Score = le meilleur des deux ratios
    return max(ratio_kw, ratio_msg)

def _get_searchable_terms(item: dict[str, Any]) -> list[str]:
    """
    Extrait tous les termes de recherche d'un item de la base de connaissances.
    Gère les deux formats:
    - Root kb.json: "questions" (pluriel, liste) + "tags"
    - Lang kb.json: "question" (singulier, string) + "keywords"
    """
    terms: list[str] = []
    
    # Format root kb.json: questions (pluriel, liste de strings)
    questions = item.get("questions", [])
    if isinstance(questions, list):
        terms.extend(questions)
    elif isinstance(questions, str) and questions:
        terms.append(questions)
    
    # Format lang kb.json: question (singulier, string)
    question = item.get("question", "")
    if isinstance(question, str) and question:
        terms.append(question)
    
    # Keywords (lang kb.json)
    keywords = item.get("keywords", [])
    if isinstance(keywords, list):
        terms.extend(keywords)
    elif isinstance(keywords, str) and keywords:
        terms.append(keywords)
    
    # Tags (root kb.json)
    tags = item.get("tags", [])
    if isinstance(tags, list):
        terms.extend(tags)
    
    return terms

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
        terms = _get_searchable_terms(item)
        best_score = 0.0
        for term in terms:
            score = score_match(normalized_message, term)
            if score > best_score:
                best_score = score
        if best_score >= 0.25:
            candidates.append((best_score, item.get("answer", "")))

    # 2. Recherche dans la FAQ locale
    for item in local_faq:
        score = score_match(normalized_message, item["question"])
        if score >= 0.25:
            candidates.append((score, item["answer"]))

    # 3. Recherche dans les Dialogues
    for item in local_dialogues:
        score = score_match(normalized_message, item["question"])
        if score >= 0.25:
            candidates.append((score, item["answer"]))

    # Retourner la réponse avec le score le plus élevé parmi TOUTES les sources
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    return None
