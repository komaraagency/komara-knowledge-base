"""Fichier de recherche locale pour le bot Telegram Komara.

Moteur de matching par score basé sur l'intersection de mots (token overlap).
Comprend la normalisation des accents, pluriels et recherche dans 'questions' (liste) ou 'question' (string).
"""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text


def _stem(word: str) -> str:
    """Stemming léger pour le français : retire les pluriels courants."""
    if len(word) <= 3:
        return word
    for suffix in ('aux', 'eaux'):
        if word.endswith(suffix):
            return word[:-len(suffix)] + 'al'
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def _tokenize(text: str) -> set[str]:
    """Extrait et normalise les mots d'un texte."""
    raw = set(re.findall(r'[a-zàâçéèêëîïôùûüÿœ0-9]+', text.lower()))
    return {_stem(w) for w in raw if len(w) >= 2}


def score_match(user_message: str, keyword: str) -> float:
    """
    Calcule le score de correspondance basé sur l'intersection des mots.
    Le score = proportion de mots de la question KB trouvés dans le message utilisateur.
    """
    if not user_message or not keyword:
        return 0.0

    words_msg = _tokenize(user_message)
    words_kw = _tokenize(keyword)

    if not words_kw or not words_msg:
        return 0.0

    intersection = words_msg.intersection(words_kw)
    return len(intersection) / len(words_kw)


def _get_questions(item: dict) -> List[str]:
    """Extrait les questions d'un item KB — gère 'questions' (liste) ou 'question' (string)."""
    if 'questions' in item:
        qs = item['questions']
        if isinstance(qs, list):
            return [str(q) for q in qs if q]
        return [str(qs)]
    if 'question' in item:
        return [str(item['question'])]
    return []


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
        questions = _get_questions(item)
        best_score = max(
            (score_match(normalized_message, q) for q in questions),
            default=0.0
        )
        if best_score >= 0.25:
            candidates.append((best_score, item.get("answer", "")))

    # 2. Recherche dans la FAQ locale (docs/faq.md)
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
