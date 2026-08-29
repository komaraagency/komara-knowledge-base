"""Recherche locale tolérante, sans appel à une IA ou à un service externe."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Les groupes restent volontairement ciblés sur le vocabulaire commercial du bot.
# Ils servent à rapprocher des formulations, pas à inventer de nouvelles réponses.
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "greeting": ("bonjour", "salut", "coucou", "hello", "hi", "bonsoir", "salam", "yo"),
    "site_web": ("site", "sit", "site web", "site internet", "website", "page web", "landing page", "presence en ligne"),
    "application": ("application", "aplikasion", "aplication", "app", "appli", "logiciel", "outil numerique"),
    "bot": ("bot", "chatbot", "assistant", "agent conversationnel"),
    "automatisation": ("automatisation", "automatiser", "automtise", "automtiser", "workflow", "tache repetitive", "taches repetitif", "processus automatique"),
    "tarif": ("prix", "tarif", "tarifs", "cout", "cout dun", "budget", "combien", "offre", "devis"),
    "paiement": ("paiement", "payer", "pe paye", "plusieur foi", "regler", "versement", "acompte"),
    "commande": ("commander", "commande", "demarrer", "commencer", "demender", "voudrai demender", "lancer un projet"),
    "rendez_vous": ("rendez vous", "rdv", "reservation", "reserver", "reservation", "prise de rendez vous"),
    "design": ("design", "visuel", "visuels", "graphisme", "creation graphique"),
    "logo": ("logo", "identite visuelle", "branding", "marque"),
    "reseaux_sociaux": ("reseaux sociaux", "instagram", "facebook", "tiktok", "publication"),
    "support": ("support", "assistance", "aide", "reclamation", "service client"),
    "whatsapp": ("whatsapp", "watsapp", "whatssap"),
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
    value_tokens = tokens(text)
    result = set(value_tokens)
    for group, aliases in SYNONYM_GROUPS.items():
        alias_tokens = [tokens(alias) for alias in aliases]
        if any(normalize_text(alias) in text for alias in aliases):
            result.add(f"__{group}__")
            continue
        # Tolérance limitée aux fautes : elle ne s’applique qu’aux mots d’au
        # moins quatre caractères et exige une similarité élevée.
        for alias_set in alias_tokens:
            if len(alias_set) != 1:
                continue
            alias_word = next(iter(alias_set))
            if any(
                len(word) >= 4 and SequenceMatcher(None, word, alias_word).ratio() >= 0.78
                for word in value_tokens
            ):
                result.add(f"__{group}__")
                break
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
        # Un mot générique présent dans une phrase ne doit pas battre une
        # intention plus longue et plus informative (ex. « devis » vs
        # « demander un devis »).
        if candidate_normalized in query_normalized and len(candidate_normalized.split()) == 1:
            return 250 + len(candidate_normalized)
        return 2000 + min(len(query_normalized), len(candidate_normalized))

    query_concepts = concepts(query_normalized)
    candidate_concepts = concepts(candidate_normalized)
    overlap = query_concepts & candidate_concepts
    # Rapproche aussi deux mots isolés légèrement mal orthographiés, sans
    # appliquer de correction automatique aux phrases entières.
    query_words = {word for word in query_concepts if not word.startswith('__')}
    candidate_words = {word for word in candidate_concepts if not word.startswith('__')}
    fuzzy_overlap = sum(
        1 for word in query_words
        if len(word) >= 4 and any(SequenceMatcher(None, word, other).ratio() >= 0.80 for other in candidate_words)
    )
    if fuzzy_overlap:
        overlap = set(overlap)
        overlap.update(f"__fuzzy_{index}__" for index in range(fuzzy_overlap))
    if not overlap:
        return 0

    # La couverture de la question est privilégiée; un seul mot générique ne suffit
    # pas à battre une question plus spécifique dans la base.
    coverage = len(overlap) / max(1, len(query_concepts))
    score = len(overlap) * 100 + int(coverage * 100)
    if len(overlap) == 1 and len(query_concepts) > 2:
        score -= 80
    return max(0, score)
