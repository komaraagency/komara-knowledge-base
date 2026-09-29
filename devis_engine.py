# -*- coding: utf-8 -*-
"""
devis_engine.py — Feature #1 : Convertisseur de Devis Multi-Devises
Komara Agency 🇬🇳 — 100 % local, zéro API payante à chaque requête.

- data/all_currencies.json : 220 pays → monnaie (ISO 4217) + symbole
- data/rates.json          : taux de change base EUR (règle d'or : jamais
  USD comme base — l'€ est la base de vente internationale fixe)
                             1×/semaine par update_rates.py (API gratuite)
- convert_devis(pays_code, prix_base) : prix local + monnaie, fallback USD
- Détection localité : n° WhatsApp (+224 = GN…) → language_code → demander
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("komara")

BASE_DIR = Path(__file__).resolve().parent
CURRENCIES_FILE = BASE_DIR / "data" / "all_currencies.json"
RATES_FILE = BASE_DIR / "data" / "rates.json"

BASE_CURRENCY = "EUR"  # le catalogue Komara est en € (règle d'or)
USD_PER_EUR_FALLBACK = 1.09  # secours si rates.json indisponible
EUR_RATE_FALLBACK = 1.09  # 1 EUR = 1.09 USD si rates.json indisponible

# ---------------------------------------------------------------------------
# Chargement (mis en cache, rechargé si le fichier change)
# ---------------------------------------------------------------------------
_CURRENCIES_CACHE: dict | None = None
_RATES_CACHE: dict | None = None
_CURRENCIES_MTIME = 0.0
_RATES_MTIME = 0.0


def _load_currencies() -> dict:
    global _CURRENCIES_CACHE, _CURRENCIES_MTIME
    try:
        mtime = CURRENCIES_FILE.stat().st_mtime
        if _CURRENCIES_CACHE is None or mtime != _CURRENCIES_MTIME:
            _CURRENCIES_CACHE = json.loads(CURRENCIES_FILE.read_text(encoding="utf-8"))
            _CURRENCIES_MTIME = mtime
    except Exception as e:
        logger.error("Chargement all_currencies.json impossible : %s", e)
        _CURRENCIES_CACHE = _CURRENCIES_CACHE or {}
    return _CURRENCIES_CACHE


def _load_rates() -> dict:
    global _RATES_CACHE, _RATES_MTIME
    try:
        mtime = RATES_FILE.stat().st_mtime
        if _RATES_CACHE is None or mtime != _RATES_MTIME:
            _RATES_CACHE = json.loads(RATES_FILE.read_text(encoding="utf-8"))
            _RATES_MTIME = mtime
    except Exception as e:
        logger.error("Chargement rates.json impossible : %s", e)
        _RATES_CACHE = _RATES_CACHE or {"base": "USD", "rates": {}}
    return _RATES_CACHE


def usd_per_unit(currency: str) -> float | None:
    """Combien de vaut 1 unité de `currency` en USD. None si inconnu.
    (Compat : conservé pour les modules existants.)"""
    rates = _load_rates().get("rates", {})
    if currency == "USD":
        return 1.0
    r = rates.get(currency)
    return float(r) if r else None


def eur_per_unit(currency: str) -> float | None:
    """RÈGLE D'OR : combien d'unités de `currency` vaut 1 EUR.
    Base EUR — jamais USD comme base de conversion."""
    if currency == "EUR":
        return 1.0
    data = _load_rates()
    rates = data.get("rates", {})
    r = rates.get(currency)
    if not r:
        return EUR_RATE_FALLBACK if currency == "USD" else None
    if str(data.get("base", "EUR")).upper() == "USD":
        # fichier ancien format base USD → normalisation EUR
        eur = rates.get("EUR") or (1.0 / USD_PER_EUR_FALLBACK)
        return float(r) / float(eur)
    return float(r)


def eur_price_local(prix_eur: float, currency: str) -> float | None:
    """Prix fixe € → équivalent local indicatif (info uniquement)."""
    r = eur_per_unit(currency)
    return None if r is None else round(float(prix_eur) * r, 2)


def convert(amount: float, from_cur: str, to_cur: str) -> float | None:
    """Convertit via la base USD. None si une monnaie est inconnue."""
    from_cur, to_cur = from_cur.upper(), to_cur.upper()
    if from_cur == to_cur:
        return float(amount)
    f = usd_per_unit(from_cur)
    t = usd_per_unit(to_cur)
    if not f or not t:
        return None
    return round(amount / f * t, 2)


# ---------------------------------------------------------------------------
# API principale (spécification Feature #1)
# ---------------------------------------------------------------------------

def convert_devis(pays_code: str, prix_base: float, base_currency: str = BASE_CURRENCY) -> dict:
    """Convertit un prix vers la monnaie du pays du client.

    Retour : {country, country_code, currency, symbol, price_local,
              price_base, base_currency, fallback_usd}
    Fallback USD si le pays ou la monnaie n'est pas dans rates.json."""
    prix_eur = round(float(prix_base), 2)
    cc = (pays_code or "").strip().upper()[:2]
    info = _load_currencies().get(cc)
    if not info:
        # RÈGLE D'OR (lettre finale) : pays inconnu → PRIX FIXE €
        # affiché tel quel. Jamais de fallback USD comme prix.
        return {
            "country": (pays_code or "").strip() or "—", "country_code": cc or "--",
            "currency": "EUR", "symbol": "€",
            "price_local": prix_eur,
            "price_base": prix_eur,
            "base_currency": "EUR", "fallback_eur": True, "fallback_usd": False,
        }
    currency, country = info["currency"], info["name"]

    # conversion via base EUR (info locale uniquement)
    if base_currency.upper() != "EUR":
        # on ramène toujours le prix de vente en € fixe international
        to_eur = eur_per_unit(base_currency.upper())
        prix_eur = round(float(prix_base) / to_eur, 2) if to_eur else prix_eur
    price_local = eur_price_local(prix_eur, currency)
    fallback_eur = False
    if price_local is None:
        # monnaie hors rates.json → on reste en € fixe (info absente)
        currency, price_local = "EUR", prix_eur
        fallback_eur = True

    sym = info.get("symbol", "€") if currency != "EUR" else "€"
    return {
        "country": country,
        "country_code": cc or "--",
        "currency": currency,
        "symbol": sym,
        "price_local": price_local,
        "price_base": prix_eur,
        "base_currency": "EUR",
        "fallback_eur": fallback_eur,
        "fallback_usd": False,
    }


def format_price(conv: dict) -> str:
    """« 26 500 FG » ou « 45,50 € » — format lisible pour le client."""
    v = conv["price_local"]
    # monnaies à gros chiffres (GNF, XOF…) → pas de décimales
    if v == int(v) or v >= 1000:
        s = f"{int(round(v)):,}".replace(",", " ")
    else:
        s = f"{v:,.2f}".replace(",", " ")
    return f"{s} {conv['symbol']}"


# ---------------------------------------------------------------------------
# Détection de la localité (priorités de la spec)
# ---------------------------------------------------------------------------

# Préfixes téléphoniques (E.164) → pays. Priorité 1a.
PHONE_PREFIXES: list[tuple[str, str]] = [
    ("224", "GN"), ("223", "ML"), ("225", "CI"), ("226", "BF"), ("227", "NE"),
    ("228", "TG"), ("229", "BJ"), ("221", "SN"), ("222", "MR"), ("212", "MA"),
    ("213", "DZ"), ("216", "TN"), ("218", "LY"), ("220", "GM"), ("233", "GH"),
    ("234", "NG"), ("235", "TD"), ("236", "CF"), ("237", "CM"), ("238", "CV"),
    ("240", "GQ"), ("241", "GA"), ("242", "CG"), ("243", "CD"), ("244", "AO"),
    ("245", "GW"), ("248", "SC"), ("249", "SD"), ("250", "RW"), ("251", "ET"),
    ("252", "SO"), ("253", "DJ"), ("254", "KE"), ("255", "TZ"), ("256", "UG"),
    ("257", "BI"), ("258", "MZ"), ("260", "ZM"), ("261", "MG"), ("262", "RE"),
    ("263", "ZW"), ("264", "NA"), ("265", "MW"), ("266", "LS"), ("267", "BW"),
    ("268", "SZ"), ("269", "KM"), ("291", "ER"), ("20", "EG"), ("27", "ZA"),
    ("1", "US"), ("44", "GB"), ("33", "FR"), ("49", "DE"), ("34", "ES"),
    ("39", "IT"), ("351", "PT"), ("32", "BE"), ("41", "CH"), ("31", "NL"),
    ("966", "SA"), ("971", "AE"), ("90", "TR"), ("91", "IN"), ("86", "CN"),
    ("81", "JP"), ("82", "KR"), ("61", "AU"), ("64", "NZ"), ("55", "BR"),
    ("52", "MX"), ("7", "RU"), ("380", "UA"), ("48", "PL"),
]


def detect_country_from_phone(phone: str) -> str | None:
    """+224… → GN. Priorité 1a de la spec."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("0")
    for prefix, cc in sorted(PHONE_PREFIXES, key=lambda p: -len(p[0])):
        if digits.startswith("+" + prefix):
            return cc
    return None


# Priorité 1b : language_code du client (Telegram) → pays par défaut du marché
LANG_TO_COUNTRY = {
    "fr": "GN",   # marché principal de la marque
    "en": "US",
    "es": "ES",
    "ar": "MA",
    "pt": "GW",
}

# Noms courants → code pays (le client peut répondre « Guinée », « Sénégal »…)
COUNTRY_NAMES = {
    "guinee": "GN", "guinée": "GN", "conakry": "GN", "gn": "GN",
    "senegal": "SN", "sénégal": "SN", "sn": "SN", "dakar": "SN",
    "mali": "ML", "ml": "ML", "cote d ivoire": "CI", "ivoire": "CI",
    "ci": "CI", "abidjan": "CI", "burkina": "BF", "bf": "BF",
    "niger": "NE", "ne": "NE", "togo": "TG", "tg": "TG", "benin": "BJ",
    "bj": "BJ", "gambie": "GM", "gm": "GM", "ghana": "GH", "gh": "GH",
    "nigeria": "NG", "ng": "NG", "mauritanie": "MR", "mr": "MR",
    "maroc": "MA", "morocco": "MA", "ma": "MA", "algerie": "DZ",
    "algérie": "DZ", "dz": "DZ", "tunisie": "TN", "tn": "TN",
    "libye": "LY", "ly": "LY", "egypte": "EG", "égypte": "EG", "eg": "EG",
    "cameroun": "CM", "cm": "CM", "tchad": "TD", "td": "TD",
    "gabon": "GA", "ga": "GA", "congo": "CG", "rd congo": "CD",
    "kinshasa": "CD", "rwanda": "RW", "rw": "RW", "kenya": "KE",
    "ke": "KE", "tanzanie": "TZ", "tz": "TZ", "ouganda": "UG",
    "ug": "UG", "ethiopie": "ET", "éthiopie": "ET", "et": "ET",
    "soudan": "SD", "sd": "SD", "somalie": "SO", "so": "SO",
    "zambie": "ZM", "zm": "ZM", "zimbabwe": "ZW", "zw": "ZW",
    "afrique du sud": "ZA", "za": "ZA", "madagascar": "MG", "mg": "MG",
    "france": "FR", "fr": "FR", "paris": "FR", "belgique": "BE",
    "be": "BE", "suisse": "CH", "ch": "CH", "canada": "CA", "ca": "CA",
    "usa": "US", "us": "US", "united states": "US", "america": "US",
    "states": "US", "espagne": "ES", "spain": "ES", "portugal": "PT",
    "pt": "PT", "allemagne": "DE", "de": "DE", "italie": "IT", "it": "IT",
    "royaume uni": "GB", "uk": "GB", "gb": "GB", "london": "GB",
    "angleterre": "GB", "arabie saoudite": "SA", "sa": "SA",
    "emirats": "AE", "émirats": "AE", "ae": "AE", "dubai": "AE",
    "turquie": "TR", "tr": "TR", "inde": "IN", "india": "IN", "in": "IN",
    "chine": "CN", "china": "CN", "cn": "CN", "japon": "JP", "jp": "JP",
    "bresil": "BR", "brésil": "BR", "br": "BR", "mexique": "MX", "mx": "MX",
    "russie": "RU", "ru": "RU", "pologne": "PL", "pl": "PL",
}


def detect_country(text: str) -> str | None:
    """« GN », « Guinée », « je suis au Sénégal » → code pays."""
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.search(r"\b([A-Za-z]{2})\b", t)
    if m and m.group(1).upper() in _load_currencies():
        return m.group(1).upper()
    for name, cc in sorted(COUNTRY_NAMES.items(), key=lambda kv: -len(kv[0])):
        if name in t:
            return cc
    return None


def detect_locality(phone: str | None, lang_code: str) -> tuple[str | None, str]:
    """Priorités spec : (1a) n° tél → (1b) language_code → (2) demander.

    Retour (pays_code | None, source) avec source ∈
    {'phone','lang','ask'}."""
    cc = detect_country_from_phone(phone)
    if cc:
        return cc, "phone"
    if lang_code:
        base = lang_code.split("-")[0].lower()
        if base in LANG_TO_COUNTRY:
            return LANG_TO_COUNTRY[base], "lang"
    return None, "ask"


# ---------------------------------------------------------------------------
# Messages client multilingues — ton Aya, adresse « boss / Chef » (règle 3)
# ---------------------------------------------------------------------------
COUNTRY_ASK = {
    "fr": "Et dernier détail Chef 🙌 Dans quel pays es-tu ? (ex : GN, SN, FR, US)",
    "en": "Last detail boss 🙌 Which country are you in? (e.g. GN, SN, FR, US)",
    "es": "Último detalle jefe 🙌 ¿En qué país está? (ej.: GN, SN, FR, US)",
    "ar": "تفصيل أخير زعيم 🙌 في أي بلد أنت؟ (مثلاً: GN, SN, FR, US)",
}
NAME_ASK = {
    "fr": "Comment t'appelles-tu boss ? 😊",
    "en": "What's your name boss? 😊",
    "es": "¿Cómo se llama jefe? 😊",
    "ar": "ما اسمك زعيم؟ 😊",
}
CONVERT_LINE = {
    "fr": "💱 Chez toi : ~{price_local} ({country}, taux indicatif du jour)\n"
          "*Seul le prix en € fait foi*",
    "en": "💱 At home: ~{price_local} ({country}, indicative rate today)\n"
          "*Only the € price applies*",
    "es": "💱 En tu país: ~{price_local} ({country}, tasa indicativa de hoy)\n"
          "*Solo el precio en € hace fe*",
    "ar": "💱 عندك: ~{price_local} ({country}، سعر إرشادي لليوم)\n"
          "*السعر باليورو فقط هو المعتمد*",
}
PAID_HINT = {
    "fr": "Tape 'payer' pour les modalités de paiement ou *JE COMMENCE* au "
          "{whatsapp} 🚀",
    "en": "Type 'pay' for payment details or *I START* to {whatsapp} 🚀",
    "es": "Escriba 'pagar' para los detalles de pago o *EMPIEZO* al "
          "{whatsapp} 🚀",
    "ar": "اكتب 'دفع' لتفاصيل الدفع أو *أبدأ* إلى {whatsapp} 🚀",
}


def local_info_line(pays_code: str, prix_eur: float, lang: str) -> str:
    """Ligne « Chez toi : ~X DEV » — info locale uniquement.
    Vide si le client est en zone € (pas de conversion inutile :
    « Client France : 100€ »)."""
    conv = convert_devis(pays_code, prix_eur)
    if conv["currency"] == "EUR":
        return ""
    return t(lang, "convert_line", country=conv["country"],
             price_local=format_price(conv))


def t(lang: str, key: str, **kw) -> str:
    """Traduction locale — repli FR (langue de la marque)."""
    tables = {"country_ask": COUNTRY_ASK, "name_ask": NAME_ASK,
              "convert_line": CONVERT_LINE, "paid_hint": PAID_HINT}
    table = tables.get(key)
    if not table:
        return ""
    txt = table.get(lang) or table["fr"]
    return txt.format(**kw) if kw else txt
