"""pending_question — le bot se souvient de CE QU'IL A DEMANDÉ (Boss 08/10).

Problème signalé (captures du 08/10) : le bot pose une question
contextuelle (« Tu veux voir nos tarifs ? », « Tu vends quoi ? »,
« Dis-moi c'est pour quoi ? 1-4 ») puis oublie sa propre question :
la réponse du client est traitée comme un NOUVEAU sujet et mélangée
avec une fiche sans rapport.

Règle d'or : une question posée par le bot est PRIORITAIRE. La
réponse du client (oui / d'accord / un chiffre / un secteur) est une
réponse À CETTE QUESTION, jamais un nouveau sujet.

Ce module est 100 % déterministe (aucune IA) :
  - detect(last_bot_msg)  -> sujet de la question en attente (ou "")
  - answer(topic, kind)   -> réponse cohérente avec ce sujet

Sujets reconnus à la FIN du message du bot (la question vit dans la
dernière phrase) : tarifs, exemples (portfolio), devis, rdv, secteur,
whatsapp (numéro), menu_bot (1-4), services.
"""
from __future__ import annotations

import re
import unicodedata

CONTACT_WA = "wa.me/212701986219"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).casefold()


def _tail(last_bot_msg: str, n: int = 220) -> str:
    """La question vit dans la FIN du message (dernières ~220 lettres)."""
    return _norm(last_bot_msg.strip())[-n:]


# Chaque sujet : (nom, regex sur la fin du message du bot).
# Ordre = priorité (le plus précis d'abord).
_TOPICS: list[tuple[str, re.Pattern]] = [
    ("tarifs", re.compile(
        r"(voir|veux|souhaite)[^?.!]{0,30}(nos |les |mes )?(tarifs|prix)|"
        r"(tarifs|prix)[^?.!]{0,30}en attendant|"
        r"(tarifs|prix)[^?]{0,15}\?\s*$")),
    ("menu_bot", re.compile(
        r"c'?est pour quoi|dis-?moi c'?est pour quoi|"
        r"1\ufe0f?\u20e3[^\n]*vente[^\n]*\n[^\n]*support")),
    ("whatsapp", re.compile(
        r"(laisse|donne|envoie|ton)[^?.!\n]{0,25}(num[eé]ro|whatsapp)|"
        r"(num[eé]ro|whatsapp)[^?.!\n]{0,12}$")),
    ("exemples", re.compile(
        r"(voir|veux|montrer)[^?.!]{0,25}(exemple|r[eé]alisation|portfolio|en action)|"
        r"(exemple|r[eé]alisation)s?[^?]{0,20}\?\s*$")),
    ("rdv", re.compile(
        r"(caler|r[eé]server|prendre)[^?.!]{0,25}(rdv|rendez|appel|cr[eé]neau)|"
        r"quel est ton business pour que je pr[eé]pare")),
    ("secteur", re.compile(
        r"(tu vends|tu vends quoi|quel secteur|quelle activit[eé]|ton business|"
        r"boutique, resto ou service|ton activit[eé])[^?]{0,40}\?")),
    ("devis", re.compile(
        r"(devis|estimation)[^?]{0,40}\?\s*$|lance le devis")),
]


def detect(last_bot_msg: str) -> str:
    """Sujet de la question en attente, ou "" si le bot n'a rien demandé."""
    tail = _tail(last_bot_msg or "")
    if not tail or "?" not in tail and "tape" not in tail and "num" not in tail:
        return ""
    for name, rx in _TOPICS:
        if rx.search(tail):
            return name
    return ""


_RE_TARIFS = (
    "Voilà nos tarifs 💰\n\n"
    "• Logo seul : 50 € (livré en 24h)\n"
    "• Pack Resto Complet (Logo + Affiche FB + Carte visite) : 120 €\n"
    "• Site vitrine : à partir de 200 $\n"
    "• Bot / Agent IA : à partir de 150 $\n\n"
    "Tape 'devis' et je te fais ton estimation gratuite en 2 minutes 🚀"
)
_RE_EXEMPLES = (
    "Je te montre 👇 Tape 'portfolio' pour voir toutes nos réalisations, "
    "ou donne-moi le numéro ou le titre de celle qui t'intéresse."
)
_RE_RDV = (
    "Parfait, on cale ça 🔥 Écris directement à N-Dine sur WhatsApp : "
    f"{CONTACT_WA}\nDis juste \"RDV\" et on te répond dans les 10 min."
)
_RE_WHATSAPP = (
    "Envoie-moi ton numéro WhatsApp (avec l'indicatif) et l'équipe "
    "KOMARA te contacte sous 5 min ⚡"
)
_RE_DEVIS = (
    "Super 🚀 Tape 'devis' maintenant : je te pose 3 questions rapides "
    "et tu reçois ton estimation gratuite en 2 minutes."
)
_RE_SECTEUR = (
    "Dis-moi en une phrase ce que tu vends (boutique, resto, service...) "
    "et je te prépare la bonne proposition 👇"
)
_RE_MENU_BOT = (
    "Dis-moi le numéro qui te correspond 👇\n"
    "1️⃣ Vente / e-commerce\n2️⃣ Support client\n"
    "3️⃣ Prise de RDV\n4️⃣ Qualif de leads"
)

_YES_ANSWERS = {
    "tarifs": _RE_TARIFS,
    "exemples": _RE_EXEMPLES,
    "rdv": _RE_RDV,
    "whatsapp": _RE_WHATSAPP,
    "devis": _RE_DEVIS,
    "secteur": _RE_SECTEUR,
    "menu_bot": _RE_MENU_BOT,
}

# Réponses pour un NON (le client refuse la proposition)
_NO_ANSWER = (
    "Pas de souci 👍 Je reste là si tu changes d'avis. "
    "Une autre question ? Ou tape 'services' pour voir ce qu'on fait."
)

# Menu bot 1-4 : le chiffre répond à la question « c'est pour quoi ? »
_MENU_BOT_REPLIES = {
    1: ("Vente / e-commerce 🛒 Parfait : le bot prend les commandes, envoie prix + "
        "photos et relance les clients 24/7. Tu vends quoi exactement ? "
        "Tape 'devis' pour l'estimation gratuite."),
    2: ("Support client 💬 Le bot répond aux questions fréquentes 24/7 et te passe "
        "la main quand il faut. Sur quel canal : WhatsApp ou Telegram ?"),
    3: ("Prise de RDV 📅 Le bot propose les créneaux, confirme et te prévient. "
        "Tu veux le brancher sur WhatsApp ou Telegram ?"),
    4: ("Qualification de leads 🎯 Le bot trie chaud/froid et t'envoie les bons "
        "contacts dans ton Google Sheet. Tu vends dans quel secteur ?"),
}


def answer_yes(topic: str) -> str | None:
    """Réponse à un OUI / D'ACCORD pour le sujet en attente."""
    return _YES_ANSWERS.get(topic)


def answer_no(topic: str) -> str | None:
    return _NO_ANSWER if topic else None


def answer_number(topic: str, index: int) -> str | None:
    """Réponse à un chiffre nu pour le sujet en attente (menu bot)."""
    if topic == "menu_bot":
        return _MENU_BOT_REPLIES.get(index)
    return None
