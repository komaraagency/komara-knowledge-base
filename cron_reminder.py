# -*- coding: utf-8 -*-
"""
cron_reminder.py — Feature #2 : tourne toutes les 6 h (cron ou thread).

Deux modes d'exécution :
  1) crontab Railway / système :  «0 */6 * * * python cron_reminder.py»
  2) thread intégré au bot       :  start_background_cron(bot) appelé au boot
     (Railway n'offre pas de vrai cron sur tous les plans : le thread est
     le repli 100 % local, il ne fait AUCUN appel réseau sortant.)

Le thread respecte l'anti-conflit de la lettre #5 : il ne lit JAMAIS le
chat principal, il n'écrit que les tables pending_quotes / clients.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("komara")
INTERVAL_H = 6
INTERVAL_S = INTERVAL_H * 3600


def run_once(bot) -> dict:
    """Un passage complet des relances (testable sans cron)."""
    import relances
    return relances.process_due_reminders(bot)


def start_background_cron(bot) -> threading.Thread:
    def loop():
        while True:
            try:
                run_once(bot)
            except Exception as e:
                logger.error("cron_reminder : %s", e)
            time.sleep(INTERVAL_S)

    th = threading.Thread(target=loop, name="cron_reminder", daemon=True)
    th.start()
    logger.info("⏰ cron_reminder actif (toutes les %sh)", INTERVAL_H)
    return th


if __name__ == "__main__":
    # mode autonome (crontab) : bot Telegram réel
    logging.basicConfig(level=logging.INFO)
    import rag_bot  # charge TELEGRAM_TOKEN, actions.init_db, etc.
    run_once(rag_bot.bot)
