"""service_followup — le bot comprend la réponse à SA question de précision.

Capture Boss 09/10 23h49 :
    Bot    : « Logo ? Dis-moi 😊 Tu pars de zéro ou tu modernises un logo existant ? »
    Client : « Modernise un logo existent »
    Bot    : « Bonne question, mais elle dépasse mes connaissances actuelles »  <- il
             ne comprend pas la réponse à SA PROPRE question.

Règle (même esprit que pending_question et sector_reply) : une question de
précision posée par le bot est PRIORITAIRE. Si le dernier message du bot est
la question de précision d'un service (logo, site, bot, vidéo, visuel...), on
reconnaît la réponse libre du client (variantes, fautes, accents) et on répond
sur CE service avec UNE suite concrète : prix + devis.

100% local, déterministe, aucune invention : prix repris tels quels des
tarifs officiels (logo 50 €, site dès 200 $, bot dès 150 $). Réponse non
reconnue -> None (le moteur continue normalement).
"""
from __future__ import annotations

import re
import unicodedata

CONTACT_WA = "wa.me/212701986219"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).casefold()


# Question du bot (fin de message) -> service précisé
_ASKED = [
    ("logo", re.compile(r"(de zero ou|nouvelle marque|relooking|un logo pour ton business|"
                        r"starting from scratch|modernizing an existing logo|parte de cero)")),
    ("site", re.compile(r"(site vitrine pour te presenter|boutique en ligne pour vendre|"
                        r"presenter ton business ou vendre|showcase site|online shop)")),
    ("bot", re.compile(r"(bot whatsapp, un bot telegram|commander un bot, voir une demo|"
                       r"a whatsapp bot, a telegram bot)")),
    ("video", re.compile(r"(video publicitaire pour tes reseaux|une promo, une presentation|"
                         r"an ad video for your networks)")),
    ("visuel", re.compile(r"(visuel pour tes reseaux, une affiche promo|"
                          r"une affiche pour une promo, tes statuts)")),
]

# Réponses libres reconnues, par service : (clé, regex sur la réponse du client)
_ANSWERS = {
    "logo": [
        ("refonte", re.compile(r"(modernis|moderniz|refais|refaire|refonte|relook|existant|existent|"
                               r"rafrai|ameliore|ancien|j'?ai deja|retouch|actualis|update|refresh|"
                               r"redesign|actualiz)")),
        ("nouveau", re.compile(r"(zero|scratch|nouveau|nouvelle|nouvel|debut|cree|creer|from scratch|"
                               r"depuis rien|new|cero|je n'?ai pas|pas encore)")),
    ],
    "site": [
        ("vitrine", re.compile(r"(vitrine|presenter|presentation|showcase|portfolio|informatif)")),
        ("boutique", re.compile(r"(boutique|vendre|vente|shop|e-?commerce|store|tienda)")),
    ],
    "bot": [
        ("whatsapp", re.compile(r"(whatsapp|watsap|wa\b)")),
        ("telegram", re.compile(r"(telegram|telegramme)")),
        ("commande", re.compile(r"(commande|commander|order|business|entreprise)")),
        ("demo", re.compile(r"(demo|voir|essai|tester|test)")),
    ],
    "video": [
        ("pub", re.compile(r"(pub|publicit|promo|ad\b|reseaux)")),
        ("presentation", re.compile(r"(presentation|presenter|business|entreprise)")),
    ],
    "visuel": [
        ("affiche", re.compile(r"(affiche|promo|poster|flyer)")),
        ("statut", re.compile(r"(statut|status|whatsapp|reseaux)")),
        ("pack", re.compile(r"(pack|complet|identite|full)")),
    ],
}

_REPLIES = {
    ("logo", "refonte"): (
        "Parfait, on modernise ton logo 🎨 On garde l'esprit de ta marque et on "
        "le rend plus propre, plus pro, prêt pour les réseaux. Un logo livré "
        "coûte à partir de 50 € (24h). Envoie-moi ton logo actuel en photo, ou "
        "tape 'devis' pour ton estimation gratuite en 2 minutes 🚀"),
    ("logo", "nouveau"): (
        "Super, on part de zéro 🎨 Dis-moi le nom de ta marque et ton activité, "
        "je te prépare une proposition. Un logo livré coûte à partir de 50 € "
        "(24h). Tape 'devis' pour ton estimation gratuite en 2 minutes 🚀"),
    ("site", "vitrine"): (
        "Un site vitrine, noté 👌 Il présente ton business, tes services et tes "
        "contacts. À partir de 200 $. Tape 'devis' pour ton estimation gratuite "
        "en 2 minutes 🚀"),
    ("site", "boutique"): (
        "Une boutique en ligne, bien vu 🛒 Catalogue, panier et commandes. "
        "À partir de 200 $. Tape 'devis' pour ton estimation gratuite en "
        "2 minutes 🚀"),
    ("bot", "whatsapp"): (
        "Un bot WhatsApp, excellent choix 💬 Il répond à tes clients 24/7, "
        "prend les commandes et te passe la main au besoin. À partir de 150 $. "
        "Tape 'devis' pour ton estimation gratuite 🚀"),
    ("bot", "telegram"): (
        "Un bot Telegram, parfait 🤖 Il répond, qualifie tes clients et envoie "
        "les contacts dans ton Google Sheet. À partir de 150 $. Tape 'devis' "
        "pour ton estimation gratuite 🚀"),
    ("bot", "commande"): (
        "Très bien, on te crée un bot sur mesure 🤖 À partir de 150 $. Tape "
        "'devis' et je te pose 3 questions rapides pour ton estimation gratuite 🚀"),
    ("bot", "demo"): (
        "Avec plaisir 👀 Tape 'portfolio' pour voir nos démos de bots, puis "
        "choisis celle qui t'intéresse par son numéro ou son titre."),
    ("video", "pub"): (
        "Une vidéo pub pour tes réseaux, noté 🎬 Dis-moi ton activité et "
        "l'objectif (promo, lancement), je prépare une proposition. Tape "
        "'devis' pour ton estimation gratuite 🚀"),
    ("video", "presentation"): (
        "Une vidéo de présentation, bien vu 🎬 Dis-moi ton activité, je "
        "prépare une proposition. Tape 'devis' pour ton estimation gratuite 🚀"),
    ("visuel", "affiche"): (
        "Une affiche promo, parfait 🎨 Dis-moi l'événement ou l'offre, je "
        "prépare une proposition. Tape 'devis' pour ton estimation gratuite 🚀"),
    ("visuel", "statut"): (
        "Des visuels pour tes statuts WhatsApp, noté 📲 Dis-moi ton activité "
        "et le message à passer. Tape 'devis' pour ton estimation gratuite 🚀"),
    ("visuel", "pack"): (
        "Un pack complet d'identité visuelle, excellent 💎 Dis-moi ton "
        "activité, je prépare une proposition. Tape 'devis' pour ton "
        "estimation gratuite 🚀"),
}


def asked_service(last_bot_msg: str) -> str:
    """Service dont le bot vient de demander la précision, ou ''."""
    tail = _norm(last_bot_msg or "")[-260:]
    if "?" not in tail:
        return ""
    for name, rx in _ASKED:
        if rx.search(tail):
            return name
    return ""


def reply_for(user_text: str, last_bot_msg: str) -> str | None:
    """Réponse cohérente à la réponse libre du client, ou None."""
    service = asked_service(last_bot_msg)
    if not service:
        return None
    low = _norm(user_text)
    if not low or len(low) > 160:        # un long texte = nouveau sujet
        return None
    for key, rx in _ANSWERS.get(service, []):
        if rx.search(low):
            return _REPLIES.get((service, key))
    return None
