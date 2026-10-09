"""sector_reply — le client dit son secteur : on l'utilise, on ne le redemande pas.

Capture Boss 09/10 :
    Bot    : « Dis-moi ton activité et je te prépare une proposition. »
    Client : « J'ai une boutique »
    Bot    : « Tu vends quoi exactement ? »   <- redondant, il vient de le dire

Règle : si le client DÉCLARE son activité (« j'ai une boutique », « je suis
dans l'immobilier », « mon restaurant »), la réponse NOMME ce secteur, donne
l'angle concret pour ce secteur et propose UNE action (devis ou RDV).
100% local, aucune API, aucune invention : seuls les secteurs du dictionnaire
ci-dessous sont reconnus, sinon on retourne None (le moteur continue).
"""
from __future__ import annotations

import re

# secteur canonique -> (mots déclencheurs, angle concret pour ce secteur)
SECTORS: dict[str, tuple[tuple[str, ...], str]] = {
    "boutique": (("boutique", "magasin", "commerce", "vetement", "vêtement", "mode",
                  "friperie", "e-commerce", "ecommerce", "vends en ligne"),
                 "un bot qui présente tes articles, prend les commandes et "
                 "relance les clients qui hésitent"),
    "restaurant": (("restaurant", "resto", "traiteur", "fast food", "fast-food",
                    "maquis", "cafe", "café", "boulangerie", "patisserie", "pâtisserie"),
                   "un bot qui affiche ton menu, prend les réservations et les "
                   "commandes à emporter"),
    "salon": (("salon", "coiffure", "coiffeur", "coiffeuse", "beaute", "beauté",
               "esthetique", "esthétique", "barber", "onglerie"),
              "un bot qui prend les rendez-vous tout seul et envoie les rappels "
              "pour éviter les absences"),
    "immobilier": (("immobilier", "immo", "agence immobiliere", "agent immobilier",
                    "location", "terrain", "maison a vendre"),
                   "un bot qui qualifie les acheteurs (budget, zone) et planifie "
                   "les visites"),
    "formation": (("formation", "ecole", "école", "cours", "coaching", "academie",
                   "académie", "centre de formation"),
                  "un bot qui répond aux questions des futurs élèves, présente "
                  "tes programmes et collecte les inscriptions"),
    "clinique": (("clinique", "cabinet", "medecin", "médecin", "dentiste",
                  "pharmacie", "sante", "santé"),
                 "un bot qui prend les rendez-vous et répond aux questions "
                 "pratiques (horaires, tarifs)"),
    "transport": (("transport", "livraison", "taxi", "vtc", "logistique", "coursier"),
                  "un bot qui prend les demandes de course ou de livraison et "
                  "confirme les tarifs"),
}

# Le client DÉCLARE son activité (et ne pose pas une question).
_DECLARE_RE = re.compile(
    r"^\s*(j['’ ]?ai\s+(une?|des|mon|ma|mes)\b|je\s+(suis|tiens|g[eè]re|vends|fais|travaille|"
    r"d[ée]marre|lance|poss[eè]de)\b|mon\s+\w+|ma\s+\w+|c['’ ]?est\s+(une?|pour)\b|"
    r"i\s+(have|own|run|sell)\b|my\s+\w+|tengo\s+(un|una)\b|mi\s+\w+)",
    re.IGNORECASE)


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def detect_sector(text: str) -> str:
    """Secteur canonique déclaré dans `text`, ou ''. Mot entier uniquement."""
    low = _norm(text)
    if not low or len(low.split()) > 14 or "?" in low:
        return ""
    for sector, (words, _angle) in SECTORS.items():
        for w in words:
            if re.search(r"(?<![a-zà-ÿ])" + re.escape(w) + r"(?![a-zà-ÿ])", low):
                return sector
    return ""


def is_declaration(text: str) -> bool:
    """« j'ai une boutique », « mon restaurant »... = déclaration, pas question."""
    return bool(_DECLARE_RE.search(_norm(text)))


def reply_for(text: str, last_bot_msg: str = "") -> str | None:
    """Réponse qui NOMME le secteur et pousse UNE action. None si pas applicable.

    Ne s'active que si le client déclare son activité (déclaration claire ou
    réponse directe à une question d'activité du bot)."""
    sector = detect_sector(text)
    if not sector:
        return None
    asked = bool(re.search(r"activit|secteur|tu vends|ce que tu vends|ton business|"
                           r"type d['’ ]activit", _norm(last_bot_msg)))
    if not (is_declaration(text) or asked):
        return None
    angle = SECTORS[sector][1]
    return (f"Top 🔥 {sector.capitalize()}, noté ! Pour toi, on peut faire "
            f"{angle}.\n\n"
            "👉 Tape DEVIS pour ton prix fixe en 2 minutes, ou RDV pour qu'on "
            "en parle avec Ndine.")


def clarify_for(text: str) -> str | None:
    """Mot de secteur SEUL (« restaurant ») : on confirme et on demande l'intention
    au lieu de rester muet. None si ce n'est pas un mot de secteur isolé."""
    sector = detect_sector(text)
    if not sector or len(_norm(text).split()) > 2:
        return None
    return (f"{sector.capitalize()} 👍 Tu veux quoi pour {with_possessive(sector)} ?\n"
            "1️⃣ Un bot qui répond et vend\n2️⃣ Un site web\n"
            "3️⃣ Un logo / visuel\n4️⃣ Un DEVIS complet")


# Possessif correct par secteur (« ta boutique », « ton restaurant »).
POSSESSIVE = {"boutique": "ta", "restaurant": "ton", "salon": "ton",
              "immobilier": "ton", "formation": "ta", "clinique": "ta",
              "transport": "ton"}


def with_possessive(sector: str) -> str:
    """« ta boutique » / « ton restaurant » — jamais « ta restaurant »."""
    return f"{POSSESSIVE.get(sector, 'ton')} {sector}"
