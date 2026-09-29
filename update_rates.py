# -*- coding: utf-8 -*-
"""
update_rates.py — mise à jour hebdomadaire de data/rates.json (Feature #1).

100 % gratuit : open.er-api.com ne demande aucune clé. Le fichier local
sert de cache : le bot ne fait JAMAIS d'appel API à chaque devis.

Utilisation :
    python update_rates.py            # met à jour data/rates.json
    cron hebdo (Railway / crontab) :  0 6 * * 1 python update_rates.py

En cas d'échec réseau, les anciens taux sont conservés (jamais de crash).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("update_rates")

RATES_FILE = Path(__file__).resolve().parent / "data" / "rates.json"
API_URL = "https://open.er-api.com/v6/latest/EUR"  # RÈGLE D'OR : base EUR (lettre finale)


def update_rates() -> bool:
    try:
        req = Request(API_URL, headers={"User-Agent": "komara-agency-bot/1.0"})
        with urlopen(req, timeout=30) as r:  # noqa: S310 (URL fixe https)
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.error("Échec mise à jour des taux (anciens taux conservés) : %s", e)
        return False

    if data.get("result") != "success" or not data.get("rates"):
        log.error("Réponse API invalide — anciens taux conservés")
        return False

    old = {}
    if RATES_FILE.exists():
        try:
            old = json.loads(RATES_FILE.read_text(encoding="utf-8"))
        except Exception:
            old = {}

    payload = {
        "base": "EUR",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "open.er-api.com (hebdo)",
        "rates": {k: float(v) for k, v in sorted(data["rates"].items())},
    }
    RATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RATES_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(payload["rates"])
    log.info("✅ %s monnaies mises à jour (base EUR, prix fixe international)", n)
    if old.get("rates"):
        log.info("Ancien snapshot : %s", old.get("updated_at", "?"))
    return True


if __name__ == "__main__":
    raise SystemExit(0 if update_rates() else 1)
