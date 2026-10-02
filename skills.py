# -*- coding: utf-8 -*-
"""Compétences locales de Komara Agency 🇬🇳 : calculatrice express + date/heure.

100% hors-ligne (aucune API), pensé pour le business :
- « calcule 250 x 3 »          → 750
- « 25% de 80000 »              → 20 000 (marge, TVA, part…)
- « 800 - 15% »                 → 680 (remise instantanée)
- « 15000 + 5000 »              → 20 000 (budget)
- « quelle heure est-il »       → heure de Guinée 🇬🇳 (GMT)
- « on est quel jour »          → date du jour

Route : rag_bot._process_text → skills.handle() AVANT la recherche KB.
"""
from __future__ import annotations

import ast
import logging
import re
import unicodedata
from datetime import datetime

logger = logging.getLogger("komara.skills")

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Africa/Conakry")
except Exception:  # pragma: no cover - conteneur sans base tz
    _TZ = None

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
MSG = {
    "fr": {
        "calc": "🧮 {expr} = *{res}*\n\nEnvie d'un devis chiffré pour ton projet ? Tape *devis* 👇",
        "calc_err": "Hmm, ce calcul m'a échappé 😅\nRéessaie avec des chiffres et + - x ÷ %\nEx : *800 - 15%* ou *250 x 3*",
        "time": "🕒 Il est *{h}* (heure de Guinée 🇬🇳, GMT)\n📅 On est le *{d}*\n\nTon business, on en parle ? 😊",
        "days": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
        "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                    "août", "septembre", "octobre", "novembre", "décembre"],
    },
    "en": {
        "calc": "🧮 {expr} = *{res}*\n\nWant a priced quote for your project? Type *quote* 👇",
        "calc_err": "Hmm, that one escaped me 😅\nTry again with numbers and + - x ÷ %\nE.g.: *800 - 15%* or *250 x 3*",
        "time": "🕒 It's *{h}* (Guinea 🇬🇳 time, GMT)\n📅 Today is *{d}*\n\nShall we talk about your business? 😊",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "months": ["January", "February", "March", "April", "May", "June", "July",
                    "August", "September", "October", "November", "December"],
    },
    "es": {
        "calc": "🧮 {expr} = *{res}*\n\n¿Quiere un presupuesto para su proyecto? Escriba *presupuesto* 👇",
        "calc_err": "Hmm, ese cálculo me escapó 😅\nInténtalo de nuevo con números y + - x ÷ %\nEj.: *800 - 15%* o *250 x 3*",
        "time": "🕒 Son las *{h}* (hora de Guinea 🇬🇳, GMT)\n📅 Hoy es *{d}*\n\n¿Hablamos de su negocio? 😊",
        "days": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "months": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                    "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    },
    "ar": {
        "calc": "🧮 {expr} = *{res}*\n\nتريد تسعيرة لمشروعك؟ اكتب *تسعيرة* 👇",
        "calc_err": "همم، هذا الحساب أفلت مني 😅\nحاول مجددا بأرقام و + - x ÷ %\nمثال: *800 - 15%* أو *250 x 3*",
        "time": "🕒 الساعة *{h}* (توقيت غينيا 🇬🇳، GMT)\n📅 اليوم *{d}*\n\nنتحدث عن مشروعك؟ 😊",
        "days": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"],
        "months": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
                    "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
    },
}

def _m(lang: str, key: str) -> dict:
    return MSG.get(lang, MSG["fr"])[key]

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().strip().split())

# questions date/heure acceptées : message ENTIER uniquement (pas de
# « à quelle heure on se voit ? » qui n'est pas une demande d'heure)
_TIME_SETS = {
    "fr": {"quelle heure est il", "quelle heure", "il est quelle heure", "tu es la depuis quelle heure",
           "donne moi l heure", "l heure s il te plait", "on est quel jour", "c est quel jour",
           "aujourd hui c est quel jour", "quelle date", "quelle est la date", "on est le combien",
           "c est quoi la date", "date du jour", "le calendrier", "on est quelle date"},
    "en": {"what time is it", "what time", "what day is it", "what day", "what is the date",
            "whats the date", "what is today", "todays date", "what date is it today"},
    "es": {"que hora es", "que hora", "que dia es hoy", "que dia es", "cual es la fecha",
            "que fecha es hoy", "fecha de hoy"},
    "ar": {"كم الساعة", "ما هي الساعة", "ما اليوم", "ما التاريخ اليوم", "كم الساعة الآن"},
}

# ---------------------------------------------------------------------------
# Date / heure
# ---------------------------------------------------------------------------
def _datetime_reply(lang: str) -> str:
    now = datetime.now(_TZ) if _TZ else datetime.utcnow()
    d = _m(lang, "days")[now.weekday()]
    mo = _m(lang, "months")[now.month - 1]
    if lang == "en":
        h = now.strftime("%I:%M %p").lstrip("0")
        date_str = f"{d}, {mo} {now.day}"
    elif lang == "ar":
        h = f"{now.hour:02d}:{now.minute:02d}"
        date_str = f"{d} {now.day} {mo}"
    else:
        h = f"{now.hour}h{now.minute:02d}"
        date_str = f"{d} {now.day} {mo}"
    return _m(lang, "time").format(h=h, d=date_str)

def is_datetime_request(text: str) -> bool:
    low = _norm(text).rstrip(" ?!.").strip()
    if not low:
        return False
    for lang_set in _TIME_SETS.values():
        if low in lang_set:
            return True
    return False

# ---------------------------------------------------------------------------
# Calculatrice
# ---------------------------------------------------------------------------
_PREFIX = re.compile(
    r"^\s*(?:calculez?|calcul|combien font|combien fait|combien ca fait|combien ça fait"
    r"|ca fait combien|ça fait combien|c est combien|combien c est|resous|résous"
    r"|what is|whats|cuanto es|cuanto da)\s+",
    re.IGNORECASE,
)
_OPS = re.compile(r"[+\-*/%]")
_ALLOWED = re.compile(r"^[\d\s+\-*/().,x×÷=]+$")

def _clean_number(expr: str) -> str:
    # « 1 500 » et « 1,5 » → formats calculables
    for _ in range(3):
        expr = re.sub(r"(\d)\s+(\d{3})(?!\d)", r"\1\2", expr)
    expr = re.sub(r"(\d),(\d)", r"\1.\2", expr)
    return expr

def _apply_percent(expr: str) -> str:
    # « 25% de 80000 » / « 25% de remise sur 80000 »
    expr = re.sub(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:de\s+)?(?:remise\s+)?(?:sur\s+)?(\d+(?:\.\d+)?)",
        r"(\1*\2/100)", expr)
    # « 800 + 15% » / « 800 - 15% » (marge / remise)
    expr = re.sub(
        r"(\d+(?:\.\d+)?)\s*([+\-])\s*(\d+(?:\.\d+)?)\s*%",
        r"(\1*(1\2\3/100))", expr)
    # « 50% » seul → 0.5
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)
    return expr

def _safe_eval(expr: str):
    tree = ast.parse(expr, mode="eval")
    allowed_bin = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                             ast.Add, ast.Sub, ast.Mult, ast.Div,
                             ast.FloorDiv, ast.Mod, ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            continue
        raise ValueError("expression non autorisée")
    return eval(compile(tree, "<calc>", "eval"))  # noqa: S307 — AST filtré

def _fmt(n) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if isinstance(n, int):
        return f"{n:,}".replace(",", " ")
    return f"{n:,.2f}".replace(",", " ").rstrip("0").rstrip(".")

def try_calculate(text: str):
    """Retourne (expr_affichée, résultat) ou None si ce n'est pas un calcul."""
    low = text.strip()
    low = _PREFIX.sub("", low, count=1)
    low = re.sub(r"[=?]+$", "", low).strip()
    if not low or not re.search(r"\d", low):
        return None
    # un opérateur (ou « x » multiplication) doit être présent
    if not re.search(r"[+\-*/%x×÷]", low, re.IGNORECASE):
        return None

    display = low
    work = low.replace("×", "*").replace("÷", "/")
    work = _clean_number(work)
    # pourcentages d'abord : les mots « de / remise / sur » sont consommés
    # par les motifs (« 25% de 80000 », « 800 - 15% »)
    if "%" in work:
        work = _apply_percent(work)
    # « x » = multiplication (250 x 3)
    work = re.sub(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])", "*", work)

    # validation finale : uniquement chiffres et opérateurs
    expr = work.replace(" ", "")
    if not expr or not re.fullmatch(r"[\d+\-*/().]+", expr):
        return None
    if not re.search(r"[+\-*/]", expr):
        return None
    try:
        result = _safe_eval(expr)
    except Exception:
        return ("err", None)
    return (display, result)

# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def handle(bot, chat_id: int, text: str, lang: str) -> bool:
    """True = message consommé (calcul ou date/heure répondu)."""
    try:
        if is_datetime_request(text):
            from rag_bot import menu_for_lang
            bot.send_message(chat_id, _datetime_reply(lang),
                             reply_markup=menu_for_lang(lang))
            return True
        calc = try_calculate(text)
        if calc:
            expr, result = calc
            if result is None:
                from rag_bot import menu_for_lang
                bot.send_message(chat_id, _m(lang, "calc_err"),
                                 reply_markup=menu_for_lang(lang))
            else:
                from rag_bot import menu_for_lang
                bot.send_message(
                    chat_id,
                    _m(lang, "calc").format(expr=expr, res=_fmt(result)),
                    reply_markup=menu_for_lang(lang))
            return True
    except Exception:
        logger.warning("Compétence locale : échec silencieux", exc_info=True)
    return False
