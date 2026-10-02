# -*- coding: utf-8 -*-
"""
cart_nudge.py — Relance panier abandonné (lettre finale, Partie 2.4).

Règle : le client a vu le tunnel paiement (panier / devis / checkout)
mais n'a pas payé → relance unique après 10 minutes :
« Toujours ok pour bloquer ta place à {price}€ ? Je te la garde
jusqu'à 20h. »

• Table SQLite `cart_watch` (ACTIONS_DIR) : chat_id, seen_at, lang,
  reminded. Une SEULE relance par panier vu.
• Le message mène avec le PRIX FIXE € (règle d'or international),
  jamais un montant local comme prix.
• Anti-spam maison respecté (clients.last_auto_message_date).
• Thread 1 minute lancé par commercial_cron.start_all(bot).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("komara")

NUDGE_DELAY = timedelta(minutes=10)   # lettre : « après 10 min »
LOOP_SECONDS = 60                      # vérification chaque minute
KEEP_UNTIL_HOUR = 20                    # « je te la garde jusqu'à 20h »

DB_LOCK = threading.RLock()
DB_CONN: sqlite3.Connection | None = None

NUDGE_MSG = {
    "fr": "🛒 Toujours ok pour bloquer ta place à *{price}€* Chef ? "
          "Je te la garde jusqu'à 20h 🔥",
    "en": "🛒 Still ok to secure your spot at *{price}€* boss? "
          "I'll keep it for you until 8 pm 🔥",
    "es": "🛒 ¿Todavía ok para reservar su lugar a *{price}€* jefe? "
          "Se lo guardo hasta las 20h 🔥",
    "ar": "🛒 ما زلت موافقاً على حجز مكانك بسعر *{price}€* يا زعيم؟ "
          "أحفظه لك حتى الساعة 8 مساءً 🔥",
}


def init_db() -> None:
    global DB_CONN
    import actions
    DB_CONN = actions.DB_CONN
    with DB_LOCK:
        DB_CONN.execute(
            "CREATE TABLE IF NOT EXISTS cart_watch ("
            " chat_id TEXT PRIMARY KEY,"
            " seen_at TEXT NOT NULL,"
            " lang TEXT DEFAULT 'fr',"
            " reminded INTEGER DEFAULT 0,"
            " total_eur REAL DEFAULT 0)"
        )
        DB_CONN.commit()


def mark_seen(chat_id: int, lang: str = "fr", total_eur: float = 0) -> None:
    """À appeler quand le client VOIT le tunnel paiement avec un panier."""
    if DB_CONN is None:
        init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT INTO cart_watch (chat_id, seen_at, lang, reminded, total_eur) "
            "VALUES (?,?,?,0,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "seen_at=excluded.seen_at, lang=excluded.lang, "
            "total_eur=excluded.total_eur, reminded=0",
            (str(chat_id), now, lang, float(total_eur)),
        )
        DB_CONN.commit()


def _clear(chat_id: str) -> None:
    with DB_LOCK:
        DB_CONN.execute("DELETE FROM cart_watch WHERE chat_id=?", (chat_id,))
        DB_CONN.commit()


def process_nudges(bot, now: datetime | None = None) -> int:
    """Envoie les relances dues (vue > 10 min, non relancé, panier
    toujours non vide). Retour : nombre de relances envoyées."""
    import catalogue
    now = now or datetime.now(timezone.utc)
    if DB_CONN is None:
        init_db()
    sent = 0
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT chat_id, seen_at, lang, total_eur FROM cart_watch "
            "WHERE reminded = 0").fetchall()
    for chat_id, seen_at, lang, total_eur in rows:
        try:
            seen = datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
            if now - seen < NUDGE_DELAY:
                continue
            # panier toujours plein ? sinon on oublie
            if catalogue.cart_count(int(chat_id)) == 0:
                _clear(chat_id)
                continue
            total = total_eur or catalogue.cart_total(int(chat_id))
            if not total:
                _clear(chat_id)
                continue
            msg = NUDGE_MSG.get(lang, NUDGE_MSG["fr"]).format(price=f"{total:g}")
            bot.send_message(int(chat_id), msg, parse_mode="Markdown")
            with DB_LOCK:
                DB_CONN.execute(
                    "UPDATE cart_watch SET reminded=1 WHERE chat_id=?",
                    (chat_id,))
                DB_CONN.commit()
            sent += 1
            logger.info("Relance panier 10min → %s (%g€)", chat_id, total)
        except Exception as e:
            logger.error("Relance panier (%s) : %s", chat_id, e)
    return sent


def _loop(bot) -> None:
    while True:
        try:
            process_nudges(bot)
        except Exception as e:
            logger.error("Boucle relance panier : %s", e)
        time.sleep(LOOP_SECONDS)


def start(bot) -> None:
    """Thread daemon — appelé par commercial_cron.start_all(bot)."""
    init_db()
    th = threading.Thread(target=_loop, args=(bot,), daemon=True,
                          name="komara-cart-nudge")
    th.start()
    logger.info("Relance panier abandonné (10 min) démarrée")
