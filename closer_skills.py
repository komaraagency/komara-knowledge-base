"""
Compétences CLOSER 14/10 (Boss 14/10) :
1. dire NON proprement — demande illégale / interdite
2. lire l'émotion — énervé / déçu → empathie + escalade directe
100% local : listes de mots par langue, aucun appel externe.
NB : les réclamations avérées (« escroc », « porter plainte »)
restent gérées par actions.py avant ce module — pas de collision.
"""
import re

# Ce qu'on ne fait JAMAIS : faux documents, piratage, fraudes,
# contenus sexuels, deepfakes... Refus ferme, poli, + réorientation
# vers ce qu'on sait faire (visuels, bots, sites).
_REFUS_RX = re.compile(
    r"\b(faux document|faux papiers|fausse carte|faux diplôme|faux diplome|"
    r"fausse facture|pirater|piratage|hacker|phishing|hameçonn|"
    r"virus|malware|blanchiment|faux billets|contrefaçon|contrefacon|"
    r"faux passeport|fausse identité|fausse identite|"
    r"nudité|nue|porno|sextape|deepfake|usurpation|usurper)\b",
    re.IGNORECASE)

_REFUS_MSG = {
    "fr": "Ça je ne peux pas le faire boss 🙏 Komara Agency ne fait que du "
          "clean : logos, affiches, sites, bots, automatisation. Dis-moi ce "
          "que tu veux créer pour ton business, je m'y mets tout de suite 👇",
    "en": "That I can't do boss 🙏 Komara Agency only does clean work: logos, "
          "posters, websites, bots, automation. Tell me what you want to "
          "create for your business, I'm on it right away 👇",
    "es": "Eso no puedo hacerlo jefe 🙏 Komara Agency solo hace trabajo limpio: "
          "logos, pósters, sitios, bots, automatización. Dime qué quieres "
          "crear para tu negocio, me pongo ahora mismo 👇",
    "ar": "هذا لا أستطيع فعله يا زعيم 🙏 وكالة كومارا تقوم فقط بعمل نظيف: "
          "شعارات، ملصقات، مواقع، بوتات، أتمتة. أخبرني ماذا تريد إنشاء "
          "لعملك، أبدأ فوراً 👇",
}

# Émotion négative : désamorcer avec empathie + passage direct au boss.
_NEGATIVE_RX = re.compile(
    r"\b(énervé|enerve|ras le bol|nul|déçu|decu|scandaleux|honteux|"
    r"ça ne marche pas|ca ne marche pas|ça marche pas|ca marche pas|"
    r"n'importe quoi|nimporte quoi|rembourse|remboursé|remboursement|"
    r"frustrated|angry|useless|garbage|annoyed|refund|"
    r"estafa|basura|مستاء|غاضب|استرجاع)\b",
    re.IGNORECASE)

_NEGATIVE_MSG = {
    "fr": "Je comprends ta frustration, désolé que ça coince 🙏 Laisse-moi te "
          "passer Ndine en direct pour régler ça vite : WhatsApp "
          "+212 701 986 219. Envoie-lui « AYA » et il te prend en charge "
          "immédiatement.",
    "en": "I understand your frustration, sorry for the trouble 🙏 Let me put "
          "you straight through to Ndine: WhatsApp +212 701 986 219. Send him "
          "\"AYA\" and he'll take care of you right away.",
    "es": "Entiendo tu frustración, lamento el problema 🙏 Te paso "
          "directamente con Ndine: WhatsApp +212 701 986 219. Envíale \"AYA\" "
          "y te atiende de inmediato.",
    "ar": "أتفهم إحباطك، آسف على المشكلة 🙏 دعني أوصلك مباشرة بـدين: "
          "واتساب +212 701 986 219. أرسل له «AYA» وسيتولى أمرك فوراً.",
}


def is_offlimits(text: str) -> bool:
    """Demande illégale / interdite → True (refus + réorientation)."""
    return bool(_REFUS_RX.search(text or ""))


def refusal_message(lang: str) -> str:
    return _REFUS_MSG.get(lang, _REFUS_MSG["fr"])


def is_negative_emotion(text: str) -> bool:
    """Client énervé / déçu → True (empathie + escalade boss)."""
    return bool(_NEGATIVE_RX.search(text or ""))


def empathy_message(lang: str) -> str:
    return _NEGATIVE_MSG.get(lang, _NEGATIVE_MSG["fr"])
