"""phone_validation — un SEUL validateur de numéro pour tout le bot (Boss 09/10).

Contexte (audit des conversations réelles) : un client a tapé
« Okmjhcfj » puis « +21287654213 » — enregistré comme numéro de commande
alors que c'est un FAUX numéro marocain (un vrai +212 a 9 chiffres après
l'indicatif, mobile 06/07). Avant ce module, chaque tunnel avait sa règle :
« >= 7 chiffres » (commande/lead), « >= 8 chiffres » (humain), et après
2 échecs le tunnel commande acceptait N'IMPORTE QUEL texte.

Règles (100% locales, aucune API) :
  1. Longueur E.164 : 8 à 15 chiffres au total.
  2. Indicatif connu : la longueur locale ET la tranche de premier
     chiffre sont vérifiées (ex : +212 -> 9 chiffres locaux commençant
     par 5/6/7/8 ; +224 -> 9 chiffres commençant par 6...).
  3. Numéro sans indicatif : 8 à 15 chiffres tolérés (le numéro local
     marocain/guinéen « 06... »/« 6... » passe).
  4. Faux évidents REJETÉS : tout le même chiffre, suite croissante
     (123456789), >= 6 fois le même chiffre d'affilée, que des zéros.

API :
  ok, reason = check(raw)      # reason : "ok" ou code d'erreur
  pretty      = normalize(raw) # "+2126..." propre, ou "" si invalide
  REASONS     # messages d'erreur par langue, clé = reason
"""
from __future__ import annotations

import re

# Indicatifs connus du bot : (indicatif, longueur locale, premiers chiffres locaux acceptés)
# Vide/None = tous les premiers chiffres acceptés.
KNOWN_CC: dict[str, tuple[int, tuple[str, ...] | None]] = {
    "212": (9, ("5", "6", "7", "8")),   # Maroc
    "224": (9, ("6",)),                 # Guinée
    "221": (9, ("7",)),                 # Sénégal
    "225": (10, ("0", "1", "2", "5", "7")),  # Côte d'Ivoire
    "223": (8, ("6", "7", "8", "9")),   # Mali
    "226": (8, None),                   # Burkina
    "229": (10, None),                  # Bénin
    "220": (7, ("2", "3", "6", "7", "9")),  # Gambie
    "237": (9, ("6", "7")),             # Cameroun
    "243": (9, None),                   # RDC
    "241": (8, None),                   # Gabon
    "233": (10, ("2", "3", "5")),       # Ghana
    "254": (9, ("1", "7")),             # Kenya
    "33": (9, ("1", "2", "3", "4", "5", "6", "7", "8", "9")),  # France
    "32": (8, None),                    # Belgique
    "41": (9, ("6", "7")),              # Suisse
    "1": (10, None),                    # USA/Canada
    "44": (10, ("7")),                  # UK
}

# Messages de re-demande par raison, par langue.
REASONS: dict[str, dict[str, str]] = {
    "not_digits": {
        "fr": "Hmm, je ne lis pas bien ce numéro 😅 Envoie-le en chiffres, par "
              "exemple : 06 12 34 56 78 ou +224 622 00 00 00 📞",
        "en": "Hmm, I can't read that number 😅 Send digits only, e.g. "
              "06 12 34 56 78 or +224 622 00 00 00 📞",
        "es": "No leo bien ese número 😅 Envíalo en dígitos, ej : "
              "06 12 34 56 78 o +224 622 00 00 00 📞",
        "ar": "لم أقرأ الرقم جيدا 😅 أرسله بالأرقام، مثال : "
              "06 12 34 56 78 أو +224 622 00 00 00 📞",
    },
    "bad_length": {
        "fr": "Ce numéro a un nombre de chiffres impossible 😅 Un numéro "
              "valide a 8 à 15 chiffres (ex : 06 12 34 56 78). Réessaie 📞",
        "en": "That digit count is impossible 😅 A valid number has 8-15 "
              "digits (e.g. 06 12 34 56 78). Try again 📞",
        "es": "Esa cantidad de dígitos es imposible 😅 Un número válido "
              "tiene 8-15 dígitos (ej : 06 12 34 56 78). Inténtalo de nuevo 📞",
        "ar": "عدد الأرقام غير صحيح 😅 الرقم الصحيح يحتوي على 8 إلى 15 رقما "
              "(مثال : 06 12 34 56 78). حاول مرة أخرى 📞",
    },
    "bad_country": {
        "fr": "Ce numéro ne correspond pas à un vrai numéro {cc} 😅 Vérifie "
              "l'indicatif et le nombre de chiffres (ex : +212 6 12 34 56 78)."
              " Réessaie 📞",
        "en": "That doesn't match a real {cc} number 😅 Check the country "
              "code and digit count (e.g. +212 6 12 34 56 78). Try again 📞",
        "es": "Eso no corresponde a un número real de {cc} 😅 Verifica el "
              "prefijo y la cantidad de dígitos (ej : +212 6 12 34 56 78). "
              "Inténtalo de nuevo 📞",
        "ar": "هذا الرقم لا يطابق رقما حقيقيا {cc} 😅 تحقق من رمز الدولة وعدد "
              "الأرقام (مثال : +212 6 12 34 56 78). حاول مرة أخرى 📞",
    },
    "fake_pattern": {
        "fr": "Ça ressemble à un numéro inventé 😅 Envoie ton VRAI numéro "
              "WhatsApp (ex : 06 12 34 56 78) pour que l'équipe te contacte 📞",
        "en": "That looks like a made-up number 😅 Send your REAL WhatsApp "
              "number (e.g. 06 12 34 56 78) so the team can reach you 📞",
        "es": "Parece un número inventado 😅 Envía tu número REAL de WhatsApp "
              "(ej : 06 12 34 56 78) para que el equipo te contacte 📞",
        "ar": "يبدو رقما مختلقا 😅 أرسل رقم واتساب الحقيقي (مثال : "
              "06 12 34 56 78) ليتواصل معك الفريق 📞",
    },
}

_FAKE_REASONS = ("not_digits", "bad_length", "bad_country", "fake_pattern")


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _is_fake(digits: str) -> bool:
    """Faux évidents : tout pareil, suite croissante, >= 6 répétitions, que des 0."""
    if len(set(digits)) == 1:
        return True
    if digits == "".join(str((int(digits[0]) + i) % 10) for i in range(len(digits))):
        return True
    if re.search(r"(\d)\1{6,}", digits):
        return True
    return False


def check(raw: str) -> tuple[bool, str]:
    """(valide, raison). Raison « ok » si valide, sinon code d'erreur."""
    text = (raw or "").strip()
    if not text:
        return False, "not_digits"
    digits = _digits(text)
    if len(digits) == 0:
        return False, "not_digits"
    if len(digits) < 8 or len(digits) > 15:
        return False, "bad_length"
    if _is_fake(digits):
        return False, "fake_pattern"

    # Indicatif pays ? (+CC ou 00CC)
    cc, local = "", digits
    m = re.match(r"^(?:\+|00)(\d{1,3})", text.replace(" ", ""))
    if m:
        for width in (3, 2, 1):
            cand = digits[:width]
            if cand in KNOWN_CC:
                cc, local = cand, digits[width:]
                break
        if not cc:
            cc, local = digits[:3], digits[3:]  # indicatif inconnu : tolérant

    if cc and cc in KNOWN_CC:
        want_len, firsts = KNOWN_CC[cc]
        if len(local) != want_len:
            return False, "bad_country"
        if firsts and not local.startswith(tuple(firsts)):
            return False, "bad_country"
    return True, "ok"


def normalize(raw: str) -> str:
    """Forme canonique lisible (« +2126XXXXXXXX » ou « 06XXXXXXXX »),
    ou '' si le numéro est invalide."""
    ok, _ = check(raw)
    if not ok:
        return ""
    text = (raw or "").strip()
    digits = _digits(text)
    if text.startswith("+") or text.startswith("00"):
        m = re.match(r"^(?:\+|00)(\d{1,3})", text.replace(" ", ""))
        cc = ""
        for width in (3, 2, 1):
            if digits[:width] in KNOWN_CC:
                cc = digits[:width]
                break
        if not cc and m:
            cc = m.group(1)
        if cc:
            return f"+{cc}{digits[len(cc):]}"
        return f"+{digits}"
    return digits


def reject_msg(raw: str, lang: str = "fr") -> str:
    """Message de re-demande pour un numéro invalide, avec l'indicatif
    pays détecté (ex : « vrai numéro +212 ») quand c'est pertinent."""
    ok, reason = check(raw)
    if ok:
        return reason_msg("not_digits", lang)
    cc = ""
    if reason == "bad_country":
        digits = _digits(raw)
        text = (raw or "").strip().replace(" ", "")
        if text.startswith("+") or text.startswith("00"):
            for width in (3, 2, 1):
                if digits[:width] in KNOWN_CC:
                    cc = "+" + digits[:width]
                    break
    return reason_msg(reason, lang, cc=cc)


def reason_msg(reason: str, lang: str = "fr", cc: str = "") -> str:
    """Message de re-demande adapté à la raison d'invalidité."""
    lang = lang if lang in ("fr", "en", "es", "ar") else "fr"
    msgs = REASONS.get(reason) or REASONS["not_digits"]
    return msgs.get(lang, msgs["fr"]).replace("{cc}", cc or "de ce pays")
