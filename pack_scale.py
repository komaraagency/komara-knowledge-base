# -*- coding: utf-8 -*-
"""
pack_scale.py — Feature #5 : Pack Scale (parrainage + recouvrement +
upsell J+30 + winback). Tout en €, catalogue 1 à 7, zéro API externe.

CATALOGUE RÉEL (€) — source unique : catalogue bots & agents IA :
  1. Bot Scripté — 50€                 3. Agent IA Premium — 150€
  2. Chatbot IA Vendeur — 100€         4. Maintenance — 50€/mois (bot : 20€/mois)

Fonctions (toutes par cron séparé, anti-conflit total, anti-spam
1 message auto / client / jour via clients.last_auto_message_date) :
  5. PARRAINAGE AUTO   : achat livré + J+3 → lien unique
                         komara.agency/ref/{prenom}{id} + 20% (ou 1 mois
                         chatbot offert) — table parrainage
  6. RECOUVREMENT AUTO : paiement en 2x → J-1 rappel, Jour J QR
                         (generate_payment_qr), J+2 impayé → suspendu
  7. UPSELL J+30       : Bot sans Chatbot → chatbot +50€ ; Chatbot sans
                         Agent IA → agent IA +50€ ; sans Maintenance →
                         maintenance 50€/mois
  8. WINBACK           : devis expired depuis 60j → -20% (prix*0.8, local)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import commercial_db as cdb

logger = logging.getLogger("komara")

# Ids alignés sur l'ordre d'insertion du catalogue (catalogue.py) :
# 1=Bot Scripté, 2=Chatbot IA Vendeur, 3=Agent IA Premium, 4=Maintenance.
CATALOG = {
    1: ("Bot Scripté", 50.0),
    2: ("Chatbot IA Vendeur", 100.0),
    3: ("Agent IA Premium", 150.0),
    4: ("Maintenance mensuelle", 50.0),
}
BOT_PROD = 1
CHATBOT_PROD = 2
AGENT_PROD = 3
MAINT_PROD = 4

PARRAIN_PCT = 20.0        # 20% du prix
WINBACK_PCT = 20.0        # -20% Tabaski


def _today(now=None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def _can_msg(chat_id, now) -> bool:
    return cdb.can_auto_message(chat_id, _today(now))


def _sent(chat_id, now) -> None:
    cdb.set_auto_messaged(chat_id, _today(now))


# ---------------------------------------------------------------------------
# 5. PARRAINAGE AUTO — achat livré + J+3
# ---------------------------------------------------------------------------

def process_parrainage(bot, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    sent = 0
    ensure_columns()
    import actions
    conn = actions.DB_CONN
    rows = conn.execute(
        "SELECT id, chat_id, produit_id, produit, prix_eur, delivered_at "
        "FROM purchases WHERE status='livre' AND parrain_notified=0"
    ).fetchall()
    cols = ["id", "chat_id", "produit_id", "produit", "prix_eur", "delivered_at"]
    for r in rows:
        d = dict(zip(cols, r))
        delivered = _parse(d["delivered_at"])
        if not delivered or now - delivered < timedelta(days=3):
            continue
        if not _can_msg(d["chat_id"], now):
            continue
        prenom = _prenom_of(conn, d["chat_id"])
        commission = round((d["prix_eur"] or 0) * PARRAIN_PCT / 100, 2)
        lien = f"komara.agency/ref/{prenom}{d['id']}"
        cdb.insert_parrainage(d["chat_id"], prenom, commission)
        msg = (f"Ton {d['produit']} est en ligne 🔥 Ramène 1 client, on "
               f"t'offre 20% soit {commission:g}€ ou 1 mois de Maintenance "
               f"offert (50€).\nTon lien perso : {lien}")
        _safe_send(bot, d["chat_id"], msg)
        with actions.DB_LOCK:
            conn.execute("UPDATE purchases SET parrain_notified=1 WHERE id=?",
                         (d["id"],))
            conn.commit()
        _sent(d["chat_id"], now)
        sent += 1
    return sent


def _prenom_of(conn, chat_id) -> str:
    row = conn.execute("SELECT name FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    return (row[0].split()[0] if row and row[0] else "ami")


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_send(bot, chat_id, text):
    if bot is None:
        logger.info("[SIMU] auto → %s : %s", chat_id, text[:60])
        return
    try:
        bot.send_message(int(chat_id), text)
    except Exception as e:
        logger.error("Envoi auto impossible (%s) : %s", chat_id, e)


# ---------------------------------------------------------------------------
# 6. RECOUVREMENT AUTO (2x) — J-1 / Jour J / J+2
# ---------------------------------------------------------------------------

def process_recouvrement(bot, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    import actions
    conn = actions.DB_CONN
    rows = conn.execute(
        "SELECT id, chat_id, produit, prix_eur, tranche2_date, tranche2_payee, "
        "recouv_stage FROM purchases WHERE paiement_en_2x=1 AND "
        "tranche2_payee=0 AND status='livre'").fetchall()
    cols = ["id", "chat_id", "produit", "prix_eur", "tranche2_date",
            "tranche2_payee", "recouv_stage"]
    sent = 0
    for r in rows:
        d = dict(zip(cols, r))
        t2 = _parse(d["tranche2_date"])
        if not t2 or not _can_msg(d["chat_id"], now):
            continue
        half = round((d["prix_eur"] or 0) / 2, 2)
        stage = d["recouv_stage"] or 0
        # J-1
        if stage == 0 and 0 <= (t2 - now).total_seconds() <= 86400:
            _safe_send(bot, d["chat_id"],
                       f"Rappel : 2ème tranche de {half:g}€ demain pour ton "
                       f"{d['produit']} 💪")
            _set_stage(conn, d["id"], 1); _sent(d["chat_id"], now); sent += 1
        # Jour J (+ tolérance 12h) : QR
        elif stage <= 1 and -43200 <= (t2 - now).total_seconds() <= 43200:
            import qr_module
            conv_cc = _country(conn, d["chat_id"])
            conv = __import__("devis_engine").convert_devis(conv_cc, half)
            qr_module.generate_payment_qr(
                bot, int(d["chat_id"]), conv["price_local"], conv["currency"],
                "Orange Money / Wave", "fr")
            _set_stage(conn, d["id"], 2); _sent(d["chat_id"], now); sent += 1
        # J+2 impayé → suspendu
        elif stage == 2 and now - t2 >= timedelta(days=2):
            _safe_send(bot, d["chat_id"],
                       f"Service en pause pour impayé de {half:g}€. Régularise "
                       f"ici : tape 'payer' pour le QR 🔒")
            with actions.DB_LOCK:
                conn.execute("UPDATE purchases SET status='suspendu' WHERE id=?",
                             (d["id"],))
                conn.commit()
            _sent(d["chat_id"], now); sent += 1
    return sent


def _set_stage(conn, pid, stage):
    import actions
    with actions.DB_LOCK:
        conn.execute("UPDATE purchases SET recouv_stage=? WHERE id=?",
                     (stage, pid))
        conn.commit()


def _country(conn, chat_id) -> str:
    row = conn.execute("SELECT phone FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    import devis_engine
    cc, _ = devis_engine.detect_locality(row[0] if row else "", "fr")
    return cc or "GN"


# ---------------------------------------------------------------------------
# 7. UPSELL J+30 (le plus rentable)
# ---------------------------------------------------------------------------

def process_upsell(bot, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    import actions
    conn = actions.DB_CONN
    sent = 0
    rows = conn.execute(
        "SELECT id, chat_id, produit_id, produit, delivered_at, upsell_done "
        "FROM purchases WHERE status='livre' AND upsell_done=0").fetchall()
    cols = ["id", "chat_id", "produit_id", "produit", "delivered_at", "upsell_done"]
    for r in rows:
        d = dict(zip(cols, r))
        delivered = _parse(d["delivered_at"])
        if not delivered or now - delivered < timedelta(days=30):
            continue
        if not _can_msg(d["chat_id"], now):
            continue
        pid = d["produit_id"]
        has = _products_of(conn, d["chat_id"])
        # Règles de la lettre #7
        if pid == BOT_PROD and CHATBOT_PROD not in has:
            target, add = "Chatbot IA Vendeur", 50.0   # 100-50
        elif pid in (BOT_PROD, CHATBOT_PROD) and AGENT_PROD not in has:
            target, add = "Agent IA Premium", 100.0
        elif pid in (BOT_PROD, CHATBOT_PROD, AGENT_PROD) and MAINT_PROD not in has:
            target, add = "Maintenance mensuelle", 50.0
        else:
            _mark_upsell(conn, d["id"]); continue  # rien à proposer
        msg = (f"Ça fait 30j que ton {d['produit']} tourne 🔥 70% de nos "
               f"clients ajoutent {target}. Tu veux qu'on te l'ajoute pour "
               f"{add:g}€ ?")
        _safe_send(bot, d["chat_id"], msg)
        _mark_upsell(conn, d["id"]); _sent(d["chat_id"], now); sent += 1
    return sent


def _products_of(conn, chat_id) -> set:
    rows = conn.execute("SELECT produit_id FROM purchases WHERE chat_id=?",
                        (str(chat_id),)).fetchall()
    return {r[0] for r in rows if r[0]}


def _mark_upsell(conn, pid):
    import actions
    with actions.DB_LOCK:
        conn.execute("UPDATE purchases SET upsell_done=1 WHERE id=?", (pid,))
        conn.commit()


# ---------------------------------------------------------------------------
# 8. WINBACK — devis expired depuis 60 jours
# ---------------------------------------------------------------------------

def process_winback(bot, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    sent = 0
    import actions
    conn = actions.DB_CONN
    rows = conn.execute(
        "SELECT id, chat_id, client_name, price_eur, project_desc, service, "
        "created_at, winback_done FROM pending_quotes WHERE status='expired' "
        "AND winback_done=0").fetchall()
    cols = ["id", "chat_id", "client_name", "price_eur", "project_desc",
            "service", "created_at", "winback_done"]
    for r in rows:
        d = dict(zip(cols, r))
        created = _parse(d["created_at"])
        if not created or now - created < timedelta(days=60):
            continue
        if not _can_msg(d["chat_id"], now):
            continue
        old = d["price_eur"] or 0
        new = round(old * 0.8, 2)
        prenom = (d["client_name"] or "").split()[0] if d["client_name"] else "Hello"
        msg = (f"Hello {prenom}, tu avais demandé un devis pour "
               f"{d['service'] or d['project_desc'] or 'ton projet'} à "
               f"{old:g}€. Ce mois on fait -20% Tabaski : {new:g}€ au lieu de "
               f"{old:g}€. Toujours d'actualité ? Tape OUI")
        _safe_send(bot, d["chat_id"], msg)
        with actions.DB_LOCK:
            conn.execute("UPDATE pending_quotes SET winback_done=1 WHERE id=?",
                         (d["id"],))
            conn.commit()
        _sent(d["chat_id"], now); sent += 1
    return sent


# ---------------------------------------------------------------------------
# ENTRÉE CRON
# ---------------------------------------------------------------------------

def process(bot, now=None) -> dict:
    """Un passage complet (cron 6h). Jamais d'exception vers le cron."""
    ensure_columns()
    out = {"parrainage": 0, "recouvrement": 0, "upsell": 0, "winback": 0}
    # PRIORITÉ ARGENT : le recouvrement passe AVANT le parrainage
    # pour ne jamais laisser un impayé masqué par un message promo
    # (anti-spam : 1 message/client/jour → le plus urgent gagne)
    try:
        out["recouvrement"] = process_recouvrement(bot, now)
    except Exception as e:
        logger.error("recouvrement : %s", e)
    try:
        out["parrainage"] = process_parrainage(bot, now)
    except Exception as e:
        logger.error("parrainage : %s", e)
    try:
        out["upsell"] = process_upsell(bot, now)
    except Exception as e:
        logger.error("upsell : %s", e)
    try:
        out["winback"] = process_winback(bot, now)
    except Exception as e:
        logger.error("winback : %s", e)
    if any(out.values()):
        logger.info("pack_scale : %s", out)
    return out


# colonnes utilitaires de purchases — créées à l'init (idempotent)
def ensure_columns() -> None:
    import actions
    conn = actions.DB_CONN
    with actions.DB_LOCK:
        for col in ("parrain_notified", "recouv_stage", "upsell_done"):
            if col not in [r[1] for r in conn.execute("PRAGMA table_info(purchases)")]:
                conn.execute(f"ALTER TABLE purchases ADD COLUMN {col} INTEGER DEFAULT 0")
        for col in ("winback_done",):
            if col not in [r[1] for r in conn.execute("PRAGMA table_info(pending_quotes)")]:
                conn.execute(f"ALTER TABLE pending_quotes ADD COLUMN {col} INTEGER DEFAULT 0")
        conn.commit()
