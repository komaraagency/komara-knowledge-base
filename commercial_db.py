# -*- coding: utf-8 -*-
"""
commercial_db.py — socle DB des features commerciales (F2 → F6).

Réutilise la connexion SQLite d'actions.py (même fichier, même lock) :
aucune migration cassante, tout en CREATE TABLE IF NOT EXISTS + ALTER
inoffensif si la colonne existe déjà.

Tables :
  pending_quotes  (F2) devis en attente → relances J+1/J+3/J+7
  payments        (F3) reçus QR scannés / paiements confirmés
  qualification   (F4) réponses budget du tunnel de qualification
  parrainage      (F5) liens filleuls + commissions
  abonnements     (F6) assurance revenu (MRR) — mensuel €
Colonnes ajoutées :
  clients.client_step            (F4) new/qualifying/quoted/downsell_offered/
                                      bump_offered/paid/low_budget
  clients.last_auto_message_date (F5) anti-spam : 1 message auto / jour
  clients.assurance_refusee      (F6)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import actions

STEPS = ("new", "qualifying", "qualified", "quoted", "downsell_offered",
         "bump_offered", "paid", "low_budget")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> tuple[sqlite3.Connection, object]:
    return actions.DB_CONN, actions.DB_LOCK


def _add_column(conn: sqlite3.Connection, table: str, column: str,
                decl: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_commercial_db() -> None:
    conn, lock = _conn()
    with lock:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                client_name TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                country_code TEXT DEFAULT '',
                currency TEXT DEFAULT '',
                price_local REAL DEFAULT 0,
                price_eur REAL DEFAULT 0,
                project_desc TEXT DEFAULT '',
                service TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                reminder_stage INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_reminder_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT DEFAULT '',
                quote_id INTEGER,
                transaction_id TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT '',
                method TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qualification (
                chat_id TEXT PRIMARY KEY,
                budget_tier TEXT DEFAULT '',
                answer TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS parrainage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parrain_id TEXT NOT NULL,
                parrain_prenom TEXT DEFAULT '',
                filleul_id TEXT DEFAULT '',
                commission_eur REAL DEFAULT 0,
                status TEXT DEFAULT 'en_attente',
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS abonnements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                produit_id INTEGER,
                produit TEXT DEFAULT '',
                montant_mensuel_eur REAL DEFAULT 0,
                prochaine_facture_date TEXT DEFAULT '',
                status TEXT DEFAULT 'actif',
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                produit_id INTEGER,
                produit TEXT DEFAULT '',
                prix_eur REAL DEFAULT 0,
                paiement_en_2x INTEGER DEFAULT 0,
                tranche2_date TEXT DEFAULT '',
                tranche2_payee INTEGER DEFAULT 0,
                status TEXT DEFAULT 'livre',
                delivered_at TEXT,
                created_at TEXT NOT NULL
            )""")
        _add_column(conn, "clients", "client_step",
                    "TEXT DEFAULT 'new' NOT NULL")
        _add_column(conn, "clients", "last_auto_message_date", "TEXT DEFAULT ''")
        _add_column(conn, "clients", "assurance_refusee", "INTEGER DEFAULT 0")
        conn.commit()


# ---------------------------------------------------------------------------
# pending_quotes (F2)
# ---------------------------------------------------------------------------

def insert_pending_quote(chat_id, client_name, phone, country_code,
                         currency, price_local, price_eur, project_desc,
                         service) -> int:
    conn, lock = _conn()
    with lock:
        cur = conn.execute(
            """INSERT INTO pending_quotes (chat_id, client_name, phone,
               country_code, currency, price_local, price_eur, project_desc,
               service, status, reminder_stage, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'pending',0,?)""",
            (str(chat_id), client_name, phone, country_code, currency,
             float(price_local or 0), float(price_eur or 0),
             project_desc[:500], service, _now()))
        conn.commit()
        return cur.lastrowid


def pending_quotes(status: str = "pending") -> list[dict]:
    conn, _ = _conn()
    rows = conn.execute(
        "SELECT * FROM pending_quotes WHERE status=?", (status,)).fetchall()
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM pending_quotes LIMIT 1").description]
    return [dict(zip(cols, r)) for r in rows]


def update_quote(qid: int, **fields) -> None:
    conn, lock = _conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    with lock:
        conn.execute(f"UPDATE pending_quotes SET {sets} WHERE id=?",
                     (*fields.values(), qid))
        conn.commit()


def mark_quote_paid(chat_id: str) -> bool:
    """F2 : si le client répond/règle, on stoppe les relances."""
    conn, lock = _conn()
    with lock:
        cur = conn.execute(
            "UPDATE pending_quotes SET status='paid' WHERE chat_id=? AND status='pending'",
            (str(chat_id),))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# étapes commerciales (F4) — client_step
# ---------------------------------------------------------------------------

def get_step(chat_id) -> str:
    conn, _ = _conn()
    row = conn.execute("SELECT client_step FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    return row[0] if row and row[0] else "new"


def set_step(chat_id, step: str) -> None:
    if step not in STEPS:
        raise ValueError(f"step inconnu : {step}")
    conn, lock = _conn()
    with lock:
        conn.execute(
            """INSERT INTO clients (chat_id, first_seen, last_seen, client_step)
               VALUES (?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET client_step=excluded.client_step""",
            (str(chat_id), _now(), _now(), step))
        conn.commit()


def set_auto_messaged(chat_id, date: str) -> None:
    conn, lock = _conn()
    with lock:
        conn.execute("UPDATE clients SET last_auto_message_date=? WHERE chat_id=?",
                     (date, str(chat_id)))
        conn.commit()


def can_auto_message(chat_id, today: str) -> bool:
    """Anti-spam F5 : max 1 message auto par client et par jour."""
    conn, _ = _conn()
    row = conn.execute("SELECT last_auto_message_date FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    return not (row and row[0] == today)


def save_qualification(chat_id, tier: str, answer: str) -> None:
    conn, lock = _conn()
    with lock:
        conn.execute(
            """INSERT INTO qualification (chat_id, budget_tier, answer, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET budget_tier=excluded.budget_tier,
                   answer=excluded.answer, created_at=excluded.created_at""",
            (str(chat_id), tier, answer[:100], _now()))
        conn.commit()


# ---------------------------------------------------------------------------
# achats / parrainage / abonnements (F5/F6)
# ---------------------------------------------------------------------------

def insert_purchase(chat_id, produit_id: int, produit: str, prix_eur: float,
                    paiement_en_2x: bool = False) -> int:
    conn, lock = _conn()
    tranche2 = ""
    if paiement_en_2x:
        from datetime import timedelta
        tranche2 = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    with lock:
        cur = conn.execute(
            """INSERT INTO purchases (chat_id, produit_id, produit, prix_eur,
               paiement_en_2x, tranche2_date, status, delivered_at, created_at)
               VALUES (?,?,?,?,?,?, 'livre', ?, ?)""",
            (str(chat_id), produit_id, produit, prix_eur,
             1 if paiement_en_2x else 0, tranche2, _now(), _now()))
        conn.commit()
        return cur.lastrowid


def insert_parrainage(parrain_id, prenom: str, commission_eur: float) -> int:
    conn, lock = _conn()
    with lock:
        cur = conn.execute(
            """INSERT INTO parrainage (parrain_id, parrain_prenom, commission_eur,
               status, created_at) VALUES (?,?,?, 'en_attente', ?)""",
            (str(parrain_id), prenom, float(commission_eur), _now()))
        conn.commit()
        return cur.lastrowid


def insert_abonnement(chat_id, produit_id: int, produit: str,
                      montant_mensuel_eur: float) -> int:
    from datetime import timedelta
    conn, lock = _conn()
    next30 = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    with lock:
        cur = conn.execute(
            """INSERT INTO abonnements (chat_id, produit_id, produit,
               montant_mensuel_eur, prochaine_facture_date, status, created_at)
               VALUES (?,?,?,?,?, 'actif', ?)""",
            (str(chat_id), produit_id, produit, float(montant_mensuel_eur),
             next30, _now()))
        conn.commit()
        return cur.lastrowid


def insert_payment(chat_id, transaction_id: str, amount: float, currency: str,
                   method: str = "", quote_id: int | None = None) -> int:
    conn, lock = _conn()
    with lock:
        cur = conn.execute(
            """INSERT INTO payments (chat_id, quote_id, transaction_id, amount,
               currency, method, created_at) VALUES (?,?,?,?,?,?,?)""",
            (str(chat_id), quote_id, transaction_id[:200], float(amount or 0),
             currency, method, _now()))
        conn.commit()
        return cur.lastrowid


def log_revenue(event: str, chat_id: str) -> None:
    """Trace les événements de revenu dans clients.events (pour /stats)."""
    try:
        actions.upsert_client(chat_id, event=event)
    except Exception:
        pass
