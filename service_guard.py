# ---------------------------------------------------------------------------
# service_guard.py — Cohérence de SERVICE entre la demande et la fiche
# (Boss 09/10, capture 23h48).
#
# BUG : « Est-ce que tu peux me créer un logo » recevait la fiche
# « Est-ce que tu crées des chatbots » -> « Oui, je crée des chatbots ».
# Cause : 5 mots sur 7 se recoupent (« est ce que tu ... cré ») et même le
# CrossEncoder préfère cette fiche (+4,67) : un modèle générique ne sait pas
# que « logo » et « chatbot » sont deux PRODUITS différents de l'agence.
#
# RÈGLE MÉTIER déterministe : si le client NOMME un service précis, une fiche
# qui ne parle QUE d'un autre service est écartée. Une fiche neutre (qui ne
# cite aucun service) ou qui cite le même service reste candidate.
# ---------------------------------------------------------------------------
import re
import unicodedata

# service -> racines (sans accents, minuscules), mots entiers ou préfixes
_SERVICES = {
    "logo":   (r"logos?", r"logotype", r"embleme", r"identite visuelle", r"charte graphique"),
    "bot":    (r"bots?", r"chatbots?", r"robots?", r"agents? ia", r"assistant virtuel"),
    "site":   (r"sites?", r"site web", r"websites?", r"vitrine", r"boutique en ligne", r"e-?commerce"),
    "visuel": (r"affiches?", r"flyers?", r"visuels?", r"posters?", r"banniere", r"carte de visite"),
    "video":  (r"videos?", r"montage", r"clips?", r"reels?"),
    "photo":  (r"photos?", r"shooting", r"portraits?"),
    "app":    (r"applications?", r"applis?", r"apps?"),
    "formation": (r"formations?", r"cours", r"coaching"),
}
# SECTEURS (Boss 09/10, capture : « Je veux un logo » -> « Parfait pour ton
# resto » alors que le client n'a JAMAIS dit resto). Une fiche écrite pour un
# secteur précis n'est servie que si le client a nommé ce secteur.
_SECTORS = {
    "resto": (r"restos?", r"restaurants?", r"maquis", r"traiteur", r"fast-?food", r"snack"),
    "boutique": (r"boutiques?", r"magasin", r"friperie", r"vetements?", r"mode"),
    "salon": (r"salon", r"coiffure", r"coiffeu\w+", r"barber", r"beaute", r"esthetique"),
    "clinique": (r"cliniques?", r"cabinet", r"medecin", r"dentiste", r"pharmacie"),
    "immo": (r"immobilier", r"immo", r"terrain", r"agence immobiliere"),
    "ecole": (r"ecoles?", r"etablissement scolaire", r"universite"),
}
_SECTOR_RE = {
    name: re.compile(r"\b(?:" + "|".join(pats) + r")\b")
    for name, pats in _SECTORS.items()
}

_SERVICE_RE = {
    name: re.compile(r"\b(?:" + "|".join(pats) + r")\b")
    for name, pats in _SERVICES.items()
}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower().replace("\u2011", "-").replace("\u2019", "'")


def services_in(text: str) -> set:
    """Ensemble des services explicitement nommés dans le texte."""
    n = _norm(text)
    return {name for name, rx in _SERVICE_RE.items() if rx.search(n)}


def sectors_in(text: str) -> set:
    """Secteurs d'activité explicitement nommés dans le texte."""
    n = _norm(text)
    return {name for name, rx in _SECTOR_RE.items() if rx.search(n)}


def is_compatible(message: str, candidate_question: str, candidate_answer: str = "") -> bool:
    """True si la fiche peut répondre au message sur le plan du service.

    - le client ne nomme aucun service       -> compatible (pas de règle)
    - la fiche ne nomme aucun service        -> compatible (fiche générale)
    - la fiche cite au moins un service du client -> compatible
    - la fiche cite UNIQUEMENT d'autres services  -> INCOMPATIBLE
    On juge sur la QUESTION de la fiche d'abord (ce qu'elle couvre vraiment) ;
    la réponse ne sert qu'en repli quand la question est muette."""
    # SECTEUR : la fiche vise un secteur (resto...) que le client n'a pas
    # nommé -> elle suppose une info qu'il n'a pas donnée -> écartée.
    # (On juge la QUESTION de la fiche, pas la réponse commerciale.)
    fiche_sectors = sectors_in(candidate_question)
    if fiche_sectors and not (fiche_sectors & sectors_in(message)):
        return False
    wanted = services_in(message)
    if not wanted:
        return True
    offered = services_in(candidate_question)
    if not offered:
        offered = services_in(candidate_answer)
    if not offered:
        return True
    return bool(wanted & offered)
