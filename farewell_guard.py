"""farewell_guard — « Avant de partir » seulement aux vraies fins (Boss 09/10).

Capture : le client parcourt le portfolio (« Une autre réalisation ? »),
dit « Merci », et le bot sort « Avant de partir : - Tape CATALOGUE... À tout
de suite ! » comme si le client partait — alors qu'il est en pleine visite.

Règle :
- Mots de REMERCIEMENT seuls (« merci », « super », « thanks ») pendant une
  conversation ACTIVE (flux en cours, question en attente, liste/menu qui
  vient d'être envoyé) → petit ack, on ne clôt PAS.
- Vrais mots de DÉPART (« au revoir », « bye », « à plus »...) → rien ne
  change, la fiche de clôture du Sheet reste légitime.

100 % local, aucune API.
"""
from __future__ import annotations

import re

# Remerciements seuls (la fiche L155 matche aussi « super »).
_THANKS_RE = re.compile(
    r"^\s*(merci(?:\s+(?:beaucoup|bien|à toi|a toi|pour\s+tout))?|"
    r"thanks?|thank\s*you|muchas\s+gracias|gracias|"
    r"super|parfait|g[ée]nial|cool|"
    r"شكرا|شكراً)\s*[.!…]*\s*$",
    re.IGNORECASE)

# Vrais départs : là seulement, la clôture « Avant de partir » est légitime.
_GOODBYE_RE = re.compile(
    r"\b(au\s*revoir|bye|ciao|good\s*bye|goodbye|\belias\b|"
    r"[àa]\s*plus|[àa]\s*bient[oô]t|bonne\s*journ[ée]e|bonne\s*nuit|"
    r"hasta\s+luego|adi[oó]s|وداعا|مع السلامة)\b",
    re.IGNORECASE)


def is_thanks_only(text: str) -> bool:
    """« Merci », « Super », « Thanks » SEULS (pas « merci pour le devis »)."""
    return bool(_THANKS_RE.match((text or "").strip())) and not is_leaving(text)


def is_leaving(text: str) -> bool:
    return bool(_GOODBYE_RE.search(text or ""))


def conversation_active(chat_id: int, last_bot_msg: str) -> bool:
    """True si la conversation CONTINUE : flux en cours, question en attente,
    ou liste/menu qui vient d'être envoyé (portfolio, services, catalogue)."""
    import actions

    # 1. Un tunnel est en cours (devis, commande, rdv, lead, qualif...)
    try:
        if actions._fetch_flow(chat_id):
            return True
    except Exception:
        pass

    # 2. Le bot vient de poser une question (y compris questions en attente)
    msg = last_bot_msg or ""
    if "?" in msg:
        return True
    try:
        import pending_question
        if pending_question.detect(msg):
            return True
    except Exception:
        pass

    # 3. Une liste numérotée vient d'être envoyée (portfolio / menu services)
    if "1️⃣" in msg or msg.rstrip().endswith("👇"):
        return True

    # 4. Un contexte de liste est actif (portfolio, services, catalogue)
    try:
        import list_context
        if list_context.get_context(chat_id):
            return True
    except Exception:
        pass
    return False


# Petit ack par langue (on ne clôt pas, on reste dans la conversation).
_THANKS_MID = {
    "fr": "Avec plaisir 🙏 On continue quand tu veux — je t'écoute 👇",
    "en": "You're welcome 🙏 I'm here whenever you need me 👇",
    "es": "Con gusto 🙏 Sigo aquí cuando quieras 👇",
    "ar": "على الرحب والسعة 🙏 أنا هنا وقتما تشاء 👇",
}


def mid_thanks_reply(lang: str) -> str:
    return _THANKS_MID.get(lang, _THANKS_MID["fr"])
