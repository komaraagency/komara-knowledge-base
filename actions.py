"""Actions exécutables du bot Komara Agency 🇬🇳 — 100% local, zéro IA externe.

7 modules d'exécution réelle (pas de bla-bla) :
  1. Formulaire de commande guidé  (/commander)
  2. Prise de RDV avec créneaux     (/rdv)
  3. Collecte de leads qualifiés    (être rappelé)
  4. Devis instantané (grille prix) (/devis)
  5. Rappels programmés J+1         (file d'attente auto)
  6. Sondage + statistiques         (/sondage, /stats)
  7. Rapport questions non reconnues (/rapport)

Stockage : SQLite local (data/actions.db). Aucune dépendance externe.
"""
from __future__ import annotations

import json
import logging
import os
import re
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import catalogue
import devis_engine
import google_link
import invoices
import osm_maps
import backup_drive
import weekly_report
from local_stats import get_unrecognized_stats

logger = logging.getLogger("komara.actions")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
ACTIONS_DIR = Path(os.getenv("ACTIONS_DIR", str(BASE_DIR / "data")))
ACTIONS_DB = ACTIONS_DIR / "actions.db"
DB_LOCK = threading.Lock()
DB_CONN: sqlite3.Connection | None = None

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
FOLLOWUP_LOOP_INTERVAL = max(30, int(os.getenv("FOLLOWUP_INTERVAL", "60")))

# Horaires de bureau (heure locale de l'agence ; Guinée = UTC+0)
# Formats acceptés: "9:30", "21:00", "9.5", "9"
# RÈGLE BOSS (02/10) : défaut = Maroc (UTC+1, horaire permanent sauf
       # Ramadan) car WORK_START/WORK_END (9h30-21h) sont pensés sur l'heure
       # du patron (Essaouira). Variable Railway TIMEZONE_OFFSET absente ->
       # fallback UTC+0 décalait "fermé/ouvert" d'1h (bug confirmé 02/10).
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "1"))


def _parse_hour(value: str, default: float) -> float:
    try:
        value = (value or "").strip()
        if ":" in value:
            hours, minutes = value.split(":", 1)
            return int(hours) + int(minutes) / 60
        return float(value)
    except (ValueError, AttributeError):
        return default


def _fmt_hour(value: float) -> str:
    hours, minutes = int(value), int(round((value - int(value)) * 60))
    return f"{hours}h{minutes:02d}" if minutes else f"{hours}h"


WORK_START = _parse_hour(os.getenv("WORK_START", "9:30"), 9.5)
WORK_END = _parse_hour(os.getenv("WORK_END", "21:00"), 21.0)
WEEKEND_OFF = os.getenv("WEEKEND_OFF", "true").lower() in {"1", "true", "yes", "on"}

# Grille de prix alignée sur kb.json (monnaies 100% €)
# Délais de référence par catégorie (affichés dans le devis instantané)
SERVICE_DELAYS: dict[str, str] = {
    "Bot": "48h",
    "Chatbot": "3-5 jours",
    "Agent IA": "5-7 jours",
    "Maintenance": "immédiate",
}

def _catalogue_grid() -> list[tuple[str, str, str, str]]:
    """Grille devis construite depuis le catalogue (source unique).
    Boucle sur N produits : si l'admin ajoute/retire un service via
    /produit, le devis suit automatiquement."""
    import catalogue
    grid = []
    for i, (pid, name, _desc, price) in enumerate(catalogue.active_products(), start=1):
        with catalogue.DB_LOCK:
            row = catalogue.DB_CONN.execute(
                "SELECT category FROM products WHERE id = ?", (pid,)).fetchone()
        delay = SERVICE_DELAYS.get(row[0] if row else "", "selon projet")
        grid.append((str(i), name, f"{price:g}€", delay))
    return grid

# Remplie dans init_db() une fois la base prête (jamais à l'import)
PRICE_GRID: list[tuple[str, str, str, str]] = []

# RÈGLE D'OR (02/10) : chaque question est traduite — un client EN/ES/AR
# ne doit PLUS recevoir la question du sondage en français.
SURVEY_QUESTIONS = [
    {"id": "satisfaction", "kind": "rate",
     "fr": "Sur 1 à 5, comment notes-tu ton expérience avec Komara Agency 🇬🇳 ?",
     "en": "From 1 to 5, how do you rate your experience with Komara Agency 🇬🇳 ?",
     "es": "Del 1 al 5, ¿cómo valoraría su experiencia con Komara Agency 🇬🇳 ?",
     "ar": "من 1 إلى 5، كيف تقيّم تجربتك مع Komara Agency 🇬🇳 ؟"},
    {"id": "reco", "kind": "bool",
     "fr": "Recommanderais-tu Komara Agency à un proche ? (oui/non)",
     "en": "Would you recommend Komara Agency to a friend? (yes/no)",
     "es": "¿Recomendaría Komara Agency a un allegado? (sí/no)",
     "ar": "هل تنصح بـ Komara Agency لشخص قريب منك؟ (نعم/لا)"},
    {"id": "comment", "kind": "text",
     "fr": "Un commentaire pour l'équipe ? (ou tape 'passer')",
     "en": "Any comment for the team? (or type 'skip')",
     "es": "¿Algún comentario para el equipo? (o escriba 'pasar')",
     "ar": "أي تعليق للفريق؟ (أو اكتب 'نم')"},
]

def _survey_q(idx: int, lang: str) -> str:
    """Texte de la question du sondage dans la langue du client (repli FR)."""
    q = SURVEY_QUESTIONS[idx]
    return q.get(lang) or q["fr"]

CANCEL_WORDS = {"annuler", "cancel", "stop", "quitter", "إلغاء", "Cancelar"}

TRIGGERS: dict[str, set[str]] = {
    "order": {
        "🚀 Commander", "🚀 Order", "🚀 طلب", "🚀 Ordenar",
        "/commander", "commander", "je veux commander", "passer commande", "passer une commande",
        "i want to order",
    },
    "rdv": {
        "📅 Rendez-vous", "📅 Book a call", "📅 Reservar", "📅 حجز موعد", "حجز موعد", "موعدي",
        "/rdv", "rdv", "rendez-vous", "prendre rendez", "réserver un appel", "réserver un rendez",
        "book a call", "reservar",
    },
    "devis": {
        "📄 Devis", "📄 Quote", "📄 Presupuesto",
        "/devis", "devis", "avoir un devis", "demander un devis", "un devis", "quote", "presupuesto",
        "📄 تسعيرة", "تسعيرة", "عرض سعر", "التسعيرة",
    },
    "lead": {
        "📞 Être rappelé", "être rappelé", "etre rappelé", "être rappelé(e)",
        "rappelez-moi", "rappel", "on m'appelle", "call me back",
    },
    "parrainage": {
        "/parrainage", "parrainage", "parrain", "programme de parrainage",
        "referral", "parraine", "je parraine",
    },
    "survey": {
        "⭐ Avis", "⭐ Feedback", "⭐ Opinión", "⭐ تقييم", "تقييمي", "قيّمني",
        "/sondage", "sondage", "donner mon avis", "laisser un avis", "mon avis",
    },
}

ADMIN_COMMANDS = {"/paiement", "/admin", "/msg", "/broadcast", "/pause", "/reprend", "/prend", "/stats", "/rapport", "/export", "/maj", "/update", "/commandes", "/orders", "/promo", "/promos", "/rdvs", "/clients", "/produit", "/produits", "/kb_import", "/kb_modele", "/modeles", "/google", "/facture", "/backup", "/hebdo", "/solde", "/ka", "/bonnus", "/apprends", "/apprendre", "/apprendres"}

GREETING_WORDS: set[str] = {
    "bonjour", "salut", "bonsoir", "coucou", "hello", "hi", "hola",
    "buenos dias", "buenas", "salam", "salam aleykoum", "أهلا", "مرحبا", "سلام",
}

NEW_RDV_WORDS: set[str] = {
    "nouveau rdv", "nouveau rendez-vous", "nouveau rendez",
    "new rdv", "new appointment", "nuevo rdv", "nueva cita",
}

OK_WORDS: set[str] = {"ok", "oui", "yes", "si", "صحيح"}

# Suivi de commande côté client
TRACKING_TRIGGERS: set[str] = {
    "/suivi", "/tracking", "/track", "/estado", "/seguimiento", "/تتبع",
    "suivi", "suivi commande", "suivi de commande", "statut", "statut commande",
    "où en est ma commande", "ou en est ma commande", "où est ma commande",
    "ma commande", "état de ma commande", "ou ça en est",
    "where is my order", "order status", "my order", "tracking",
    "dónde está mi pedido", "donde esta mi pedido", "estado de mi pedido", "mi pedido",
    "أين طلبي", "حالة طلبي",
}

ORDER_STATUSES: dict[str, str] = {
    "attente": "🕐 En attente",
    "en attente": "🕐 En attente",
    "en cours": "🔧 En cours",
    "cours": "🔧 En cours",
    "livre": "✅ Livré",
    "livré": "✅ Livré",
    "livree": "✅ Livré",
    "annule": "❌ Annulé",
    "annulé": "❌ Annulé",
    "annulee": "❌ Annulé",
}

# ---------------------------------------------------------------------------
# Textes des flux (fr complet, en/es essentiels, ar → fr)
# ---------------------------------------------------------------------------

# Variantes de confirmation (random.choice : jamais 2 fois la même phrase)

RDV_DONE_VARIANTS_FR = [
    "C'est noté {nom} ✅\nRDV pour {sujet} — {slot}.\nJe t'envoie un vocal de confirmation et un rappel la veille 🙏",
    "Parfait {nom}, on a bloqué {slot} pour {sujet} ✅\nTu recevras un rappel la veille, insh'Allah 🙏",
    "Alhamdulilah, c'est calé {nom} ✅\n{slot} pour {sujet}.\nÀ tout à l'heure ! 🔥",
]

SURVEY_ASK_VARIANTS_FR = [
    "Ton avis compte beaucoup pour nous ⭐ Tu nous mets combien : 1 à 5 ?",
    "Si tu as aimé le service, laisse-nous ta note de 1 à 5 ⭐ Ça nous aide à grandir 🙏",
]

# ── LETTRE MASTER (29/09) — ÉTAPE 1 : les messages vivent dans
# lang/{fr,en,es,ar}.json (source unique, éditables sans toucher au code).
# ÉTAPE 2 : t() = get_text(key, lang) avec repli FR automatique, exactement
# comme demandé : translations[lang].get(key) or translations["fr"].get(key).
T: dict[str, dict[str, str]] = {}
for _lg in ("fr", "en", "es", "ar"):
    _p = BASE_DIR / "lang" / f"{_lg}.json"
    with _p.open("r", encoding="utf-8") as _fh:
        T[_lg] = json.load(_fh)

def t(lang: str, key: str, **kwargs) -> str:
    """Texte du flux dans la langue (fr par défaut)."""
    text = T.get(lang, T["fr"]).get(key, T["fr"].get(key, key))
    return text.format(**kwargs) if kwargs else text


WHATSAPP_FALLBACK = os.getenv("AGENCY_WHATSAPP", "+212701986219")


# ---------------------------------------------------------------------------
# Base de données locale
# ---------------------------------------------------------------------------

def init_db() -> None:
    global DB_CONN
    ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    DB_CONN = sqlite3.connect(ACTIONS_DB, check_same_thread=False)
    with DB_LOCK:
        DB_CONN.executescript("""
            CREATE TABLE IF NOT EXISTS flows (
                chat_id TEXT PRIMARY KEY,
                flow TEXT NOT NULL,
                step TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, name TEXT, phone TEXT, sector TEXT,
                need TEXT, budget TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, service TEXT, activity TEXT, deadline TEXT,
                name TEXT, phone TEXT, status TEXT DEFAULT 'en attente',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, name TEXT, topic TEXT,
                slot TEXT, slot_iso TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, service TEXT, price TEXT,
                delay TEXT, details TEXT, code TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS survey_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, question_id TEXT, answer TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offhours_notified (
                chat_id TEXT,
                day TEXT,
                PRIMARY KEY (chat_id, day)
            );
            CREATE TABLE IF NOT EXISTS clients (
                chat_id TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                activity TEXT DEFAULT '',
                events TEXT DEFAULT '[]',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS global_promo (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                pct REAL NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount_pct REAL NOT NULL,
                active INTEGER DEFAULT 1,
                uses INTEGER DEFAULT 0,
                max_uses INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referrals (
                code TEXT PRIMARY KEY,
                owner_chat TEXT NOT NULL,
                credits INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referral_uses (
                code TEXT NOT NULL,
                new_chat TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (code, new_chat)
            );
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, message TEXT, due_at TEXT NOT NULL,
                sent INTEGER DEFAULT 0, created_at TEXT NOT NULL
            );
        """)
        # Tables catalogue + panier
        catalogue.init_catalogue_db(DB_CONN)
        global PRICE_GRID
        PRICE_GRID = _catalogue_grid()
        google_link.ensure_table(DB_CONN)
        DB_CONN.commit()
        # Migrations pour bases déjà déployées
        for migration in (
            "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'en attente'",
            "ALTER TABLE quotes ADD COLUMN code TEXT DEFAULT ''",
            "ALTER TABLE appointments ADD COLUMN slot_iso TEXT DEFAULT ''",
        ):
            try:
                DB_CONN.execute(migration)
                DB_CONN.commit()
            except sqlite3.OperationalError:
                pass  # colonne déjà présente
    logger.info("Base actions SQLite initialisée : %s", ACTIONS_DB)


    # Lot 22 (F1-F6) : tables commerciales — pending_quotes, payments,
    # parrainage, abonnements, purchases, client_step…
    import commercial_db
    commercial_db.init_commercial_db()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fetch_flow(chat_id: int) -> tuple[str, str, dict] | None:
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT flow, step, data FROM flows WHERE chat_id =?", (str(chat_id),)
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[2])
    except json.JSONDecodeError:
        data = {}
    return row[0], row[1], data


def _save_flow(chat_id: int, flow: str, step: str, data: dict) -> None:
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT OR REPLACE INTO flows (chat_id, flow, step, data, updated_at) VALUES (?,?,?,?,?)",
            (str(chat_id), flow, step, json.dumps(data, ensure_ascii=False), _now()),
        )
        DB_CONN.commit()


def _clear_flow(chat_id: int) -> None:
    with DB_LOCK:
        DB_CONN.execute("DELETE FROM flows WHERE chat_id =?", (str(chat_id),))
        DB_CONN.commit()


def _insert(table: str, fields: dict) -> int:
    cols = ", ".join(fields.keys())
    marks = ", ".join("?" for _ in fields)
    with DB_LOCK:
        cur = DB_CONN.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(fields.values())
        )
        DB_CONN.commit()
        row_id = cur.lastrowid or 0
    # Sync Google (Sheets + Contacts) — silencieuse si non liée
    if table in {"orders", "leads", "appointments"}:
        try:
            google_link.hook(table, fields)
        except Exception:
            pass
    return row_id


def _count(table: str) -> int:
    with DB_LOCK:
        row = DB_CONN.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def schedule_followup(chat_id: int, message: str, due_at: datetime) -> None:
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT INTO followups (chat_id, message, due_at, sent, created_at) VALUES (?,?,?,0,?)",
            (str(chat_id), message, due_at.isoformat(timespec="seconds"), _now()),
        )
        DB_CONN.commit()
    logger.info("Rappel programmé pour %s à %s", chat_id, due_at)


# ---------------------------------------------------------------------------
# Notifications admin (local, Telegram direct)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Promotions globales (catalogue entier) — lot 20
# ---------------------------------------------------------------------------

def get_global_promo() -> tuple[float, str | None]:
    """Pourcentage promo globale active (0 = aucune) + son label."""
    if DB_CONN is None:
        return (0.0, None)
    try:
        with DB_LOCK:
            row = DB_CONN.execute(
                "SELECT pct, label FROM global_promo WHERE id = 1").fetchone()
        return (float(row[0]), row[1] or "PROMO") if row else (0.0, None)
    except sqlite3.OperationalError:
        return (0.0, None)


def _broadcast_clients(bot, text: str) -> tuple[int, int]:
    """Envoie un message à tous les clients connus. Retourne (ok, échecs)."""
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        rows = DB_CONN.execute("SELECT chat_id, name FROM clients").fetchall()
    sent = failed = 0
    for cid, name in rows:
        try:
            bot.send_message(int(cid), text)
            sent += 1
            time.sleep(0.06)  # limite anti-flood Telegram
        except Exception:
            failed += 1
    return sent, failed


def set_global_promo(bot, pct: float, label: str) -> bool:
    """Active une promo globale sur TOUT le catalogue et notifie les clients."""
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT OR REPLACE INTO global_promo (id, pct, label, created_at) VALUES (1,?,?,?)",
            (float(pct), label, _now()),
        )
        DB_CONN.commit()
    sent, failed = _broadcast_clients(
        bot,
        f"🔥 {label} : -{pct:g}% sur TOUT le catalogue Komara Agency 🇬🇳 !\n"
        f"Tape 'catalogue' pour voir les nouveaux prix 🔥")
    bot.send_message(
        ADMIN_CHAT_ID,
        f"✅ {label} -{pct:g}% activée sur tout le catalogue.\n"
        f"clients notifiés : {sent} ({failed} échec(s))")
    return True


def clear_global_promo(bot) -> bool:
    """Arrête la promo globale (sans spammer les clients)."""
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        DB_CONN.execute("DELETE FROM global_promo WHERE id = 1")
        DB_CONN.commit()
    bot.send_message(ADMIN_CHAT_ID, "✅ Promo globale désactivée. Les prix catalogue reviennent à la normale.")
    return True


# ---------------------------------------------------------------------------
# Questions non répondues : fichier persistant + notification admin — lot 20
# ---------------------------------------------------------------------------

# RÈGLE BOSS (02/10) : plus de fichier local — compteur éphémère en RAM,
# les questions non répondues vivent dans le Google Sheet dédié.
_notified_unanswered: set[str] = set()
_UNANSWERED_COUNTS: dict[str, int] = {}

def notify_unanswered(bot, chat_id: int, text: str, lang: str) -> None:
    """Question non répondue → onglet « Questions sans réponse » du Google
    Sheet dédié + notification admin (une seule notif par question).
    RÈGLE BOSS (02/10) : AUCUN fichier local — plus d'unanswered_questions.json
    sur le disque Railway ou dans le repo."""
    q = (text or "").strip()
    if not q or q.startswith("/"):
        return
    import unicodedata as _ud
    _fold = _ud.normalize("NFKD", q.lower().replace("'", " ").replace("’", " "))
    key = " ".join("".join(ch for ch in _fold if not _ud.combining(ch)).split())
    # compteur en RAM (éphémère, juste pour la colonne Occurrences)
    count = _UNANSWERED_COUNTS.get(key, 0) + 1
    _UNANSWERED_COUNTS[key] = count
    try:
        from memory_sheets import log_unanswered
        log_unanswered(q, lang, chat_id, count)
    except Exception:
        logger.debug("Miroir question sans réponse impossible", exc_info=True)
    # notification admin — une seule fois par question (par process)
    if key not in _notified_unanswered:
        _notified_unanswered.add(key)
        notify_admin(
            bot,
            f"❓ QUESTION NON RÉPONDUE\n"
            f"« {q[:300]} »\n"
            f"🗣 langue : {lang} • 👤 chat : {chat_id}\n"
            f"→ enseigne-le : /apprends {q[:80]} || ta réponse")


# ---------------------------------------------------------------------------
# Démo Agent IA : capture + notification admin — lot 20 (ex-formation)
# ---------------------------------------------------------------------------

FORMATION_MSGS = {
    "fr": "Parfait 🔥 Pour réserver ta démo d'Agent IA (gratuite, 15 min), envoie :\n1) Ton nom\n2) Ton business (ce que tu vends)\n3) Ton numéro\n\nL'équipe te fixe un créneau et tu vois ton agent vendre en direct 🚀",
    "en": "Perfect 🔥 To book your AI Agent demo (free, 15 min), send:\n1) Your name\n2) Your business (what you sell)\n3) Your number\n\nThe team schedules a slot and you watch your agent sell live 🚀",
    "es": "Perfecto 🔥 Para reservar su demo de Agente IA (gratis, 15 min), envíe:\n1) Su nombre\n2) Su negocio (qué vende)\n3) Su número\n\nEl equipo fija un horario y ve a su agente vender en directo 🚀",
    "ar": "ممتاز 🔥 لحجز عرض توضيحي لوكيل الذكاء الاصطناعي (مجاني، 15 دقيقة)، أرسل:\n1) اسمك\n2) عملك (ماذا تبيع)\n3) رقمك\n\nيحدد الفريق موعداً وترى وكيلك يبيع مباشرة 🚀",
}
FORMATION_OK = {
    "fr": "Noté 🔥 Ta démo est réservée ! L'équipe Komara te fixe un créneau dans quelques minutes. Tu vas voir ton agent IA en action 🤖",
    "en": "Noted 🔥 Your demo is booked! The Komara team schedules your slot in a few minutes. You'll see your AI agent in action 🤖",
    "es": "Anotado 🔥 ¡Su demo está reservada! El equipo Komara le fija un horario en unos minutos. Verá su agente IA en acción 🤖",
    "ar": "تم 🔥 حجز عرضك التوضيحي! يحدد لك فريق كومارا موعداً خلال دقائق. سترى وكيل الذكاء الاصطناعي في العمل 🤖",
}

def set_pending_formation(chat_id: int) -> None:
    """Le client vient de recevoir l'invitation à s'inscrire : le prochain
    message sera capturé comme infos d'inscription."""
    _save_flow(chat_id, "formation_inscription", "infos", {})

def formation_capture(bot, chat_id: int, text: str, lang: str) -> bool:
    """Capture les infos de réservation démo et notifie l'admin
    (pour fixer un créneau de démo Agent IA)."""
    low = text.strip().lower()
    if low in {"annuler", "cancel", "stop", "إلغاء"}:
        _clear_flow(chat_id)
        if lang == "fr":
            bot.send_message(chat_id, "OK, on annule pour l'instant 😊 Tape 'démo' quand tu veux 🚀")
        else:
            bot.send_message(chat_id, "OK, cancelled for now 😊 Type 'demo' whenever you want 🚀")
        return True
    _clear_flow(chat_id)
    info = text.strip()[:600]
    uname = ""
    try:
        c = get_client(chat_id)
        if c and c.get("name"):
            uname = f" ({c['name']})"
    except Exception:
        pass
    notify_admin(
        bot,
        f"🤖 NOUVELLE DÉMO — AGENT IA\n"
        f"👤 Client{uname} (chat_id {chat_id})\n"
        f"📋 Infos : « {info} »\n"
        f"→ fixe-lui un créneau de démo 15 min 👥")
    bot.send_message(chat_id, FORMATION_OK.get(lang, FORMATION_OK["fr"]))
    return True

# ---------------------------------------------------------------------------
# Document envoyé par un CLIENT (pas l'admin) — FIX BOSS (03/10, screenshot
# "PDF analyse ne répond pas") : le bot bloquait tout document client avec
# un froid "réservé à l'admin" et la conversation mourait là. Il transmet
# maintenant le fichier à l'équipe ET relance tout de suite vers le devis
# — zéro extraction de contenu PDF (pas de lib installée, on ne promet pas
# ce qu'on ne fait pas), mais le client n'est plus jamais ignoré.
# ---------------------------------------------------------------------------

def handle_client_document(bot, chat_id: int, message, lang: str) -> None:
    doc = getattr(message, "document", None)
    filename = (doc.file_name if doc else None) or "fichier"
    client = get_client(chat_id)
    name = (client or {}).get("name") or f"chat_id {chat_id}"
    try:
        bot.forward_message(ADMIN_CHAT_ID, chat_id, message.message_id)
    except Exception as exc:
        logger.warning("Transfert document client à l'admin échoué : %s", exc)
    notify_admin(bot, f"📎 DOCUMENT CLIENT ({name}) — {filename} — ⬆️ transféré ci-dessus")
    grid = " / ".join(f"{name}" for _n, name, _p, _d in PRICE_GRID)
    bot.send_message(chat_id, t(lang, "client_doc_received", grid=grid))


def notify_admin(bot, text: str) -> None:
    """Prévient le propriétaire (ADMIN_CHAT_ID) d'un lead/commande/RDV."""
    if not ADMIN_CHAT_ID:
        logger.info("ADMIN_CHAT_ID absent — notification ignorée : %s", text[:60])
        return
    try:
        bot.send_message(ADMIN_CHAT_ID, text)
    except Exception as exc:  # admin bloqué / id inconnu
        logger.warning("Notification admin échouée : %s", exc)


def _service_by_num(num: str) -> tuple[str, str, str, str] | None:
    for n, name, price, delay in PRICE_GRID:
        if n == num:
            return n, name, price, delay
    return None


def _agency_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)


DAY_NAMES: dict[str, list[str]] = {
    "fr": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "ar": ["إثنين", "ثلاثاء", "أربعاء", "خميس", "جمعة", "سبت", "أحد"],
}


def _day_name(lang: str, weekday: int) -> str:
    return DAY_NAMES.get(lang, DAY_NAMES["fr"])[weekday]


def _gen_slots(lang: str = "fr") -> list[tuple[str, str]]:
    """6 créneaux : 3 prochains jours × 10h/15h.
    Inclut le week-end si l'agence est ouverte 7j/7 (WEEKEND_OFF=false)."""
    slots = []
    day = _agency_now()
    while len(slots) < 6:
        day += timedelta(days=1)
        if WEEKEND_OFF and day.weekday() >= 5:  # samedi/dimanche
            continue
        for hour in (10, 15):
            slot_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            connector = {"en": "at", "es": "a las", "ar": "في"}.get(lang, "à")
            label = f"{_day_name(lang, slot_dt.weekday())} {slot_dt.strftime('%d/%m')} {connector} {slot_dt.strftime('%Hh')}"
            slots.append((label, slot_dt))
    return slots


def _fmt_slots(slots: list[tuple[str, str]]) -> str:
    digits = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    return "\n".join(f"{digits[i]} {label}" for i, (label, _) in enumerate(slots))


# ---------------------------------------------------------------------------
# Point d'entrée : appelé par le handler principal AVANT la recherche KB
# ---------------------------------------------------------------------------

def handle(bot, chat_id: int, text: str, lang: str) -> bool:
    """Traite commandes et flux actifs. True = message consommé."""
    if DB_CONN is None:
        init_db()

    # Lot 21 : filet de sécurité — même si l'appelant n'a pas nettoyé les
    # caractères invisibles WhatsApp/iOS (\u200e etc.), une commande
    # collée avec ces marqueurs reste reconnue ici.
    text_clean = re.sub(r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff\u2060]", "", text or "").strip()

    # 0pré. Mémoire légère : « je vends dans une boutique » → fiche client
    try:
        _maybe_remember_activity(chat_id, text_clean)
    except Exception:
        pass

    # 0. Commandes admin (premier mot, arguments autorisés)
    low = text_clean.lower()
    words = text_clean.split()
    first_word = words[0].lower() if words else ""
    # 0quin-bis. /promo <code> côté CLIENT = vérification de code.
    # L'admin seul garde la création (/promo CODE 20) : un client qui
    # tape « /promo TABASKI20 » ne doit plus se faire refuser.
    if first_word == "/promo" and not (ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID):
        return _promo_check(bot, chat_id, text_clean, lang, first_token=True)
    if first_word in ADMIN_COMMANDS:
        args = text_clean.split(maxsplit=1)[1] if len(words) > 1 else ""
        return _admin_command(bot, chat_id, first_word, args, lang)

    # 0s. Flux Démo Agent IA : le message suivant l'invitation est
    # capturé comme infos de réservation et notifié à l'admin.
    _flow = _fetch_flow(chat_id)
    if _flow and _flow[0] == "formation_inscription" and not text_clean.startswith("/"):
        return formation_capture(bot, chat_id, text_clean, lang)

    # 0quin-quater. Réclamation / arnaque / sujet sensible : règle
    # docs/dialogues-frequents.md — aucune réponse automatique de vente,
    # l'équipe révise manuellement. On accuse réception sobrement et
    # on notifie l'admin immédiatement.
    if _is_complaint(low):
        client = get_client(chat_id)
        notify_admin(
            bot,
            "🚨 RÉCLAMATION / SUJET SENSIBLE\n"
            f"👤 {(client or {}).get('name') or '(inconnu)'} — chat_id {chat_id}\n"
            f"💬 « {text_clean[:180]} »\n"
            "→ Revue manuelle requise, NE PAS répondre en automatique.",
        )
        bot.send_message(chat_id, t(lang, "complaint_ack"))
        return True

    # 0quin-ter. « Parler à un humain » : interrompt TOUT flow actif
    # (devis, panier, commande) — le client ne doit jamais rester coincé.
    if _wants_human(low):
        _clear_flow(chat_id)
        _save_flow(chat_id, "human", "whatsapp", {})
        bot.send_message(chat_id, t(lang, "human_ask"))
        return True

    # 0quin. Code promo client : /code XXX, « code promo XXX », « promo code XXX »
    if (low in {"/code", "code", "code promo", "promo code"}
            or low.startswith("/code ") or low.startswith("code promo ")
            or low.startswith("promo code ")):
        return _promo_check(bot, chat_id, text_clean, lang)

    # 0ter. Suivi de commande (statut depuis la base locale)
    if low in TRACKING_TRIGGERS:
        return order_tracking(bot, chat_id, lang)

    # 0ter-bis. Catalogue, panier, ajouter/vider.
    # Lot 22 : UNIQUEMENT hors flux actif — un client qui répond
    # « boutique » à l'étape activité du devis ne doit pas déclencher le
    # catalogue : sa réponse alimente le flux en cours (le hijack du
    # catalogue cassait l'étape 4/5 du devis).
    # RÈGLE D'OR (02/10) : une COMMANDE slash (/catalogue, /panier...)
    # est toujours exécutée, même pendant un flux actif — c'est un geste
    # explicite du client, pas une réponse à une question du flux. Seuls
    # les déclencheurs mots (« boutique ») respectent l'exclusion du flux.
    if ((not _fetch_flow(chat_id)) or text_clean.startswith("/")) and catalogue.handle_client(bot, chat_id, text_clean, lang):
        return True

    # 0bis. Message hors horaires (1x/jour, n'interrompt rien)
    maybe_off_hours_notice(bot, chat_id, lang)

    # 1. Annulation d'un flux actif
    if low in CANCEL_WORDS and _fetch_flow(chat_id):
        _clear_flow(chat_id)
        bot.send_message(chat_id, t(lang, "cancelled"))
        return True

    # 2. Démarrage d'un flux (déclencheurs / boutons)
    for flow, words in TRIGGERS.items():
        if text_clean in words or low in words:
            # (égalité exacte ci-dessus, contenu ci-dessous)
            # Panier non vide → tunnel catalogue au lieu du flux commande
            if flow == "parrainage":
                return _parrainage_reply(bot, chat_id, lang)
            if flow == "order" and catalogue.start_checkout(bot, chat_id, lang):
                return True
            return start_flow(bot, chat_id, flow, lang, trigger_text=text_clean)

    # 2ter. LETTRE MASTER — le client parle en PHRASE : « je souhaite un
    # devis », « je veux commander un chatbot ». Les déclencheurs devis/order
    # sont recherchés par frontière de mot dans la phrase (pas seulement
    # en égalité exacte), sinon la demande partait en réponse KB hors-sujet.
    _phrase_intent = re.search(r"\b(devis|commander|quote|presupuesto)\b", low)
    if _phrase_intent and not _fetch_flow(chat_id):
        _w = _phrase_intent.group(1)
        if _w in ("devis", "quote", "presupuesto"):
            return start_flow(bot, chat_id, "devis", lang, trigger_text=text_clean)
        if catalogue.start_checkout(bot, chat_id, lang):
            return True
        return start_flow(bot, chat_id, "order", lang, trigger_text=text_clean)

    # 2bis. 'nouveau rdv' force un RDV même si un existe déjà
    if low in NEW_RDV_WORDS:
        return start_flow(bot, chat_id, "rdv", lang, force=True)

    # 3. Étape d'un flux actif — RÈGLE D'OR (02/10) : une COMMANDE ("/...")
    # n'est JAMAIS avalée comme réponse à un flux en cours. Bug corrigé :
    # /catalogue, /stats... pendant un devis/lead actif répondaient
    # hors-sujet (la question suivante du flux) au lieu d'exécuter la
    # commande — le flux continue d'exister, prêt à reprendre la main dès
    # que le client répond normalement.
    active = _fetch_flow(chat_id)
    if active and not text_clean.startswith("/"):
        flow, step, data = active
        return _advance_flow(bot, chat_id, flow, step, data, text_clean, lang)

    # 4. Client connu : salutation personnalisée (mémoire longue)
    if low in GREETING_WORDS and client_greeting(bot, chat_id, lang):
        return True

    # 5. Commande non reconnue : on le dit clairement plutôt que de la
    # laisser tomber dans le flux (ci-dessus) ou dans la recherche KB
    # floue (coq-à-l'âne — règle d'or : jamais de réponse devinée pour
    # une commande). Les commandes reconnues ont déjà "return True" plus
    # haut (admin, catalogue, panier, code promo, suivi...).
    # FIX BOSS (03/10) : les commandes de génération d'images Pollinations
    # (/image, /imagine, /photo, /dessin + prompt) ne sont PAS des
    # commandes inconnues — elles doivent atteindre img_gen qui suit dans
    # le pipeline. Avant : « /image un logo doré » répondait « Commande
    # inconnue » et le générateur n'était JAMAIS atteint.
    _IMG_CMDS = {"/image", "/imagine", "/photo", "/dessin"}
    if text_clean.startswith("/") and first_word in _IMG_CMDS:
        return False  # laisse passer vers img_gen (prompt du client)
    if text_clean.startswith("/"):
        bot.send_message(
            chat_id,
            t(lang, "unknown_command") if T["fr"].get("unknown_command")
            else "🤷 Commande inconnue. Tape /menu pour voir les options 👇")
        return True

    return False


def start_flow(bot, chat_id: int, flow: str, lang: str, force: bool = False,
                 trigger_text: str = "") -> bool:
    """Démarre un flux : premier message envoyé au client."""
    if flow == "order":
        _save_flow(chat_id, "order", "service", {})
        bot.send_message(chat_id, t(lang, "order_start"))
    elif flow == "rdv":
        # Mémoire : déjà un RDV à venir → on le rappelle au lieu de mélanger
        if not force:
            upcoming = upcoming_appointment(chat_id)
            if upcoming:
                bot.send_message(
                    chat_id, t(lang, "rdv_already", slot=upcoming[0], topic=upcoming[1]),
                )
                return True
        # Mémoire : client connu → on saute la demande de nom
        client = get_client(chat_id)
        if client and client["name"]:
            _save_flow(chat_id, "rdv", "topic", {"name": client["name"], "known": True})
            bot.send_message(chat_id, t(lang, "rdv_known_start", name=client["name"]))
        else:
            _save_flow(chat_id, "rdv", "name", {})
            bot.send_message(chat_id, t(lang, "rdv_start"))
    elif flow == "devis":
        grid = "\n".join(
            f"{n}️⃣ {name} — {price}" for n, name, price, _ in PRICE_GRID
        )
        # LETTRE MASTER (29/09) — ANTI-FUITE : plus d'interrogatoire budget
        # (3 questions qui faisaient fuir 80% des clients). Le devis
        # démarre direct : grille de services PUIS UNE SEULE question
        # (activité) puis devis complet avec prix € fixe.
        # La demande d'origine du client sert de description (pour le
        # calcul de complexité et le récap admin) : 0 question de plus.
        _save_flow(chat_id, "devis", "service", {"details": (trigger_text or "")[:500]})
        bot.send_message(chat_id, t(lang, "devis_start", grid=grid))
    elif flow == "lead":
        _save_flow(chat_id, "lead", "name", {})
        bot.send_message(chat_id, t(lang, "lead_start"))
    elif flow == "human":
        # Tunnel « parler à un humain » (bouton 💬 du catalogue, ou
        # demande texte « je veux parler à un vrai humain »).
        _save_flow(chat_id, "human", "whatsapp", {})
        bot.send_message(chat_id, t(lang, "human_ask"))
    elif flow == "survey":
        _save_flow(chat_id, "survey", "0", {})
        if lang == "fr":
            # random.choice : jamais 2 fois la même demande d'avis
            bot.send_message(chat_id, random.choice(SURVEY_ASK_VARIANTS_FR))
        else:
            bot.send_message(chat_id, t(lang, "survey_start", question=_survey_q(0, lang)))
    else:
        return False
    return True


# ---------------------------------------------------------------------------
# Moteur d'étapes des flux
# ---------------------------------------------------------------------------

def _advance_flow(bot, chat_id: int, flow: str, step: str, data: dict, text: str, lang: str) -> bool:
    if flow == "order":
        return _step_order(bot, chat_id, step, data, text, lang)
    if flow == "rdv":
        return _step_rdv(bot, chat_id, step, data, text, lang)
    if flow == "devis":
        return _step_devis(bot, chat_id, step, data, text, lang)
    if flow == "lead":
        return _step_lead(bot, chat_id, step, data, text, lang)
    if flow == "survey":
        return _step_survey(bot, chat_id, step, data, text, lang)
    if flow == "human":
        return _step_human(bot, chat_id, step, data, text, lang)
    if flow == "checkout":
        return catalogue.step_checkout(bot, chat_id, step, data, text, lang)
    if flow == "post_cta":
        return _step_post_cta(bot, chat_id, step, data, text, lang)
    _clear_flow(chat_id)
    return False


# ---------------------------------------------------------------------------
# CTA de clôture "oui/non" (fin de sondage, secret_reply, etc.) — FIX
# BOSS (03/10, screenshot) : un "Oui" tapé juste après un message du bot
# qui invite ("Tu veux lancer un projet ? Tape 'commander'") ne
# correspondait à AUCUN flux actif (le flux précédent était déjà clos) :
# le mot tombait jusqu'à la base de connaissances, où un "oui" appris
# pour un AUTRE contexte (ex: réponse à "votre business c'est pour
# WhatsApp ?") répondait n'importe quoi. offer_order_cta() ouvre une
# micro-fenêtre d'1 message qui intercepte oui/non EN PRIORITÉ, pour que
# les deux "oui" ne matchent plus jamais la même chose.
# ---------------------------------------------------------------------------

_POST_CTA_OUI = {"oui", "yes", "sí", "si", "نعم"}
_POST_CTA_NON = {"non", "no", "لا"}


def offer_order_cta(bot, chat_id: int, lang: str, message: str, reply_markup=None) -> None:
    """Envoie un message de clôture qui invite à démarrer une commande,
    et ouvre une micro-fenêtre où oui/non sont interprétés sans ambiguïté."""
    _save_flow(chat_id, "post_cta", "order", {})
    if reply_markup is not None:
        bot.send_message(chat_id, message, reply_markup=reply_markup)
    else:
        bot.send_message(chat_id, message)


def _step_post_cta(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    low = text.strip().lower()
    _clear_flow(chat_id)
    if low in _POST_CTA_OUI:
        if catalogue.start_checkout(bot, chat_id, lang):
            return True
        return start_flow(bot, chat_id, "order", lang, trigger_text=text)
    if low in _POST_CTA_NON:
        bot.send_message(chat_id, t(lang, "post_cta_declined"))
        return True
    # Autre réponse : on relâche la main, le message suit son chemin normal
    # (KB, autre déclencheur...) au lieu d'être avalé à tort.
    return False


def _step_human(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    """Tunnel « parler à un humain » : capture le WhatsApp, prévient l'admin."""
    if step != "whatsapp":
        _clear_flow(chat_id)
        return False
    whatsapp = text.strip()[:100]
    client = get_client(chat_id)
    name = (client or {}).get("name") or "(inconnu)"
    activity = (client or {}).get("activity") or "?"
    upsert_client(chat_id, phone=whatsapp, event="demande humain")
    _clear_flow(chat_id)
    bot.send_message(chat_id, t(lang, "human_done"))
    notify_admin(
        bot,
        "🙋 DEMANDE HUMAIN\n"
        f"👤 {name} — chat_id {chat_id}\n"
        f"💼 Activité : {activity}\n"
        f"📱 WhatsApp : {whatsapp}\n"
        "→ Contacte-le sous 5 min ⚡",
    )
    return True


def _step_order(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    low = text.strip().lower()
    if step == "service":
        digits = text.strip()
        service = _service_by_num(digits)
        if not service:
            bot.send_message(chat_id, t(lang, "invalid_service"))
            return True
        data["service"] = service[1]
        # LETTRE MASTER — ANTI-FUITE : le prix fixe s'affiche dès le choix
        # du service (jamais de « commande = paiement » sans prix).
        data["price"] = service[2] if len(service) > 2 else ""
        _save_flow(chat_id, "order", "activity", data)
        bot.send_message(chat_id, t(lang, "order_activity",
                                   service=service[1], price=data["price"]))
        return True

    if step == "activity":
        data["activity"] = text[:200]
        _save_flow(chat_id, "order", "deadline", data)
        bot.send_message(chat_id, t(lang, "order_deadline"))
        return True

    if step == "deadline":
        data["deadline"] = text[:100]
        _save_flow(chat_id, "order", "name", data)
        # Mémoire : client connu → on propose son nom enregistré
        client = get_client(chat_id)
        if client and client["name"]:
            bot.send_message(chat_id, t(lang, "order_known_name", name=client["name"]))
        else:
            bot.send_message(chat_id, t(lang, "order_name"))
        return True

    if step == "name":
        # Mémoire : 'ok' → on garde le nom déjà connu
        if low in OK_WORDS:
            known = get_client(chat_id)
            if known and known["name"]:
                data["name"] = known["name"]
            else:
                bot.send_message(chat_id, t(lang, "order_name"))
                return True
        else:
            data["name"] = text[:100]
        client = get_client(chat_id)
        _save_flow(chat_id, "order", "phone", data)
        if client and client["phone"]:
            bot.send_message(chat_id, t(lang, "order_known_phone", phone=client["phone"]))
        else:
            bot.send_message(chat_id, t(lang, "order_phone"))
        return True

    if step == "phone":
        if low in OK_WORDS and not data.get("phone_entered"):
            known = get_client(chat_id)
            if known and known["phone"]:
                data["phone"] = known["phone"]
            else:
                bot.send_message(chat_id, t(lang, "order_phone"))
                return True
        else:
            data["phone"] = text[:50]
        _clear_flow(chat_id)

        order_id = _insert("orders", {
            "chat_id": str(chat_id), "service": data.get("service", ""),
            "activity": data.get("activity", ""), "deadline": data.get("deadline", ""),
            "name": data.get("name", ""), "phone": data.get("phone", ""),
            "status": "en attente", "created_at": _now(),
        })
        _insert("leads", {
            "chat_id": str(chat_id), "name": data.get("name", ""),
            "phone": data.get("phone", ""), "sector": data.get("activity", ""),
            "need": data.get("service", ""), "budget": data.get("deadline", ""),
            "created_at": _now(),
        })
        # Mémoire longue : fiche client enrichie + historique
        upsert_client(
            chat_id, name=data.get("name", ""), phone=data.get("phone", ""),
            activity=data.get("activity", ""),
            event=f"Commande n°{order_id} : {data.get('service','')}",
        )

        price_line = f"💰 Prix : {data.get('price','')}\n" if data.get("price") else ""
        recap = (
            f"📌 Commande n°{order_id}\n"
            f"🛠️ Service : {data.get('service','')}\n"
            f"💼 Activité : {data.get('activity','')}\n"
            f"{price_line}"
            f"⏱️ Délai : {data.get('deadline','')}\n"
            f"👤 Nom : {data.get('name','')}\n"
            f"📞 WhatsApp : {data.get('phone','')}"
        )
        bot.send_message(
            chat_id, t(lang, "order_done", recap=recap, whatsapp=WHATSAPP_FALLBACK),
            parse_mode="Markdown",
        )
        notify_admin(
            bot,
            f"🛒 NOUVELLE COMMANDE\n{recap}\n👤 @{chat_id}",
        )
        # Relance auto J+1
        schedule_followup(
            chat_id,
            f"Salut {data.get('name','')} 👋 Toujours partant pour ton {data.get('service','')} ? "
            f"Réponds ici et on bloque ton projet 🚀",
            datetime.now() + timedelta(days=1),
        )
        return True

    _clear_flow(chat_id)
    return False


def _step_rdv(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    if step == "name":
        data["name"] = text[:100]
        _save_flow(chat_id, "rdv", "topic", data)
        bot.send_message(chat_id, t(lang, "rdv_topic"))
        return True

    if step == "topic":
        data["topic"] = text[:200]
        slots = _gen_slots(lang)
        data["slots"] = [[label, dt.isoformat(timespec="seconds")] for label, dt in slots]
        _save_flow(chat_id, "rdv", "slot", data)
        bot.send_message(chat_id, t(lang, "rdv_slot", slots=_fmt_slots(slots)))
        return True

    if step == "slot":
        digits = "".join(ch for ch in text if ch.isdigit())
        slots = [tuple(s) for s in data.get("slots", [])]
        index = int(digits) - 1 if digits else -1
        if index < 0 or index >= len(slots):
            bot.send_message(chat_id, t(lang, "invalid_slot"))
            return True
        label, slot_iso = slots[index]
        _clear_flow(chat_id)

        _insert("appointments", {
            "chat_id": str(chat_id), "name": data.get("name", ""),
            "topic": data.get("topic", ""), "slot": label, "slot_iso": slot_iso,
            "created_at": _now(),
        })
        # Événement Google Calendar (silencieux si non lié)
        try:
            from google_link import create_calendar_event
            create_calendar_event(
                f"RDV Komara 🇬🇳 — {data.get('name', '')} : {data.get('topic', '')}",
                datetime.fromisoformat(slot_iso), 30,
                description=f"Client Telegram chat_id {chat_id}",
                tz_offset_hours=TIMEZONE_OFFSET,
            )
        except Exception:
            logger.debug("Création événement Calendar ignorée", exc_info=True)
        # Mémoire longue : fiche client + historique
        upsert_client(
            chat_id, name=data.get("name", ""),
            event=f"RDV pris : {label} ({data.get('topic', '')})",
        )
        if lang == "fr":
            # random.choice : jamais 2 fois la même confirmation
            rdv_txt = random.choice(RDV_DONE_VARIANTS_FR).format(
                nom=data.get("name") or "toi",
                sujet=data.get("topic", ""),
                slot=label,
            )
            bot.send_message(chat_id, rdv_txt)
        else:
            bot.send_message(
                chat_id,
                t(lang, "rdv_done", name=data.get("name", ""), topic=data.get("topic", ""), slot=label),
            )
        notify_admin(
            bot,
            f"📅 NOUVEAU RDV\n👤 {data.get('name','')}\n📝 {data.get('topic','')}\n"
            f"🗓️ {label}\n📞 chat_id: {chat_id}",
        )
        # Rappel 1h avant le créneau
        slot_dt = datetime.fromisoformat(slot_iso)
        reminder_at = slot_dt - timedelta(hours=1)
        if reminder_at > datetime.now():
            schedule_followup(
                chat_id,
                f"⏰ Rappel : ton RDV Komara Agency 🇬🇳 est à {label}. C'est bientôt ! 👊",
                reminder_at,
            )
        return True

    _clear_flow(chat_id)
    return False


# ---------------------------------------------------------------------------
# Calcul du devis : base + complexité + urgence - promo (100% local)
# ---------------------------------------------------------------------------

def _devis_base_price(service: str) -> float:
    """Prix de base du devis = prix du catalogue en base (source unique).
    Si l'admin change un prix (/produit maj), le devis suit — fini les
    300€ d'un côté et 100€ de l'autre."""
    import catalogue
    with catalogue.DB_LOCK:
        row = catalogue.DB_CONN.execute(
            "SELECT price FROM products WHERE name = ? AND active = 1", (service,)
        ).fetchone()
    if row:
        return float(row[0])
    for _cat, name, _d, price in catalogue.OFFICIAL_SERVICES_2026:
        if name == service:
            return price
    return 50.0

DEVIS_COMPLEX_RULES: list[tuple[list[str], int, str]] = [
    (["paiement", "orange money", "wave", "paypal", "encaisser"], 40, "Paiement intégré au bot"),
    (["multilingue", "plusieurs langues", "anglais et", "en arabe", "en espagnol",
      "soussou", "malinke", "malinké"], 20, "Version multilingue"),
    (["crm", "google agenda", "notion", "google sheet", "formulaire",
      "base de clients", "prospect"], 15, "Intégrations (agenda/CRM/prospects)"),
    (["messenger", "instagram", "tiktok", "multi-canal", "multi canal", "4 canaux"], 25, "Canaux supplémentaires"),
    (["mémoire", "memory", "apprendre", "relance auto", "relances auto"], 30, "Mémoire + relances automatiques"),
]

DEVIS_URGENT_WORDS = ["urgent", "24h", "48h", "72h", "express", "rapidement",
                      "au plus vite", "cette semaine", "this week", "urgente"]
DEVIS_FLEXIBLE_WORDS = ["flexible", "peu importe", "3 mois", "6 mois", "no rush"]

def _norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in [("é","e"),("è","e"),("ê","e"),("à","a"),("ç","c"),("ù","u")]:
        s = s.replace(a, b)
    return s

def calc_devis(data: dict) -> tuple[list[str], float]:
    """Calcule le devis à partir des infos collectées. Retourne (lignes, total)."""
    service = data.get("service", "")
    base = _devis_base_price(service)
    lines = [f"Base {service} : {base:g}€"]
    total = base

    details = _norm(data.get("details", "") + " " + data.get("activity", ""))
    for keywords, pct, label in DEVIS_COMPLEX_RULES:
        if any(k in details for k in keywords):
            add = base * pct / 100
            lines.append(f"{label} : +{pct}% (+{add:g}€)")
            total += add

    deadline = _norm(data.get("deadline", ""))
    if any(w in deadline for w in DEVIS_URGENT_WORDS):
        add = base * 25 / 100
        lines.append(f"Délai express : +25% (+{add:g}€)")
        total += add

    promo = data.get("promo")
    if promo:
        remise = total * promo["pct"] / 100
        lines.append(f"Code {promo['code']} : -{promo['pct']:g}% (-{remise:g}€)")
        total -= remise

    return lines, round(total, 2)


def _devis_country_step(bot, chat_id: int, data: dict, lang: str) -> bool:
    """Feature #1 — détection auto de la localité :
    (1a) n° de tél du client → (1b) langue du chat → (2) on demande."""
    client = get_client(chat_id) or {}
    phone = client.get("phone") or ""
    cc, source = devis_engine.detect_locality(phone, lang)
    if source in ("phone", "lang"):
        data["country"] = cc
        _save_flow(chat_id, "devis", "activity", data)
        bot.send_message(chat_id, t(lang, "devis_activity"))
    else:
        _save_flow(chat_id, "devis", "country", data)
        bot.send_message(chat_id, devis_engine.t(lang, "country_ask"))
    return True


def _step_devis(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    """LETTRE MASTER (29/09) — ANTI-FUITE : 2 échanges max avant le devis.

    Ancien tunnel (interrogatoire qui faisait fuir 80% des clients) :
      service → détails → nom → pays → activité → délai → code promo
      = jusqu'à 6 questions avant le devis.

    Nouveau tunnel :
      service (grille catalogue avec prix €) → activité (1 SEULE question)
      → DEVIS DIRECT avec prix € fixe + conversion locale indicative.

    • Le délai n'est plus demandé : celui du service s'affiche dans le
      devis (« 3-5 jours »...).
    • Le code promo n'est plus une question bloquante : le client le tape
      via /code (indiqué dans le devis), il est appliqué automatiquement.
    • Nom/pays : récupérés SANS question (nom Telegram mémorisé + pays
      auto-détecté via téléphone/langue ; à défaut € fixe seul).
    • Les 3 infos de production (nom du bot, canaux, 5 questions)
      seront demandées PAR L'ÉQUIPE après l'acompte, pas par le bot.
    """
    if step == "service":
        digits = text.strip()
        service = _service_by_num(digits)
        if not service:
            bot.send_message(chat_id, t(lang, "invalid_service"))
            return True
        _, name, price, delay = service
        data.update({"service": name, "price": price, "delay": delay})
        _save_flow(chat_id, "devis", "activity", data)
        bot.send_message(chat_id, t(lang, "devis_activity"))
        return True

    if step == "activity":
        data["activity"] = text[:200]
        # Délai = celui du service catalogue (affiché dans le devis)
        data["deadline"] = data.get("delay", "") or "selon projet"
        # Nom : mémoire client (capturé automatiquement dès le 1er message)
        client = get_client(chat_id) or {}
        data["name"] = data.get("name") or client.get("name") or "cher client"
        # Pays : auto-détection téléphone → langue ; si inconnu, le devis
        # reste en € fixe (pas de question supplémentaire).
        cc, _src = devis_engine.detect_locality(client.get("phone") or "", lang)
        data["country"] = cc or "GN"
        return _finish_devis(bot, chat_id, data, lang)

    _clear_flow(chat_id)
    return False


def _finish_devis(bot, chat_id: int, data: dict, lang: str) -> bool:
    _clear_flow(chat_id)
    # 🎟️ Code promo validé plus tôt via /code → appliqué automatiquement,
    # sans question bloquante (règle lettre master).
    promo = data.get("promo")
    if not promo:
        pending = _PENDING_PROMO.pop(chat_id, None)
        if pending:
            code, pct = pending
            result = check_promo_code(code) or (code, pct)
            if result:
                code, pct = result
                with DB_LOCK:
                    DB_CONN.execute(
                        "UPDATE promo_codes SET uses = uses + 1 WHERE code =?", (code,)
                    )
                    DB_CONN.commit()
                data["promo"] = {"code": code, "pct": pct}
                bot.send_message(chat_id, t(lang, "devis_promo_ok", code=code, pct=pct),
                                parse_mode="Markdown")
                promo = data["promo"]

    # Calcul complet : base + complexité + urgence - promo
    calc_lines, total = calc_devis(data)
    calc_display = "\n".join("• " + ln for ln in calc_lines)

    # ── Feature #1 : conversion en monnaie locale du client ──
    import commercial_db as cdb
    cc = data.get("country") or "GN"
    conv = devis_engine.convert_devis(cc, total)
    convert_line = devis_engine.t(
        lang, "convert_line",
        country=conv["country"],
        price_local=devis_engine.format_price(conv),
        price_base=f"{total:g}", base="€",
    )
    client = get_client(chat_id) or {}
    client_name = data.get("name") or client.get("name") or "cher client"
    # ── Feature #2 : le devis part en relance auto J+1/J+3/J+7 ──
    cdb.insert_pending_quote(
        chat_id=chat_id, client_name=client_name,
        phone=client.get("phone") or "",
        country_code=conv["country_code"], currency=conv["currency"],
        price_local=conv["price_local"], price_eur=total,
        project_desc=data.get("details", "") or data.get("service", ""),
        service=data.get("service", ""),
    )
    cdb.set_step(chat_id, "quoted")

    _insert("quotes", {
        "chat_id": str(chat_id), "service": data.get("service", ""),
        "price": f"{total:g}€", "delay": data.get("deadline", ""),
        "details": data.get("details", ""), "code": promo["code"] if promo else "",
        "created_at": _now(),
    })
    bot.send_message(
        chat_id,
        t(
            lang, "devis_calc",
            service=data.get("service", ""), calc=calc_display,
            total=f"{total:g}", delay=data.get("deadline", ""),
            whatsapp=WHATSAPP_FALLBACK,
        )
        + "\n\n" + convert_line
        + "\n\n" + devis_engine.t(lang, "paid_hint", whatsapp=WHATSAPP_FALLBACK)
        + "\n\n" + t(lang, "code_usage"),
    )
    # Lot 23 : multi-mode — carte PayPal/Stripe/Support en fin de devis
    # si l'admin a activé les boutons (/paiement on).
    try:
        import payment_links
        payment_links.send_payment_card(bot, chat_id, lang)
    except Exception:
        pass  # désactivé ou sans panier/devis : comportement inchangé
    upsert_client(
        chat_id,
        event=f"Devis express : {data.get('service','')}"
        + (f" (code {promo['code']} -{promo['pct']:g}%)" if promo else ""),
    )
    # LETTRE MASTER — format admin : TOUJOURS en français, lisible,
    # avec prix € fixe, conversion locale, langue du client + demande
    # d'origine (même si le client parle EN/ES/AR).
    admin_note = f"\n🎟️ Code {promo['code']} : -{promo['pct']:g}%" if promo else ""
    notify_admin(
        bot,
        f"📄 DEVIS EXPRESS\n"
        f"🛒 Service : {data.get('service','')}\n"
        f"💰 Prix FIXE : {total:g}€\n"
        f"💱 Client {conv['country']} : ~{devis_engine.format_price(conv)} (indicatif)\n"
        f"👤 Client : {client_name} ({conv['country_code']}) - Langue: {lang}\n"
        f"💬 Demande : \"{data.get('details', '') or data.get('activity', '')}\"\n"
        f"🆔 chat_id: {chat_id}"
        + admin_note,
    )
    return True


def _step_lead(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    if step == "name":
        data["name"] = text[:100]
        _save_flow(chat_id, "lead", "phone", data)
        bot.send_message(chat_id, t(lang, "lead_phone"))
        return True

    if step == "phone":
        data["phone"] = text[:50]
        _save_flow(chat_id, "lead", "sector", data)
        bot.send_message(chat_id, t(lang, "lead_sector"))
        return True

    if step == "sector":
        data["sector"] = text[:100]
        _save_flow(chat_id, "lead", "need", data)
        bot.send_message(chat_id, t(lang, "lead_need"))
        return True

    if step == "need":
        data["need"] = text[:300]
        _clear_flow(chat_id)
        _insert("leads", {
            "chat_id": str(chat_id), "name": data.get("name", ""),
            "phone": data.get("phone", ""), "sector": data.get("sector", ""),
            "need": data.get("need", ""), "budget": "", "created_at": _now(),
        })
        upsert_client(
            chat_id, name=data.get("name", ""), phone=data.get("phone", ""),
            event=f"Lead : {data.get('sector','')} — {data.get('need','')}",
        )
        bot.send_message(chat_id, t(lang, "lead_done", whatsapp=WHATSAPP_FALLBACK))
        notify_admin(
            bot,
            f"📞 NOUVEAU LEAD\n👤 {data.get('name','')}\n📞 {data.get('phone','')}\n"
            f"💼 {data.get('sector','')}\n📝 {data.get('need','')}\n💬 chat_id: {chat_id}",
        )
        # Relance J+3 si pas de réponse
        schedule_followup(
            chat_id,
            f"Salut {data.get('name','')} 👋 Komara Agency 🇬🇳 ici. Toujours besoin d'un coup de main "
            f"pour {data.get('need','')} ? Dis-moi 👇",
            datetime.now() + timedelta(days=3),
        )
        return True

    _clear_flow(chat_id)
    return False


def _step_survey(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    try:
        idx = int(step)
    except ValueError:
        _clear_flow(chat_id)
        return False
    if idx >= len(SURVEY_QUESTIONS):
        _clear_flow(chat_id)
        return False

    question = SURVEY_QUESTIONS[idx]
    kind = question["kind"]
    answer = text.strip()
    low = answer.lower()

    if kind == "rate":
        digits = "".join(ch for ch in answer if ch.isdigit())
        if not digits or not (1 <= int(digits) <= 5):
            bot.send_message(chat_id, t(lang, "invalid_rate"))
            return True
        answer = digits
    elif kind == "bool":
        if low in {"oui", "yes", "sí", "si", "نعم"}:
            answer = "oui"
        elif low in {"non", "no", "لا"}:
            answer = "non"
        else:
            bot.send_message(chat_id, t(lang, "invalid_bool"))
            return True
    elif kind == "text" and low in {"passer", "skip", "نم", "pasar", "omitir"}:
        answer = ""

    _insert("survey_answers", {
        "chat_id": str(chat_id), "question_id": question["id"],
        "answer": answer[:500], "created_at": _now(),
    })

    nxt = idx + 1
    if nxt < len(SURVEY_QUESTIONS):
        _save_flow(chat_id, "survey", str(nxt), data)
        bot.send_message(chat_id, t(lang, "survey_next", question=_survey_q(nxt, lang)))
    else:
        _clear_flow(chat_id)
        offer_order_cta(bot, chat_id, lang, t(lang, "survey_done"))
        notify_admin(bot, f"⭐ NOUVEAU SONDAGE (chat_id {chat_id}) — dernière réponse : {answer[:60]}")
    return True


# ---------------------------------------------------------------------------
# Commandes admin : /stats et /rapport
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pause (/pause, /reprend), argent (/stats) et fiche client (/prend)
# ---------------------------------------------------------------------------

def is_paused() -> bool:
    """Le bot est-il fermé (/pause) ? En cas d'erreur : False (ouvert)."""
    try:
        if DB_CONN is None:
            init_db()
        with DB_LOCK:
            row = DB_CONN.execute(
                "SELECT value FROM bot_state WHERE key='paused'").fetchone()
        return bool(row and str(row[0]) == "1")
    except Exception:
        return False


def set_paused(value: bool) -> None:
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES ('paused', ?)",
            ("1" if value else "0",))
        DB_CONN.commit()


def _parse_price(raw) -> float:
    """« 250€ » / « 1 250,50€ » → 250.0 / 1250.5"""
    digits = re.sub(r"[^\d.,]", "", str(raw or ""))
    digits = re.sub(r"(\d)\s+(\d{3})", r"\1\2", digits)
    digits = digits.replace(",", ".")
    try:
        return float(digits) if digits else 0.0
    except ValueError:
        return 0.0


def _money_summary() -> str:
    """Vue argent : devis émis (pipeline) + commandes en attente."""
    if DB_CONN is None:
        init_db()
    month_prefix = datetime.now().strftime("%Y-%m")
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT price, created_at FROM quotes").fetchall()
        pending = DB_CONN.execute(
            "SELECT COUNT(*) FROM orders WHERE status='en attente'").fetchone()
    prices = [(_parse_price(r[0]), str(r[1] or "")) for r in rows]
    total = sum(p for p, _ in prices)
    month_prices = [p for p, d in prices if d.startswith(month_prefix)]
    mtot = sum(month_prices)
    return t("fr", "admin_money", n=len(prices),
             total=f"{total:,.0f}".replace(",", " "),
             mn=len(month_prices),
             mtot=f"{mtot:,.0f}".replace(",", " "),
             pending=pending[0] if pending else 0)


def _admin_apprends(bot, chat_id: int, args: str, lang: str) -> bool:
    """Admin : /apprends (alias /apprendre, /apprendres) — MODE FAQ via
    chat admin (règle boss 29/09 : pas d'import CSV, le boss apprend au
    bot en écrivant directement).

    /apprends <question> || <réponse>
    → enseigne le bot : réponse publiée en runtime + persistée dans le
    Google Sheet dédié « Komara Bot - Mémoire » (memory_sheets).

    STABILITÉ (règle boss) : aucune erreur ne doit faire planter le bot.
    DOUBLON : réapprendre une question la REMPLACE (nouvelle version)."""
    try:
        return _apprends_core(bot, chat_id, args, lang)
    except Exception:
        logger.exception("/apprends a échoué (anti-crash)")
        try:
            bot.send_message(
                chat_id,
                "⚠️ Oups, je n'ai pas pu enregistrer cette connaissance. "
                "Réessaie avec : /apprends <question> || <réponse>")
        except Exception:
            pass
        return True


def _apprends_core(bot, chat_id: int, args: str, lang: str) -> bool:
    import knowledge_store
    parts = (args or "").split("||", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        bot.send_message(
            chat_id,
            "Usage : /apprends <question> || <réponse>\n"
            "Exemple : /apprends vous livrez à Kindia || Oui, partout en Guinée 🇬🇳 livraison offerte !")
        return True
    result = knowledge_store.learn_entry(parts[0], parts[1], directory=ACTIONS_DIR)
    # RÈGLE BOSS (02/10) : réapprendre une question déjà connue ne bloque
    # plus — ça REMPLACE la réponse (base perso ou officielle neutralisée).
    if result.get("updated") or result.get("replaced"):
        bot.send_message(
            chat_id,
            f"✅ Connaissance mise à jour (remplace « {result['replaced'][:100]} ») :\n"
            f"❓ {result['question'][:120]}\n💬 {result['answer'][:120]}")
        return True
    bot.send_message(
        chat_id,
        f"✅ Connaissance ajoutée à la base :\n❓ {result['question'][:120]}\n💬 {result['answer'][:120]}")
    return True


def _admin_broadcast(bot, chat_id: int, args: str, lang: str) -> bool:
    text = args.strip()
    if not text:
        bot.send_message(chat_id, t(lang, "broadcast_usage"))
        return True
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT chat_id, name FROM clients").fetchall()
    if not rows:
        bot.send_message(chat_id, t(lang, "broadcast_none"))
        return True
    sent = failed = 0
    for cid, name in rows:
        try:
            body = f"Salut {name} 👋\n\n{text}" if name else text
            bot.send_message(int(cid), body)
            sent += 1
            time.sleep(0.06)  # limite anti-flood Telegram
        except Exception:
            failed += 1
    bot.send_message(chat_id, t(lang, "broadcast_done", sent=sent, failed=failed))
    return True


def _admin_prend(bot, chat_id: int, args: str, lang: str) -> bool:
    """Fiche client (nom, téléphone, chat_id) pour parler au client en direct."""
    q = args.strip()
    if DB_CONN is None:
        init_db()
    if not q:
        with DB_LOCK:
            rows = DB_CONN.execute(
                "SELECT chat_id, name, phone FROM clients "
                "ORDER BY last_seen DESC LIMIT 10").fetchall()
        if not rows:
            bot.send_message(chat_id, t(lang, "broadcast_none"))
            return True
        lines = "\n".join(
            f"• {(name or 'Sans nom')} — {phone or '📱 non connu'} (id {cid})"
            for cid, name, phone in rows)
        bot.send_message(chat_id, t(lang, "prend_list", lines=lines))
        return True
    with DB_LOCK:
        row = None
        if q.isdigit():
            row = DB_CONN.execute(
                "SELECT chat_id, name, phone, activity, last_seen FROM clients "
                "WHERE chat_id = ?", (q,)).fetchone()
        if row is None:
            row = DB_CONN.execute(
                "SELECT chat_id, name, phone, activity, last_seen FROM clients "
                "WHERE name LIKE ? OR phone LIKE ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (f"%{q}%", f"%{q}%")).fetchone()
        if row is None:
            bot.send_message(chat_id, t(lang, "prend_none", q=q))
            return True
        orders = DB_CONN.execute(
            "SELECT COUNT(*) FROM orders WHERE chat_id = ?",
            (str(row[0]),)).fetchone()
        phone = row[2]
        if not phone:
            o = DB_CONN.execute(
                "SELECT phone FROM orders WHERE chat_id = ? AND phone != '' "
                "ORDER BY id DESC LIMIT 1", (str(row[0]),)).fetchone()
            phone = o[0] if o else ""
    bot.send_message(chat_id, t(lang, "prend_card",
                                 name=row[1] or "Sans nom",
                                 phone=phone or "📱 non connu (demande-le-lui)",
                                 chat_id=row[0], activity=row[3] or "—",
                                 orders=orders[0] if orders else 0,
                                 last_seen=row[4]))
    return True


COMPLAINT_WORDS = [
    "arnaque", "arnaqué", "porter plainte", "plainte", "escroc", "voleur",
    "je me fais avoir", "rembourse", "scandal", "honteux", "trahi",
    "fraude", "c est du vol",
    # EN
    "scam", "scammed", "rip-off", "ripoff", "fraud", "file a complaint",
    "complaint", "i want a refund", "sue you",
    # ES
    "estafa", "estafado", "denuncia", "quiero mi reembolso", "ladron",
    # AR
    "احتيال", "نصب", "شكوى", "اشكو", "استرجاع",
]


# Lot 21 : une question de CONFIANCE avant achat (« comment je sais que
# t'es pas un arnaqueur ? ») contient un mot de la liste COMPLAINT_WORDS
# ("arnaque" est un sous-texte de "arnaqueur") mais n'est PAS une
# réclamation — c'est une question légitime que la KB sait très bien
# traiter (fiche persona : "Très bonne question, je suis une IA
# officielle..."). On ne déclenche JAMAIS l'accusé de réception générique
# pour ces formulations.
TRUST_QUESTION_RE = re.compile(
    r"comment\s+(?:je\s+)?(?:sais|savoir)|"
    r"pas\s+(?:un|une|des)?\s*(?:arnaqu\w*|escroc\w*|voleur\w*|fraud\w*|scam\w*)|"
    r"not\s+a\s+scam|is\s+this\s+(?:legit|real)|"
    r"c[oó]mo\s+s[eé]\s+que\s+no|no\s+es\s+una?\s+estafa|"
    r"كيف\s+أعرف|لست\s+محتال",
    re.IGNORECASE,
)

def _is_complaint(low: str) -> bool:
    if TRUST_QUESTION_RE.search(low):
        return False
    return any(w in low for w in COMPLAINT_WORDS)


HUMAN_PHRASES = [
    "un humain", "parler à un humain", "parler a un humain", "un vrai humain",
    "agent humain", "conseiller humain", "une vraie personne", "vraie personne",
    "talk to a human", "talk to a real", "real person", "speak to a human",
    "hablar con un humano", "un humano", "persona real",
    "شخص حقيقي", "تحدث مع شخص",
]


def _wants_human(low: str) -> bool:
    return any(ph in low for ph in HUMAN_PHRASES)


# Mémoire légère d'activité : « je vends dans une boutique » → fiche client
ACTIVITY_RE = re.compile(
    r"\b(?:je\s+vends?|je\s+tiens?|j['’]ai\s+une?|je\s+g[èe]re?|je\s+dirige|"
    r"on\s+vend|nous\s+vendons|j['’]ouvre)\s+[^.,!?\n]{0,24}?"
    r"\b(boutique|salon(?:\s+de\s+coiffure)?|restaurant|[eé]picerie|h[ôo]tel|"
    r"[eé]cole|pharmacie|agence|ferme|couture|commerce|business|shop|"
    r"boulangerie|v[êe]tements|p[âa]tisserie|garage|cabinet)\b",
    re.IGNORECASE,
)


def _maybe_remember_activity(chat_id: int, text: str) -> None:
    m = ACTIVITY_RE.search(text or "")
    if not m:
        return
    activity = m.group(1).lower().strip()
    client = get_client(chat_id)
    if client and client.get("activity") == activity:
        return
    upsert_client(chat_id, activity=activity)


# 🎟️ Codes promo validés via /code, en attente d'application automatique
# au prochain devis du client (règle lettre : jamais de question bloquante).
_PENDING_PROMO: dict[int, tuple[str, int]] = {}


def _promo_check(bot, chat_id: int, text: str, lang: str, first_token: bool = False) -> bool:
    """Client : /code XXX → vérifie un code promo (catalogue/devis)."""
    words = [w for w in text.split() if w.upper() not in {"CODE", "PROMO", "/CODE", "/PROMO"}]
    if not words:
        bot.send_message(chat_id, t(lang, "code_usage"))
        return True
    code = (words[0] if first_token else words[-1]).upper()
    if DB_CONN is None:
        init_db()
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT discount_pct, uses, max_uses FROM promo_codes "
            "WHERE code = ? AND active = 1", (code,)).fetchone()
    if row and (row[2] in (None, 0) or row[1] < row[2]):
        # LETTRE MASTER : le code validé est mis de côté et appliqué
        # AUTOMATIQUEMENT au prochain devis (plus de question bloquante).
        _PENDING_PROMO[chat_id] = (code, row[0])
        bot.send_message(chat_id, t(lang, "code_ok", code=code, pct=row[0]))
    else:
        bot.send_message(chat_id, t(lang, "code_bad", code=code))
    return True


def _admin_msg(bot, chat_id: int, args: str, lang: str) -> bool:
    """/msg <chat_id|numéro|nom> <texte> — écrire à un client via le bot."""
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.send_message(chat_id, t(lang, "msg_usage"))
        return True
    target_q, message = parts[0].strip(), parts[1].strip()
    if DB_CONN is None:
        init_db()
    target_id = None
    label = target_q
    with DB_LOCK:
        row = None
        if target_q.lstrip("-").isdigit():
            row = DB_CONN.execute(
                "SELECT chat_id, name, phone FROM clients WHERE chat_id = ?",
                (target_q,)).fetchone()
        if row is None:
            row = DB_CONN.execute(
                "SELECT chat_id, name, phone FROM clients "
                "WHERE name LIKE ? OR phone LIKE ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (f"%{target_q}%", f"%{target_q}%")).fetchone()
    if row:
        target_id, label = int(row[0]), (row[1] or row[0])
    elif target_q.lstrip("-").isdigit():
        target_id = int(target_q)  # chat_id direct hors table clients
    else:
        bot.send_message(chat_id, t(lang, "msg_not_found", q=target_q))
        return True
    try:
        bot.send_message(target_id, message)
        bot.send_message(chat_id, t(lang, "msg_sent", target=label))
    except Exception:
        bot.send_message(chat_id, t(lang, "msg_failed"))
    return True


def _send_kb_templates(bot, chat_id: int) -> None:
    """Admin : envoie 3 modèles prêts à remplir (FAQ, CSV, JSON) pour
    /kb_import. L'admin les remplit et les renvoie en pièce jointe."""
    import io
    faq = (
        "# Modèle FAQ — une question par bloc\n\n"
        "Q: vous livrez à Kindia ?\n"
        "A: Oui, partout en Guinée 🇬🇳 livraison offerte !\n\n"
        "Q: combien coûte un logo ?\n"
        "A: 300 000 à 500 000 GNF selon la complexité.\n\n"
        "Q: quel est le délai ?\n"
        "A: Logo 2-3 jours, affiche 24-48h, site 7 jours.\n"
    )
    csv = (
        "question;rponse\n"
        "vous livrez a kindia;Oui, partout en Guinée 🇬🇳\n"
        "combien coute un logo;300 000 à 500 000 GNF\n"
        "quel delai;Logo 2-3 jours, affiche 24-48h\n"
    )
    js = (
        '[\n'
        '  {"q": "vous livrez a kindia", "a": "Oui, partout en Guinée 🇬🇳"},\n'
        '  {"q": "combien coute un logo", "a": "300 000 a 500 000 GNF"}\n'
        ']\n'
    )
    for name, body, ext in (("modele_FAQ", faq, ".txt"),
                            ("modele_QR", csv, ".csv"),
                            ("modele", js, ".json")):
        try:
            bot.send_document(chat_id,
                              io.BytesIO(body.encode("utf-8")),
                              visible_file_name=name + ext,
                              caption=f"Modèle {ext.upper()} — remplis-le et renvoie-le 📥")
        except Exception:
            logger.warning("Envoi modèle %s échoué", ext, exc_info=True)


def _admin_command(bot, chat_id: int, command: str, args: str = "", lang: str = "fr") -> bool:
    # Fail-closed : sans ADMIN_CHAT_ID configuré, personne n'a accès
    # (même le propriétaire) — jamais l'inverse.
    if not ADMIN_CHAT_ID:
        logger.warning(
            "ADMIN_CHAT_ID non configuré — commande admin refusée : %s (chat %s)",
            command, chat_id,
        )
        bot.send_message(chat_id, t(lang, "admin_only"))
        return True
    if chat_id != ADMIN_CHAT_ID:
        bot.send_message(chat_id, t(lang, "admin_only"))
        return True

    if command == "/facture":
        invoices.cmd_facture(bot, chat_id, args, lang)
        return True

    if command == "/backup":
        backup_drive.cmd_backup(bot, chat_id, lang)
        return True

    if command == "/hebdo":
        weekly_report.cmd_hebdo(bot, chat_id, lang)
        return True

    if command == "/google":
        google_link.cmd_google(bot, chat_id, lang)
        return True

    if command == "/kb_import":
        import kb_import
        bot.send_message(chat_id, kb_import.USAGE)
        return True

    if command in ("/kb_modele", "/modeles"):
        _send_kb_templates(bot, chat_id)
        return True

    if command in {"/produit", "/produits"}:
        catalogue.admin_product(bot, chat_id, args, lang)
        return True

    if command == "/paiement":
        import payment_links
        payment_links.cmd_paiement(bot, chat_id, args, lang)
        return True

    if command == "/admin":
        # Feature #6 : Dashboard Patron — 5 cartes KPI en € (lecture DB
        # locale uniquement), puis rappel des commandes du panneau.
        try:
            import pack_patron
            bot.send_message(chat_id, pack_patron.dashboard(lang))
        except Exception as e:
            logger.error("Dashboard patron : %s", e)
        bot.send_message(chat_id, t(lang, "admin_panel"))
        return True

    if command == "/msg":
        return _admin_msg(bot, chat_id, args, lang)

    if command == "/pause":
        set_paused(True)
        bot.send_message(chat_id, t(lang, "pause_on"))
        return True

    if command == "/reprend":
        set_paused(False)
        bot.send_message(chat_id, t(lang, "pause_off"))
        return True

    if command == "/broadcast":
        return _admin_broadcast(bot, chat_id, args, lang)

    if command == "/prend":
        return _admin_prend(bot, chat_id, args, lang)

    # Lot 20 — promos globales : /solde -20%, /promo -30%, /KA -40%,
    # /bonnus -35% : appliquées au catalogue + clients notifiés.
    if command == "/solde":
        return set_global_promo(bot, 20.0, "SOLDE")
    if command == "/ka":
        return set_global_promo(bot, 40.0, "KA")
    if command == "/bonnus":
        return set_global_promo(bot, 35.0, "BONNUS")
    if command in ("/apprends", "/apprendre", "/apprendres"):
        return _admin_apprends(bot, chat_id, args, lang)

    if command == "/stats":
        with DB_LOCK:
            sat_rows = DB_CONN.execute(
                "SELECT answer FROM survey_answers WHERE question_id='satisfaction'"
            ).fetchall()
            reco_rows = DB_CONN.execute(
                "SELECT answer FROM survey_answers WHERE question_id='reco'"
            ).fetchall()
        satisfactions = [int(r[0]) for r in sat_rows if str(r[0]).isdigit()]
        avg = round(sum(satisfactions) / len(satisfactions), 1) if satisfactions else 0
        recos = sum(1 for r in reco_rows if str(r[0]).lower() in {"oui", "yes", "sí", "si"})
        reco_pct = round(100 * recos / len(reco_rows)) if reco_rows else 0
        unrecognized = len(get_unrecognized_stats(limit=200).get("items", []))
        bot.send_message(
            chat_id,
            t(
                lang, "admin_stats",
                orders=_count("orders"), rdv=_count("appointments"),
                leads=_count("leads"), quotes=_count("quotes"),
                surveys=_count("survey_answers"), satisfaction=avg,
                reco=reco_pct, unrecognized=unrecognized,
            ),
        )
        # 💰 Vue argent (devis = pipeline) : /stats montre aussi l'argent
        bot.send_message(chat_id, _money_summary())
        return True

    if command in {"/maj", "/update"}:
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_update_order(bot, chat_id, args, lang)

    if command in {"/commandes", "/orders"}:
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_list_orders(bot, chat_id, lang)

    if command == "/promo":
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_promo(bot, chat_id, args, lang)

    if command == "/promos":
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_list_promos(bot, chat_id, lang)

    if command == "/rdvs":
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_list_rdv(bot, chat_id, lang)

    if command == "/clients":
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            bot.send_message(chat_id, t(lang, "admin_only"))
            return True
        return admin_list_clients(bot, chat_id, lang)

    if command == "/export":
        return export_csv(bot, chat_id, lang)

    if command == "/rapport":
        stats = get_unrecognized_stats(limit=10)
        items = stats.get("items", [])
        if not items:
            bot.send_message(chat_id, "✅ Aucune question non reconnue pour le moment. KB au top 👌")
            return True
        lines = "\n".join(
            f"{i+1}. « {item.get('question','')[:60]} » ×{item.get('count',0)}"
            for i, item in enumerate(items)
        )
        bot.send_message(chat_id, t(lang, "admin_report", limit=len(items), items=lines))
        return True

    return False





# ---------------------------------------------------------------------------
# Mémoire longue : fiche client persistante (nom, téléphone, historique)
# ---------------------------------------------------------------------------

def upsert_client(chat_id: int, name: str = "", phone: str = "",
                  activity: str = "", event: str = "") -> None:
    """Crée ou met à jour la fiche client + journalise un évènement."""
    key = str(chat_id)
    now = _now()
    with DB_LOCK:
        row = DB_CONN.execute(
            """SELECT name, phone, activity, events, first_seen,
                      client_step, last_auto_message_date, assurance_refusee
               FROM clients WHERE chat_id =?""",
            (key,),
        ).fetchone()
        if row:
            (old_name, old_phone, old_activity, old_events, first_seen,
             old_step, old_last_auto, old_assur) = row
            name = name or old_name
            phone = phone or old_phone
            activity = activity or old_activity
            events = json.loads(old_events or "[]")
        else:
            events = []
            first_seen = now
            old_step, old_last_auto, old_assur = "new", "", 0
        if event:
            events.append(f"{now[:10]} : {event}")
            events = events[-50:]  # 50 derniers évènements max
        DB_CONN.execute(
            "INSERT OR REPLACE INTO clients "
            "(chat_id, name, phone, activity, events, first_seen, last_seen, "
            " client_step, last_auto_message_date, assurance_refusee) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, name[:100], phone[:50], activity[:150],
             json.dumps(events, ensure_ascii=False), first_seen, now,
             old_step, old_last_auto, old_assur),
        )
        DB_CONN.commit()


def get_client(chat_id: int) -> dict | None:
    """Retourne la fiche client connue, ou None."""
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT name, phone, activity, events, first_seen, last_seen "
            "FROM clients WHERE chat_id =?",
            (str(chat_id),),
        ).fetchone()
    if not row:
        return None
    name, phone, activity, events, first_seen, last_seen = row
    if not (name or phone or activity):
        return None
    return {
        "name": name, "phone": phone, "activity": activity,
        "events": json.loads(events or "[]"),
        "first_seen": first_seen, "last_seen": last_seen,
    }


def upcoming_appointment(chat_id: int) -> tuple[str, str] | None:
    """Prochain RDV à venir du client (label, topic) ou None."""
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT slot, topic FROM appointments "
            "WHERE chat_id =? AND slot_iso >=? ORDER BY slot_iso ASC LIMIT 1",
            (str(chat_id), _now()),
        ).fetchone()
    return (row[0], row[1]) if row else None


def client_greeting(bot, chat_id: int, lang: str) -> bool:
    """Salutation personnalisée pour un client déjà connu.

    LETTRE MASTER : « Re-bonjour, content de te revoir » est réservé aux
    clients REVENANTS. Un NOUVEAU client a sa fiche créée dès son 1er
    message (prénom Telegram capturé automatiquement) : il ne doit PAS
    recevoir « Re-bonjour » — il continue vers l'accueil normal.
    Discriminateur : au moins 2 évènements en base = il est déjà venu."""
    client = get_client(chat_id)
    if not client or not client["name"]:
        return False
    if len(client.get("events") or []) < 2:
        return False
    bot.send_message(
        chat_id,
        t(lang, "known_greeting", name=client["name"],
           n_events=len(client["events"])),
    )
    return True


def admin_list_rdv(bot, chat_id: int, lang: str) -> bool:
    """Admin : /rdvs — tous les RDV à venir, triés par date."""
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT a.slot, a.name, a.topic, COALESCE(c.phone, '') "
            "FROM appointments a LEFT JOIN clients c ON a.chat_id = c.chat_id "
            "WHERE a.slot_iso >=? ORDER BY a.slot_iso ASC LIMIT 20",
            (_now(),),
        ).fetchall()
    if not rows:
        bot.send_message(chat_id, "📭 Aucun RDV à venir")
        return True
    lines = [
        f"📅 {slot}\n👤 {name} — {topic}" + (f" ({phone})" if phone else "")
        for slot, name, topic, phone in rows
    ]
    bot.send_message(chat_id, "📅 RDV à venir :\n\n" + "\n\n".join(lines))
    return True


def admin_list_clients(bot, chat_id: int, lang: str) -> bool:
    """Admin : /clients — fiches clients connues avec historique."""
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT chat_id, name, phone, activity, events FROM clients "
            "ORDER BY last_seen DESC LIMIT 15"
        ).fetchall()
    if not rows:
        bot.send_message(chat_id, "📭 Aucun client enregistré pour l'instant")
        return True
    lines = []
    for cid, name, phone, activity, events in rows:
        ev = json.loads(events or "[]")
        lines.append(
            f"👤 {name or '(sans nom)'} — {phone or 'sans numéro'}\n"
            f"   {activity or 'activité inconnue'} — {len(ev)} évènement(s)\n"
            + ("\n   • " + "\n   • ".join(ev[-3:]) if ev else "")
        )
    bot.send_message(chat_id, "🧠 Clients connus :\n\n" + "\n\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# Suivi de commande (client) + gestion (admin)
# ---------------------------------------------------------------------------

def order_tracking(bot, chat_id: int, lang: str) -> bool:
    """Statut de la dernière commande du client, depuis la base locale."""
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT id, service, status, created_at FROM orders "
            "WHERE chat_id =? AND status != 'annulé' ORDER BY id DESC LIMIT 3",
            (str(chat_id),),
        ).fetchall()
    if not rows:
        bot.send_message(chat_id, t(lang, "tracking_none"))
        return True

    lines = []
    for oid, service, status, created in rows:
        label = ORDER_STATUSES.get((status or "en attente").lower(), f"🕐 {status}")
        lines.append(f"📌 {t(lang, 'tracking_order')} n°{oid} — {service}\n   {label} ({t(lang, 'tracking_since')} {created[:10]})")
    bot.send_message(chat_id, t(lang, "tracking_head") + "\n\n" + "\n\n".join(lines))
    return True


def admin_update_order(bot, chat_id: int, args: str, lang: str) -> bool:
    """Admin : /maj <id> <statut> — attente | cours | livre | annule."""
    parts = args.split()
    if len(parts) < 2 or not parts[0].isdigit():
        bot.send_message(
            chat_id,
            "Usage : /maj <id> <statut>\nStatuts : attente, cours, livre, annule\n"
            "Exemple : /maj 5 cours",
        )
        return True
    order_id, raw_status = parts[0], " ".join(parts[1:]).lower()
    if raw_status not in ORDER_STATUSES:
        bot.send_message(chat_id, f"❌ Statut inconnu '{raw_status}'. Statuts : attente, cours, livre, annule")
        return True

    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT chat_id, name, service FROM orders WHERE id =?", (order_id,)
        ).fetchone()
        if not row:
            bot.send_message(chat_id, f"❌ Commande n°{order_id} introuvable")
            return True
        DB_CONN.execute("UPDATE orders SET status =? WHERE id =?", (raw_status, order_id))
        DB_CONN.commit()
    client_id, name, service = row
    bot.send_message(
        chat_id,
        f"✅ Commande n°{order_id} → {ORDER_STATUSES[raw_status]}\n"
        f"👤 {name} | 🛠️ {service}",
    )
    # Le client est informé automatiquement
    if str(chat_id) != str(client_id):
        try:
            bot.send_message(
                int(client_id),
                f"📦 Mise à jour de ta commande n°{order_id} ({service}) :\n"
                f"{ORDER_STATUSES[raw_status]}\nMerci pour ta confiance 🙏",
            )
        except Exception as exc:
            logger.warning("Notification client %s échouée : %s", client_id, exc)
    return True


def admin_list_orders(bot, chat_id: int, lang: str) -> bool:
    """Admin : /commandes — 10 dernières commandes avec statut."""
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT id, name, service, status, created_at FROM orders ORDER BY id DESC LIMIT 10"
        ).fetchall()
    if not rows:
        bot.send_message(chat_id, "📭 Aucune commande enregistrée")
        return True
    lines = [
        f"n°{oid} — {name} — {service} — {ORDER_STATUSES.get((status or 'en attente').lower(), status)} ({created[:10]})"
        for oid, name, service, status, created in rows
    ]
    bot.send_message(chat_id, "📋 Dernières commandes :\n\n" + "\n".join(lines) +
                     "\n\nMise à jour : /maj <id> <statut>")
    return True


# ---------------------------------------------------------------------------
# Codes promo (admin crée, client applique au devis)
# ---------------------------------------------------------------------------

def admin_promo(bot, chat_id: int, args: str, lang: str) -> bool:
    """Admin : /promo CODE 10 [max_uses] | /promo off CODE | /promos."""
    parts = args.split()
    # Lot 20 : /promo seul → promo GLOBALE -30% (catalogue + notification
    # clients). /promo -25% ou /promo 25 → globale -25%. /promo off (sans
    # code) → arrête la promo globale.
    if not parts:
        return set_global_promo(bot, 30.0, "PROMO")
    if parts[0].lower() in {"off", "stop"} and len(parts) == 1:
        return clear_global_promo(bot)
    _p0 = parts[0].replace("%", "").replace(",", ".")
    try:
        _pct = abs(float(_p0))  # « -25% » ou « 25 » → -25%
        if 0 < _pct <= 90:
            return set_global_promo(bot, _pct, "PROMO")
    except ValueError:
        pass  # → création de code promotionnel (comportement historique)

    if parts[0].lower() == "off" and len(parts) >= 2:
        code = parts[1].upper()
        with DB_LOCK:
            cur = DB_CONN.execute("UPDATE promo_codes SET active = 0 WHERE code =?", (code,))
            DB_CONN.commit()
        bot.send_message(
            chat_id, f"✅ Code {code} désactivé" if cur.rowcount else f"❌ Code {code} introuvable"
        )
        return True

    if len(parts) < 2:
        bot.send_message(chat_id, "Usage : /promo CODE <pourcentage> [max_uses]")
        return True

    code = parts[0].upper()
    try:
        pct = float(parts[1].replace(",", "."))
        max_uses = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        bot.send_message(chat_id, "❌ Pourcentage ou max invalide. Exemple : /promo RAMADAN 15 100")
        return True
    if not (0 < pct <= 90):
        bot.send_message(chat_id, "❌ Le pourcentage doit être entre 1 et 90")
        return True

    with DB_LOCK:
        DB_CONN.execute(
            "INSERT OR REPLACE INTO promo_codes (code, discount_pct, active, uses, max_uses, created_at) "
            "VALUES (?,?,1,0,?,?)",
            (code, pct, max_uses, _now()),
        )
        DB_CONN.commit()
    bot.send_message(
        chat_id,
        f"🎟️ Code promo créé : *{code}* (-{pct:g}%)" + (f", max {max_uses} utilisations" if max_uses else ""),
        parse_mode="Markdown",
    )
    return True


def admin_list_promos(bot, chat_id: int, lang: str) -> bool:
    """Admin : /promos — tous les codes avec leur usage."""
    with DB_LOCK:
        rows = DB_CONN.execute(
            "SELECT code, discount_pct, active, uses, max_uses FROM promo_codes ORDER BY code"
        ).fetchall()
    if not rows:
        bot.send_message(chat_id, "🎟️ Aucun code promo. Créer : /promo CODE 10")
        return True
    lines = [
        f"{'✅' if active else '⛔'} {code} : -{pct:g}% — {uses} utilisation(s)"
        + (f" / {max_uses}" if max_uses else "")
        for code, pct, active, uses, max_uses in rows
    ]
    bot.send_message(chat_id, "🎟️ Codes promo :\n\n" + "\n".join(lines))
    return True


PARRAIN_PCT = 10  # remise offerte au filleul

PARRAINAGE_TEXTS = {
    "fr": "🎁 Ton code de parrainage : *{code}*\n\nPartage-le : ton ami obtient *-{pct}%* sur son devis, et tu gagnes un crédit offert à chaque utilisation 🚀",
    "en": "🎁 Your referral code: *{code}*\n\nShare it: your friend gets *-{pct}%* on their quote, and you earn a free credit each time 🚀",
    "es": "🎁 Tu código de referido: *{code}*\n\nCompártelo: tu amigo obtiene *-{pct}%* en su presupuesto, y tú ganas un crédito cada vez 🚀",
    "ar": "🎁 رمز الإحالة الخاص بك: *{code}*\n\nشاركه: صديقك يحصل على *-{pct}%* على عرضه، وأنت تكسب رصيدا مجانيا 🚀",
}


def _get_or_create_referral(chat_id: int) -> str:
    """Code de parrainage unique du client (créé au besoin)."""
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT code FROM referrals WHERE owner_chat =?", (str(chat_id),)
        ).fetchone()
        if row:
            return row[0]
        import secrets
        code = "KA-" + secrets.token_hex(3).upper()
        DB_CONN.execute(
            "INSERT INTO referrals (code, owner_chat, credits, created_at) VALUES (?,?,0,?)",
            (code, str(chat_id), _now()),
        )
        DB_CONN.commit()
        return code


def _parrainage_reply(bot, chat_id: int, lang: str) -> bool:
    code = _get_or_create_referral(chat_id)
    txt = PARRAINAGE_TEXTS.get(lang, PARRAINAGE_TEXTS["fr"])
    bot.send_message(chat_id, txt.format(code=code, pct=PARRAIN_PCT),
                    parse_mode="Markdown")
    return True


def _referral_as_promo(bot, chat_id: int, text: str):
    """Code de parrainage d'un AUTRE client → remise filleul + crédit au parrain."""
    code = text.strip().upper()
    if not code.startswith("KA-"):
        return None
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT owner_chat FROM referrals WHERE code =?", (code,)
        ).fetchone()
        if not row or row[0] == str(chat_id):
            return None
        already = DB_CONN.execute(
            "SELECT 1 FROM referral_uses WHERE code =? AND new_chat =?",
            (code, str(chat_id)),
        ).fetchone()
        if already:
            return None
        DB_CONN.execute(
            "INSERT INTO referral_uses (code, new_chat, created_at) VALUES (?,?,?)",
            (code, str(chat_id), _now()),
        )
        DB_CONN.execute(
            "UPDATE referrals SET credits = credits + 1 WHERE code =?", (code,)
        )
        DB_CONN.commit()
    owner = row[0]
    notify_admin(
        bot,
        f"🎁 Parrainage utilisé : code {code} par le client {chat_id} "
        f"— parrain #{owner} crédité (+1)",
    )
    return (code, PARRAIN_PCT)


def check_promo_code(code: str):
    """Valide un code promo. Retourne (code, pct) ou None."""
    code = code.strip().upper()
    if not code:
        return None
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT code, discount_pct, max_uses, uses FROM promo_codes "
            "WHERE code =? AND active = 1",
            (code,),
        ).fetchone()
    if not row:
        return None
    _, pct, max_uses, uses = row
    if max_uses and uses >= max_uses:
        return None
    return code, pct


def apply_discount(price: str, pct: float) -> str:
    """Réduit tous les montants 'NNN€' d'une chaîne prix de pct%."""
    import re as _re

    def _reduce(match: "_re.Match") -> str:
        amount = float(match.group(1))
        reduced = int(amount * (100 - pct) / 100 + 0.5)  # arrondi commercial
        return f"{reduced}€"

    return _re.sub(r"(\d+(?:\.\d+)?)€", _reduce, price)

# ---------------------------------------------------------------------------
# Message hors horaires (1 fois par jour et par client)
# ---------------------------------------------------------------------------

def is_off_hours() -> bool:
    """Vrai si l'agence est fermée : week-end (optionnel) ou hors horaires."""
    now = _agency_now()
    if WEEKEND_OFF and now.weekday() >= 5:
        return True
    current = now.hour + now.minute / 60
    return current < WORK_START or current >= WORK_END


# 🚫 Règle d'or (29/09) : jamais 2 messages à la fois. La notice « bureau
# fermé » n'est donc plus envoyée seule : elle est mise en attente ici puis
# FUSIONNÉE dans le TOUT PROCHAIN message envoyé à ce chat (voir le wrapper
# bot.send_message posé dans rag_bot.py juste après la création du bot).
_PENDING_OFFHOURS: dict[str, str] = {}

def maybe_off_hours_notice(bot, chat_id: int, lang: str) -> None:
    """Prépare (1x/jour) la notice 'bureau fermé', fusionnée au prochain
    message — jamais envoyée comme message séparé."""
    if not is_off_hours():
        return
    today = _agency_now().strftime("%Y-%m-%d")
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT 1 FROM offhours_notified WHERE chat_id =? AND day =?",
            (str(chat_id), today),
        ).fetchone()
        if row:
            return
        DB_CONN.execute(
            "INSERT OR REPLACE INTO offhours_notified (chat_id, day) VALUES (?,?)",
            (str(chat_id), today),
        )
        DB_CONN.commit()
    hours = f"{_fmt_hour(WORK_START)}-{_fmt_hour(WORK_END)}"
    _PENDING_OFFHOURS[str(chat_id)] = t(lang, "off_hours", hours=hours)


def export_csv(bot, chat_id: int, lang: str) -> bool:
    """Export CSV des leads/commandes/RDV/devis, envoyé en document Telegram."""
    import csv
    import tempfile

    if not ADMIN_CHAT_ID or chat_id != ADMIN_CHAT_ID:
        bot.send_message(chat_id, t(lang, "admin_only"))
        return True

    bot.send_message(chat_id, t(lang, "export_sent"))

    tables = {
        "leads": ["id", "chat_id", "name", "phone", "sector", "need", "budget", "created_at"],
        "orders": ["id", "chat_id", "service", "activity", "deadline", "name", "phone", "created_at"],
        "appointments": ["id", "chat_id", "name", "topic", "slot", "created_at"],
        "quotes": ["id", "chat_id", "service", "price", "delay", "details", "created_at"],
    }

    stamp = _agency_now().strftime("%Y%m%d_%H%M")
    exported = 0
    for table, cols in tables.items():
        with DB_LOCK:
            rows = DB_CONN.execute(
                f"SELECT {', '.join(cols)} FROM {table} ORDER BY id DESC"
            ).fetchall()
        if not rows:
            continue
        exported += len(rows)
        fd, path = tempfile.mkstemp(prefix=f"komara_{table}_{stamp}_", suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:  # BOM = Excel FR OK
            writer = csv.writer(handle)
            writer.writerow(cols)
            writer.writerows(rows)
        try:
            with open(path, "rb") as doc:
                bot.send_document(chat_id, doc, visible_filename=f"{table}_{stamp}.csv")
        except Exception as exc:
            logger.warning("Export %s échoué : %s", table, exc)
        finally:
            os.unlink(path)

    if not exported:
        bot.send_message(chat_id, "📭 Aucune donnée à exporter pour le moment.")
    else:
        logger.info("Export CSV envoyé à l'admin : %d lignes", exported)
    return True

# ---------------------------------------------------------------------------
# File de rappels : thread d'arrière-plan
# ---------------------------------------------------------------------------

def start_background(bot) -> None:
    """Démarre la boucle d'envoi des rappels programmés (J+1, avant-RDV...)."""

    def loop() -> None:
        while True:
            try:
                if DB_CONN is not None:
                    with DB_LOCK:
                        rows = DB_CONN.execute(
                            "SELECT id, chat_id, message FROM followups "
                            "WHERE sent = 0 AND due_at <= ?",
                            (_now(),),
                        ).fetchall()
                    for followup_id, chat_id, message in rows:
                        try:
                            bot.send_message(int(chat_id), message)
                        except Exception as exc:
                            logger.warning("Envoi rappel %s échoué : %s", followup_id, exc)
                        finally:
                            with DB_LOCK:
                                DB_CONN.execute(
                                    "UPDATE followups SET sent = 1 WHERE id =?", (followup_id,)
                                )
                                DB_CONN.commit()
                    if rows:
                        logger.info("%d rappel(s) envoyé(s)", len(rows))
            except Exception as exc:
                logger.warning("Boucle rappels : %s", exc)
            time.sleep(FOLLOWUP_LOOP_INTERVAL)

    threading.Thread(target=loop, name="komara-followups", daemon=True).start()
    logger.info("Boucle de rappels démarrée (intervalle %ss)", FOLLOWUP_LOOP_INTERVAL)


init_db()
