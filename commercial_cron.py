# -*- coding: utf-8 -*-
"""
commercial_cron.py — ordonnanceur local des features commerciales.

ANTI-CONFLIT (lettre #5) : chaque feature tourne dans SON thread avec
SON intervalle. Aucun ne touche le chat principal : ils n'écrivent que
les tables DB (pending_quotes, purchases, abonnements, parrainage) et
n'envoient que des messages PROACTIFS aux clients concernés, protégés
par l'anti-spam (1 message auto / client / jour).

Threads :
  relances     F2  toutes les 6h   (J+1/J+3/J+7)
  pack_scale   F5  toutes les 6h   (parrainage J+3, recouvrement,
                                    upsell J+30, winback 60j)
  assurance    F6  toutes les 12h  (facture mensuelle MRR + QR)
  rates        F1  1×/semaine     (update_rates.py, API gratuite)
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("komara")


def _loop(name: str, interval_s: int, fn, bot) -> None:
    def run():
        while True:
            try:
                fn(bot)
            except Exception as e:
                logger.error("cron %s : %s", name, e)
            time.sleep(interval_s)
    th = threading.Thread(target=run, name=f"cron-{name}", daemon=True)
    th.start()
    logger.info("⏰ cron %s actif (toutes les %ss)", name, interval_s)


def start_all(bot) -> dict:
    """Démarre tous les crons commerciaux. Idempotent."""
    import relances
    import update_rates
    threads = {}
    threads["relances"] = _loop("relances", 6 * 3600,
                                relances.process_due_reminders, bot)
    try:
        import pack_scale
        threads["pack_scale"] = _loop("pack_scale", 6 * 3600,
                                       pack_scale.process, bot)
    except Exception as e:
        logger.error("pack_scale indisponible : %s", e)
    try:
        import pack_patron
        threads["assurance"] = _loop("assurance", 12 * 3600,
                                      pack_patron.process_monthly, bot)
    except Exception as e:
        logger.error("pack_patron indisponible : %s", e)
    # taux de change : 1×/semaine (premier passage au boot = frais)
    threads["rates"] = _loop("rates", 7 * 24 * 3600,
                              lambda b: update_rates.update_rates(), bot)
    return threads
