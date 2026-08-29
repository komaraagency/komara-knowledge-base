"""Recherche locale tolérante, sans appel à une IA ou à un service externe."""
from __future__ import annotations

import re
import unicodedata

# Les groupes restent volontairement ciblés sur le vocabulaire commercial du bot.
# Ils servent à rapprocher des formulations, pas à inventer de nouvelles réponses.
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "greeting": ("bonjour", "salut", "coucou", "hello", "hi", "bonsoir", "salam", "yo"),
    "site_web": ("site", "site web", "site internet", "website", "page web", "landing page", "presence en ligne"),
    "application": ("application", "app", "logiciel", "outil numerique"),
    "bot": ("bot", "chatbot", "assistant", "agent conversationnel"),
    "automatisation": ("automatisation", "automatiser", "workflow", "tache repetitive", "processus automatique"),
    "tarif": ("prix", "tarif", "tarifs", "cout", "budget", "combien", "offre", "devis"),
    "paiement": ("paiement", "payer", "regler", "versement", "acompte"),
    "commande": ("commander", "commande", "demarrer", "commencer", "lancer un projet"),
    "rendez_vous": ("rendez vous", "rdv", "reservation", "prise de rendez vous"),
    "design": ("design", "visuel", "visuels", "graphisme", "creation graphique"),
    "logo": ("logo", "identite visuelle", "branding", "marque"),
    "reseaux_sociaux": ("reseaux sociaux", "instagram", "facebook", "tiktok", "publication"),
    "support": ("support", "assistance", "aide", "reclamation", "service client"),
    "humain": ("humain", "conseiller", "expert", "personne", "contact"),
}

STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "cette", "comment", "dans", "de", "des", "du", "en", "est",
    "et", "faire", "faites", "je", "la", "le", "les", "me", "mon", "nous", "pour", "pouvez", "que",
    "quel", "quelle", "quels", "quelles", "qui", "se", "sur", "te", "un", "une", "vous", "vos", "votre",
    "y", "tu", "as", "on", "ou", "d", "l",
}


def normalize_text(value: str | None) -> str:
    """Normalise casse, accents, apostrophes, traits d’union et espaces."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value: str | None) -> set[str]:
    return {word for word in normalize_text(value).split() if word and word not in STOPWORDS}


def concepts(value: str | None) -> set[str]:
    text = normalize_text(value)
    result = tokens(text)
    for group, aliases in SYNONYM_GROUPS.items():
        if any(alias in text for alias in aliases):
            result.add(f"__{group}__")
    return result


def score_match(query: str | None, candidate: str | None) -> int:
    """Retourne un score déterministe; zéro signifie qu’aucun rapprochement n’est trouvé."""
    query_normalized = normalize_text(query)
    candidate_normalized = normalize_text(candidate)
    if not query_normalized or not candidate_normalized:
        return 0
    if query_normalized == candidate_normalized:
        return 3000 + len(candidate_normalized)
    if query_normalized in candidate_normalized or candidate_normalized in query_normalized:
        return 2000 + min(len(query_normalized), len(candidate_normalized))

    query_concepts = concepts(query_normalized)
    candidate_concepts = concepts(candidate_normalized)
    overlap = query_concepts & candidate_concepts
    if not overlap:
        return 0

    # La couverture de la question est privilégiée; un seul mot générique ne suffit
    # pas à battre une question plus spécifique dans la base.
    coverage = len(overlap) / max(1, len(query_concepts))
    score = len(overlap) * 100 + int(coverage * 100)
    if len(overlap) == 1 and len(query_concepts) > 2:
        score -= 80
    return max(0, score)
