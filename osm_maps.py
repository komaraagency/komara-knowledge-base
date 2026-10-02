# ---------------------------------------------------------------------------
# osm_maps.py — Géolocalisation et frais de livraison via OpenStreetMap
# (Nominatim). 100% gratuit, aucune clé API.
# Déclencheurs : "livraison <adresse>" en texte, ou position partagée.
# ---------------------------------------------------------------------------

import math
import threading
import time
import urllib.parse

import requests

CONAKRY = (9.6412, -13.5784)  # (lat, lon)
UA = "KomaraBot/2.0 (contact: komara-agency.onrender.com)"

_last_call = 0.0
_cache: dict = {}
_lock = threading.Lock()

TEXTS = {
    "fr": {
        "found": "📍 {place}\n\nDistance depuis Conakry : **{km} km**\nZone : {zone}\nFrais estimés : {fee}\nDélai indicatif : {delay}\n\nConfirme ton adresse exacte pour valider la livraison.",
        "notfound": "❌ Adresse introuvable sur la carte. Reformule (ex. 'livraison Kaloum, Conakry').",
        "error": "⚠️ Service de carte momentanément indisponible. Réessaie dans un instant.",
    },
    "en": {
        "found": "📍 {place}\n\nDistance from Conakry: **{km} km**\nZone: {zone}\nEstimated fees: {fee}\nIndicative delay: {delay}\n\nConfirm your exact address to validate delivery.",
        "notfound": "❌ Address not found on the map. Try again (e.g. 'delivery Kaloum, Conakry').",
        "error": "⚠️ Map service temporarily unavailable. Try again shortly.",
    },
    "es": {
        "found": "📍 {place}\n\nDistancia desde Conakry: **{km} km**\nZona: {zone}\nTarifas estimadas: {fee}\nPlazo indicativo: {delay}\n\nConfirma tu dirección exacta para validar la entrega.",
        "notfound": "❌ Dirección no encontrada en el mapa. Reinténtalo (ej. 'entrega Kaloum, Conakry').",
        "error": "⚠️ Servicio de mapas no disponible. Inténtalo de nuevo.",
    },
    "ar": {
        "found": "📍 {place}\n\nالمسافة من كوناكري: **{km} كم**\nالمنطقة: {zone}\nالتكلفة التقديرية: {fee}\nالمدة التقريبية: {delay}\n\nأكد عنوانك بدقة لاعتماد التوصيل.",
        "notfound": "❌ العنوان غير موجود على الخريطة. أعد المحاولة.",
        "error": "⚠️ خدمة الخرائط غير متوفرة حاليا. حاول لاحقا.",
    },
}


def t(lang: str, key: str, **kw) -> str:
    base = TEXTS.get(lang, TEXTS["fr"])
    return base[key].format(**kw)


def _zone(km: float) -> tuple:
    if km <= 10:
        return ("Zone Conakry", "~30 000 GNF", "1 à 2 jours")
    if km <= 50:
        return ("Zone périphérique (Kindia, Dubréka…)", "~60 000 GNF", "2 à 3 jours")
    if km <= 300:
        return ("Zone régionale (Fria, Boké, Mamou…)", "~120 000 GNF", "3 à 5 jours")
    return ("Hors zone", "sur devis", "à convenir")


def _haversine(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dh = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(dh))


def geocode(query: str) -> tuple:
    """(lat, lon, nom_affiché) ou None. Politique Nominatim respectée."""
    global _last_call
    q = query.strip()
    if not q:
        return None
    with _lock:
        if q.lower() in _cache:
            return _cache[q.lower()]
    with _lock:
        wait = 1.1 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 1, "accept-language": "fr"},
            headers={"User-Agent": UA},
            timeout=20,
        )
        data = resp.json() if resp.status_code == 200 else []
    except requests.RequestException:
        return None
    if not data:
        return None
    hit = (float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", q)[:120])
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[q.lower()] = hit
    return hit


def handle_text(bot, chat_id: int, address: str, lang: str = "fr") -> bool:
    """'livraison <adresse>' → infos de livraison. Retourne True si consommé."""
    hit = geocode(address)
    if hit is None:
        bot.send_message(chat_id, t(lang, "notfound"))
        return True
    lat, lon, name = hit
    km = _haversine(CONAKRY, (lat, lon))
    zone, fee, delay = _zone(km)
    bot.send_message(
        chat_id,
        t(lang, "found", place=name, km=f"{km:.0f}", zone=zone, fee=fee, delay=delay),
        parse_mode="Markdown",
    )
    return True


def handle_location(bot, chat_id: int, lat: float, lon: float, lang: str = "fr") -> None:
    """Position Telegram partagée → infos de livraison."""
    km = _haversine(CONAKRY, (lat, lon))
    zone, fee, delay = _zone(km)
    bot.send_message(
        chat_id,
        t(lang, "found", place=f"position partagée ({lat:.3f}, {lon:.3f})",
          km=f"{km:.0f}", zone=zone, fee=fee, delay=delay),
        parse_mode="Markdown",
    )


# Mots-clés de déclenchement multilingues
PREFIXES = ("livraison", "delivery", "entrega", "توصيل")


def is_delivery_intent(text: str) -> tuple:
    """('livraison xxx') → adresse, sinon None."""
    low = (text or "").strip().lower()
    for p in PREFIXES:
        if low.startswith(p + " ") or low.startswith("/" + p):
            addr = low.split(None, 1)
            if len(addr) > 1 and addr[1].strip():
                return addr[1].strip()
    return None
