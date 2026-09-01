from __future__ import annotations

"""Fichier de recherche locale pour le bot Telegram Komara.
Version 2.0 — Matching par intention + fuzzy + tolérance fautes.
Le bot comprend l'intention du client, pas juste les mots-clés exacts.
"""

from typing import Any, List, Tuple
import re
from normalize_text import normalize_text, remove_accents, normalize, detect_intent, fuzzy_match, score_match as fuzzy_score


def _get_searchable_terms(item: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Extrait questions, tags/keywords et catégorie d'un item KB."""
    questions: list[str] = []
    tags: list[str] = []

    qs = item.get("questions", [])
    if isinstance(qs, list):
        questions.extend(qs)
    elif isinstance(qs, str) and qs:
        questions.append(qs)

    q = item.get("question", "")
    if isinstance(q, str) and q:
        questions.append(q)

    kws = item.get("keywords", [])
    if isinstance(kws, list):
        tags.extend(kws)
    elif isinstance(kws, str) and kws:
        tags.append(kws)

    ts = item.get("tags", [])
    if isinstance(ts, list):
        tags.extend(ts)

    cat = item.get("category", "")
    return questions, tags, [cat] if cat else []


def _score_entry(message_norm: str, message_raw: str, questions: list, tags: list, categories: list) -> float:
    """Score une entrée de la KB contre le message.
    Combine: matching exact + fuzzy + intention + catégorie.
    """
    if not message_norm:
        return 0.0

    msg_words = set(message_norm.split())
    score = 0.0

    # 1. Matching sur tags (poids fort)
    for tag in tags:
        tag_norm = remove_accents(tag.lower())
        tag_words = tag_norm.split()
        for tw in tag_words:
            if tw in msg_words:
                score += 2.0
            else:
                # Fuzzy: tolérer les fautes
                for mw in msg_words:
                    if len(mw) >= 3 and len(tw) >= 3 and fuzzy_match(tw, mw, 0.75):
                        score += 1.0
                        break
        # Tag complet dans le message
        if tag_norm in message_norm:
            score += 3.0

    # 2. Matching sur questions
    for q in questions:
        q_norm = remove_accents(q.lower())
        q_words = set(q_norm.split())
        overlap = msg_words & q_words
        if overlap:
            score += len(overlap) * 1.5
        # Fuzzy sur mots de la question
        for qw in q_words:
            if qw not in msg_words and len(qw) >= 4:
                for mw in msg_words:
                    if len(mw) >= 4 and fuzzy_match(qw, mw, 0.75):
                        score += 0.5
                        break

    # 3. Bonus si la catégorie correspond à l'intention détectée
    intent = detect_intent(message_raw)
    if intent:
        for cat in categories:
            if intent in cat or cat in intent:
                score += 2.0

    return score


def trouver_meilleure_reponse(
    message: str,
    knowledge_base: List[dict[str, Any]],
    local_faq: List[dict[str, str]],
    local_dialogues: List[dict[str, str]] = None
) -> str | None:
    """Retourne la meilleure réponse en comprenant l'intention du client.
    Utilise le fuzzy matching pour tolérer les fautes et mots mal formés.
    """
    if local_dialogues is None:
        local_dialogues = []

    if not message or not message.strip():
        return None

    # Normaliser le message (corrige les fautes, supprime les accents)
    message_norm = normalize(message)
    candidates: List[Tuple[float, str]] = []

    # 1. Base de connaissances (kb.json)
    for item in knowledge_base:
        questions, tags, cats = _get_searchable_terms(item)
        score = _score_entry(message_norm, message, questions, tags, cats)
        if score >= 1.5:  # Seuil minimum
            candidates.append((score, item.get("answer", "")))

    # 2. FAQ locale
    for item in local_faq:
        q = item.get("question", "")
        score = _score_entry(message_norm, message, [q], [], [])
        if score >= 1.5:
            candidates.append((score, item.get("answer", "")))

    # 3. Dialogues
    for item in local_dialogues:
        q = item.get("question", "")
        score = _score_entry(message_norm, message, [q], [], [])
        if score >= 1.5:
            candidates.append((score, item.get("answer", "")))

    if candidates:
        # Trier par score décroissant et retourner la meilleure
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return None
