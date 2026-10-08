# -*- coding: utf-8 -*-
"""Formation « question logique » (Boss 08/10) — tunnels conversationnels.

Le Boss enseigne depuis son compte admin une chaîne logique :
    /apprends_logique <question> || <réponse avec question> || <suite si oui>

La « suite » est stockée dans l'onglet Logique du Google Sheet
(Départ = début de la réponse du bot, Suite = réponse à renvoyer quand le
client CONFIRME). Au runtime, quand le bot vient d'envoyer une réponse qui
commence par « Départ » et que le client confirme (« oui », « ok »...), la
Suite enseignée prime sur le matching générique : le tunnel avance à coup
sûr jusqu'à la prise de RDV, la vente ou le contact humain.

En parallèle, `escalation()` est la sortie de secours universelle : un
client qui confirme une question du bot ne doit JAMAIS retomber sur « je
ne connais pas ce sujet » — on le dirige vers le contact direct.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata

logger = logging.getLogger(__name__)

# En-tête de l'onglet Logique (créé à la demande par memory_sheets.ensure_tab)
TAB_HEADERS_LOGIQUE = ["Départ", "Suite", "Date", "Admin"]

# Sortie de secours : le client a confirmé mais aucune suite n'est
# enseignée et le matching ne trouve rien -> contact direct + devis/rdv.
_ESCALATION = {
    "fr": (
        "Parfait, on passe en direct bro 🚀\n\n"
        "📲 WhatsApp Direct N-Dine : +212 701 986 219\n"
        "👉 Clique ici pour chatter : wa.me/212701986219\n\n"
        "Tu peux aussi taper 'devis' (prix express) ou 'rdv' (appel 15 min)."
    ),
    "en": (
        "Let's go direct 🚀\n\n"
        "📲 WhatsApp N-Dine: +212 701 986 219\n"
        "👉 Chat here: wa.me/212701986219\n\n"
        "You can also type 'quote' (express price) or 'rdv' (15-min call)."
    ),
    "es": (
        "Vamos en directo 🚀\n\n"
        "📲 WhatsApp de N-Dine: +212 701 986 219\n"
        "👉 Chatea aquí: wa.me/212701986219\n\n"
        "También puedes escribir 'presupuesto' o 'rdv' (llamada 15 min)."
    ),
}

_CACHE_TTL = 60.0  # secondes
_cache: list[tuple[str, str]] = []
_cache_at = 0.0


def _norm(text: str) -> str:
    """Normalisation de comparaison : minuscule, sans accent, espaces simples."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def escalation(lang: str = "fr") -> str:
    """Sortie de secours universelle (contact humain direct)."""
    return _ESCALATION.get(lang, _ESCALATION["fr"])


def _load() -> list[tuple[str, str]]:
    """Charge les chaînes de l'onglet Logique (cache 60 s)."""
    global _cache, _cache_at
    now = time.time()
    if _cache and now - _cache_at < _CACHE_TTL:
        return _cache
    try:
        import memory_sheets
        rows = memory_sheets.read_rows("Logique") or []
    except Exception:
        logger.debug("Onglet Logique illisible", exc_info=True)
        rows = []
    chains: list[tuple[str, str]] = []
    for row in rows:
        row = (list(row) + ["", ""])[:2]
        start, suite = (row[0] or "").strip(), (row[1] or "").strip()
        if start and suite:
            chains.append((start, suite))
    _cache, _cache_at = chains, now
    return chains


def followup_for(last_bot_msg: str) -> str | None:
    """Suite enseignée si le dernier message du bot commence par un Départ."""
    if not last_bot_msg:
        return None
    msg = _norm(last_bot_msg)
    for start, suite in _load():
        start_n = _norm(start)
        if start_n and msg.startswith(start_n[:40]):
            return suite
    return None


def teach(depart: str, suite: str, admin_id: int = 0) -> bool:
    """Enregistre une chaîne dans l'onglet Logique du Google Sheet."""
    global _cache_at
    depart, suite = (depart or "").strip(), (suite or "").strip()
    if not depart or not suite:
        return False
    try:
        import memory_sheets
        from datetime import datetime, timezone
        ok = memory_sheets.append_rows(
            "Logique", [[depart[:120], suite, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(admin_id)]])
    except Exception:
        logger.exception("teach(Logique) impossible")
        return False
    _cache_at = 0.0  # force le rechargement
    return ok


def list_chains() -> list[tuple[str, str]]:
    """Toutes les chaînes apprises (pour /logique)."""
    return _load()
