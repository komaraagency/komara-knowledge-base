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
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

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
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "0"))
WORK_START = int(os.getenv("WORK_START", "9"))
WORK_END = int(os.getenv("WORK_END", "18"))
WEEKEND_OFF = os.getenv("WEEKEND_OFF", "true").lower() in {"1", "true", "yes", "on"}

# Grille de prix alignée sur kb.json (monnaies 100% €)
PRICE_GRID: list[tuple[str, str, str, str]] = [
    ("1", "Agent IA WhatsApp/Telegram", "300€ installation + 90€/mois maintenance", "3-5 jours"),
    ("2", "Site web vitrine", "à partir de 250€", "1-2 semaines"),
    ("3", "Logo professionnel", "à partir de 150€", "2-3 jours"),
    ("4", "Application web", "sur devis (gratuit sous 24h)", "2-4 semaines"),
    ("5", "Visuels & vidéo IA", "sur devis", "selon projet"),
]

SURVEY_QUESTIONS = [
    {"id": "satisfaction", "kind": "rate", "fr": "Sur 1 à 5, comment notes-tu ton expérience avec Komara Agency 🇬🇳 ?"},
    {"id": "reco", "kind": "bool", "fr": "Recommanderais-tu Komara Agency à un proche ? (oui/non)"},
    {"id": "comment", "kind": "text", "fr": "Un commentaire pour l'équipe ? (ou tape 'passer')"},
]

CANCEL_WORDS = {"annuler", "cancel", "stop", "quitter", "إلغاء", "Cancelar"}

TRIGGERS: dict[str, set[str]] = {
    "order": {
        "🚀 Commander", "🚀 Order", "🚀 طلب", "🚀 Ordenar",
        "/commander", "commander", "je veux commander", "passer commande", "passer une commande",
        "i want to order",
    },
    "rdv": {
        "📅 Rendez-vous", "📅 Book a call", "📅 Reservar",
        "/rdv", "rdv", "rendez-vous", "prendre rendez", "réserver un appel", "réserver un rendez",
        "book a call", "reservar",
    },
    "devis": {
        "📄 Devis", "📄 Quote", "📄 Presupuesto",
        "/devis", "devis", "avoir un devis", "demander un devis", "un devis", "quote", "presupuesto",
    },
    "lead": {
        "📞 Être rappelé", "être rappelé", "etre rappelé", "être rappelé(e)",
        "rappelez-moi", "rappel", "on m'appelle", "call me back",
    },
    "survey": {
        "⭐ Avis", "⭐ Feedback", "⭐ Opinión",
        "/sondage", "sondage", "donner mon avis", "laisser un avis", "mon avis",
    },
}

ADMIN_COMMANDS = {"/stats", "/rapport", "/export"}

# ---------------------------------------------------------------------------
# Textes des flux (fr complet, en/es essentiels, ar → fr)
# ---------------------------------------------------------------------------

T = {
    "fr": {
        "cancelled": "OK, on annule 🚫\nTape 'commander' quand tu veux relancer 🚀",
        "invalid": "Je n'ai pas compris ça 😅 Réessaie, ou tape 'annuler'.",
        "invalid_rate": "Tape un chiffre entre 1 et 5 👇",
        "invalid_bool": "Réponds 'oui' ou 'non' 👇",
        "invalid_slot": "Tape le numéro du créneau (1 à 6) 👇",
        "invalid_service": "Tape le numéro du service (1 à 5) 👇",
        "order_start": "🛒 Commande express Komara Agency 🇬🇳\n\nQuel service veux-tu ?\n1️⃣ Agent IA WhatsApp/Telegram\n2️⃣ Site web vitrine\n3️⃣ Logo professionnel\n4️⃣ Application web\n5️⃣ Visuels & vidéo IA\n\nTape le numéro 👇",
        "order_activity": "Parfait : {service} 🔥\nC'est quoi ton activité ? (restaurant, boutique, immo, clinique...)",
        "order_deadline": "Nice 👌 Dans quel délai ? (ex: cette semaine, ce mois)",
        "order_name": "Top 🎯 Ton nom ou prénom ?",
        "order_phone": "Dernière étape 💪 Ton numéro WhatsApp ?",
        "order_done": "✅ Commande enregistrée !\n\n{recap}\n\n🔥 Pour bloquer ton projet :\nÉcris *JE COMMENCE* au {whatsapp}\n\nKomara Agency 🇬🇳 te confirme tout sous 24h.",
        "rdv_start": "📅 RDV avec Komara Agency 🇬🇳\n\nTon nom ou prénom ?",
        "rdv_topic": "C'est pour quel sujet ? (ex: bot pour ma boutique)",
        "rdv_slot": "Choisis ton créneau (appel 15 min) :\n{slots}\n\nTape le numéro 👇",
        "rdv_done": "✅ RDV réservé !\n\n👤 {name}\n📝 {topic}\n📅 {slot}\n\nJe te rappelle 1h avant. Si tu dois changer, écris ici 👍",
        "lead_start": "📞 OK, on t'appelle !\n\nTon nom ou prénom ?",
        "lead_phone": "Ton numéro WhatsApp ?",
        "lead_sector": "Ton secteur d'activité ? (resto, mode, immo, santé...)",
        "lead_need": "Ton besoin principal ? (ex: vendre sur WhatsApp, site vitrine)",
        "lead_done": "✅ Noté ! Komara Agency 🇬🇳 t'appelle sous 24h ouvrées 💪\nEn attendant : {whatsapp}",
        "devis_start": "📄 Devis instantané Komara Agency 🇬🇳\n\nQuel service ?\n{grid}\n\nTape le numéro 👇",
        "devis_details": "{service}\n💰 {price}\n⏱️ Livraison : {delay}\n\nDécris ton besoin en 1-2 phrases 👇",
        "devis_done": "📄 Ton devis express :\n\n🛠️ Service : {service}\n💰 {price}\n⏱️ Livraison : {delay}\n📝 Détails : {details}\n\n✅ Pour lancer : écris *JE COMMENCE* au {whatsapp}\nUn humain confirme le devis final sous 24h.",
        "survey_start": "⭐ Ton avis compte !\n\n{question}",
        "survey_next": "Merci 👍\n\n{question}",
        "survey_done": "Merci beaucoup 🙏 Ton avis est enregistré.\nTu veux lancer un projet ? Tape 'commander' 🚀",
        "admin_stats": "📊 Stats Komara Agency\n\n🛒 Commandes : {orders}\n📅 RDV : {rdv}\n📞 Leads : {leads}\n📄 Devis : {quotes}\n⭐ Sondages : {surveys}\n😀 Satisfaction moyenne : {satisfaction}/5\n👍 Recommandent : {reco}%\n\n❓ Questions non reconnues : {unrecognized}",
        "admin_report": "📋 Rapport questions sans réponse (top {limit}) :\n\n{items}\n\n→ À intégrer dans kb.json pour améliorer le bot.",
        "admin_only": "🔒 Commande réservée à l'administration.",
        "off_hours": "🌙 Komara Agency 🇬🇳 est fermée en ce moment.\nBureau ouvert : {hours} (lun-ven).\n\nPas de stress : je prends ta commande et tes questions 24/7, un humain te répond à l'ouverture 👍",
        "export_sent": "📤 Export en cours...",
    },
    "en": {
        "cancelled": "OK, cancelled 🚫\nType 'order' whenever you're ready 🚀",
        "invalid": "I didn't get that 😅 Try again, or type 'cancel'.",
        "invalid_rate": "Type a number between 1 and 5 👇",
        "invalid_bool": "Answer 'yes' or 'no' 👇",
        "invalid_slot": "Type the slot number (1 to 6) 👇",
        "invalid_service": "Type the service number (1 to 5) 👇",
        "order_start": "🛒 Express order — Komara Agency 🇬🇳\n\nWhich service?\n1️⃣ AI Agent WhatsApp/Telegram\n2️⃣ Website\n3️⃣ Logo\n4️⃣ Web app\n5️⃣ AI visuals & video\n\nType the number 👇",
        "order_activity": "Great: {service} 🔥\nWhat's your business? (restaurant, shop, real estate...)",
        "order_deadline": "Nice 👌 What's your timeline? (e.g. this week, this month)",
        "order_name": "Perfect 🎯 Your name?",
        "order_phone": "Last step 💪 Your WhatsApp number?",
        "order_done": "✅ Order saved!\n\n{recap}\n\n🔥 To lock your project:\nWrite *I START* to {whatsapp}\n\nKomara Agency 🇬🇳 confirms everything within 24h.",
        "rdv_start": "📅 Book a call — Komara Agency 🇬🇳\n\nYour name?",
        "rdv_topic": "What's the topic? (e.g. bot for my shop)",
        "rdv_slot": "Pick your slot (15-min call):\n{slots}\n\nType the number 👇",
        "rdv_done": "✅ Call booked!\n\n👤 {name}\n📝 {topic}\n📅 {slot}\n\nI'll remind you 1h before 👍",
        "lead_start": "📞 OK, we'll call you!\n\nYour name?",
        "lead_phone": "Your WhatsApp number?",
        "lead_sector": "Your sector? (food, fashion, real estate...)",
        "lead_need": "Your main need? (e.g. sell on WhatsApp)",
        "lead_done": "✅ Got it! Komara Agency 🇬🇳 calls you within 24h 💪\nMeanwhile: {whatsapp}",
        "devis_start": "📄 Instant quote — Komara Agency 🇬🇳\n\nWhich service?\n{grid}\n\nType the number 👇",
        "devis_details": "{service}\n💰 {price}\n⏱️ Delivery: {delay}\n\nDescribe your need in 1-2 sentences 👇",
        "devis_done": "📄 Your express quote:\n\n🛠️ Service: {service}\n💰 {price}\n⏱️ Delivery: {delay}\n📝 Details: {details}\n\n✅ To start: write *I START* to {whatsapp}\nA human confirms the final quote within 24h.",
        "survey_start": "⭐ Your feedback matters!\n\n{question}",
        "survey_next": "Thanks 👍\n\n{question}",
        "survey_done": "Thank you so much 🙏 Your feedback is saved.\nWant to start a project? Type 'order' 🚀",
        "admin_stats": "📊 Komara Agency Stats\n\n🛒 Orders: {orders}\n📅 Calls: {rdv}\n📞 Leads: {leads}\n📄 Quotes: {quotes}\n⭐ Surveys: {surveys}\n😀 Avg satisfaction: {satisfaction}/5\n👍 Would recommend: {reco}%",
        "admin_report": "📋 Unanswered questions report (top {limit}):\n\n{items}\n\n→ Add to kb.json to improve the bot.",
        "admin_only": "🔒 Admin-only command.",
        "off_hours": "🌙 Komara Agency 🇬🇳 is closed right now.\nOffice hours: {hours} (Mon-Fri).\n\nNo worries: I take your order and questions 24/7, a human replies at opening 👍",
        "export_sent": "📤 Exporting...",
    },
    "es": {
        "cancelled": "OK, cancelado 🚫\nEscribe 'ordenar' cuando quieras 🚀",
        "invalid": "No entendí 😅 Intenta de nuevo, o escribe 'cancelar'.",
        "invalid_rate": "Escribe un número del 1 al 5 👇",
        "invalid_bool": "Responde 'sí' o 'no' 👇",
        "invalid_slot": "Escribe el número de la franja (1 a 6) 👇",
        "invalid_service": "Escribe el número del servicio (1 a 5) 👇",
        "order_start": "🛒 Pedido express — Komara Agency 🇬🇳\n\n¿Qué servicio?\n1️⃣ Agente IA WhatsApp/Telegram\n2️⃣ Sitio web\n3️⃣ Logo\n4️⃣ App web\n5️⃣ Visuales y video IA\n\nEscribe el número 👇",
        "order_activity": "Perfecto: {service} 🔥\n¿Cuál es tu negocio? (restaurante, tienda...)",
        "order_deadline": "Bien 👌 ¿En qué plazo? (esta semana, este mes)",
        "order_name": "Genial 🎯 ¿Tu nombre?",
        "order_phone": "Último paso 💪 ¿Tu número de WhatsApp?",
        "order_done": "✅ ¡Pedido guardado!\n\n{recap}\n\n🔥 Para bloquear tu proyecto:\nEscribe *EMPIEZO* al {whatsapp}\n\nKomara Agency 🇬🇳 confirma todo en 24h.",
        "rdv_start": "📅 Reservar llamada — Komara Agency 🇬🇳\n\n¿Tu nombre?",
        "rdv_topic": "¿Sobre qué tema? (ej: bot para mi tienda)",
        "rdv_slot": "Elige tu franja (llamada de 15 min):\n{slots}\n\nEscribe el número 👇",
        "rdv_done": "✅ ¡Llamada reservada!\n\n👤 {name}\n📝 {topic}\n📅 {slot}\n\nTe recuerdo 1h antes 👍",
        "lead_start": "📞 ¡Ok, te llamamos!\n\n¿Tu nombre?",
        "lead_phone": "¿Tu número de WhatsApp?",
        "lead_sector": "¿Tu sector? (comida, moda, inmobiliaria...)",
        "lead_need": "¿Tu necesidad principal? (ej: vender por WhatsApp)",
        "lead_done": "✅ ¡Anotado! Komara Agency 🇬🇳 te llama en 24h 💪\nMientras: {whatsapp}",
        "devis_start": "📄 Presupuesto instantáneo — Komara Agency 🇬🇳\n\n¿Qué servicio?\n{grid}\n\nEscribe el número 👇",
        "devis_details": "{service}\n💰 {price}\n⏱️ Entrega: {delay}\n\nDescribe tu necesidad en 1-2 frases 👇",
        "devis_done": "📄 Tu presupuesto express:\n\n🛠️ Servicio: {service}\n💰 {price}\n⏱️ Entrega: {delay}\n📝 Detalles: {details}\n\n✅ Para empezar: escribe *EMPIEZO* al {whatsapp}\nUn humano confirma el presupuesto final en 24h.",
        "survey_start": "⭐ ¡Tu opinión cuenta!\n\n{question}",
        "survey_next": "Gracias 👍\n\n{question}",
        "survey_done": "Muchas gracias 🙏 Tu opinión está guardada.\n¿Quieres empezar un proyecto? Escribe 'ordenar' 🚀",
        "admin_stats": "📊 Estadísticas Komara Agency\n\n🛒 Pedidos: {orders}\n📅 Llamadas: {rdv}\n📞 Leads: {leads}\n📄 Presupuestos: {quotes}\n⭐ Encuestas: {surveys}\n😀 Satisfacción media: {satisfaction}/5\n👍 Recomendarían: {reco}%",
        "admin_report": "📋 Informe de preguntas sin respuesta (top {limit}):\n\n{items}\n\n→ Añadir a kb.json para mejorar el bot.",
        "admin_only": "🔒 Comando solo para administración.",
        "off_hours": "🌙 Komara Agency 🇬🇳 está cerrada ahora.\nHorario: {hours} (lun-vie).\n\nTranquilo: tomo tu pedido y preguntas 24/7, un humano responde a la apertura 👍",
        "export_sent": "📤 Exportando...",
    },
}


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
                name TEXT, phone TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, name TEXT, topic TEXT,
                slot TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, service TEXT, price TEXT,
                delay TEXT, details TEXT, created_at TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT, message TEXT, due_at TEXT NOT NULL,
                sent INTEGER DEFAULT 0, created_at TEXT NOT NULL
            );
        """)
        DB_CONN.commit()
    logger.info("Base actions SQLite initialisée : %s", ACTIONS_DB)


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
        return cur.lastrowid or 0


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


def _gen_slots() -> list[tuple[str, str]]:
    """6 créneaux : 3 prochains jours ouvrés × 10h/15h."""
    slots = []
    day = datetime.now()
    while len(slots) < 6:
        day += timedelta(days=1)
        if day.weekday() >= 5:  # samedi/dimanche
            continue
        for hour in (10, 15):
            slot_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            label = slot_dt.strftime("%a %d/%m à %Hh")
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

    text_clean = text.strip()

    # 0. Commandes admin
    low = text_clean.lower()
    if low in ADMIN_COMMANDS:
        return _admin_command(bot, chat_id, low, lang)

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
            return start_flow(bot, chat_id, flow, lang)

    # 3. Étape d'un flux actif
    active = _fetch_flow(chat_id)
    if active:
        flow, step, data = active
        return _advance_flow(bot, chat_id, flow, step, data, text_clean, lang)

    return False


def start_flow(bot, chat_id: int, flow: str, lang: str) -> bool:
    """Démarre un flux : premier message envoyé au client."""
    if flow == "order":
        _save_flow(chat_id, "order", "service", {})
        bot.send_message(chat_id, t(lang, "order_start"))
    elif flow == "rdv":
        _save_flow(chat_id, "rdv", "name", {})
        bot.send_message(chat_id, t(lang, "rdv_start"))
    elif flow == "devis":
        grid = "\n".join(
            f"{n}️⃣ {name} — {price}" for n, name, price, _ in PRICE_GRID
        )
        _save_flow(chat_id, "devis", "service", {})
        bot.send_message(chat_id, t(lang, "devis_start", grid=grid))
    elif flow == "lead":
        _save_flow(chat_id, "lead", "name", {})
        bot.send_message(chat_id, t(lang, "lead_start"))
    elif flow == "survey":
        _save_flow(chat_id, "survey", "0", {})
        bot.send_message(chat_id, t(lang, "survey_start", question=SURVEY_QUESTIONS[0]["fr"]))
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
    _clear_flow(chat_id)
    return False


def _step_order(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    if step == "service":
        digits = text.strip()
        service = _service_by_num(digits)
        if not service:
            bot.send_message(chat_id, t(lang, "invalid_service"))
            return True
        data["service"] = service[1]
        _save_flow(chat_id, "order", "activity", data)
        bot.send_message(chat_id, t(lang, "order_activity", service=service[1]))
        return True

    if step == "activity":
        data["activity"] = text[:200]
        _save_flow(chat_id, "order", "deadline", data)
        bot.send_message(chat_id, t(lang, "order_deadline"))
        return True

    if step == "deadline":
        data["deadline"] = text[:100]
        _save_flow(chat_id, "order", "name", data)
        bot.send_message(chat_id, t(lang, "order_name"))
        return True

    if step == "name":
        data["name"] = text[:100]
        _save_flow(chat_id, "order", "phone", data)
        bot.send_message(chat_id, t(lang, "order_phone"))
        return True

    if step == "phone":
        data["phone"] = text[:50]
        _clear_flow(chat_id)

        _insert("orders", {
            "chat_id": str(chat_id), "service": data.get("service", ""),
            "activity": data.get("activity", ""), "deadline": data.get("deadline", ""),
            "name": data.get("name", ""), "phone": data.get("phone", ""),
            "created_at": _now(),
        })
        _insert("leads", {
            "chat_id": str(chat_id), "name": data.get("name", ""),
            "phone": data.get("phone", ""), "sector": data.get("activity", ""),
            "need": data.get("service", ""), "budget": data.get("deadline", ""),
            "created_at": _now(),
        })

        recap = (
            f"🛠️ Service : {data.get('service','')}\n"
            f"💼 Activité : {data.get('activity','')}\n"
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
        slots = _gen_slots()
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
            "topic": data.get("topic", ""), "slot": label, "created_at": _now(),
        })
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


def _step_devis(bot, chat_id: int, step: str, data: dict, text: str, lang: str) -> bool:
    if step == "service":
        digits = text.strip()
        service = _service_by_num(digits)
        if not service:
            bot.send_message(chat_id, t(lang, "invalid_service"))
            return True
        _, name, price, delay = service
        data.update({"service": name, "price": price, "delay": delay})
        _save_flow(chat_id, "devis", "details", data)
        bot.send_message(chat_id, t(lang, "devis_details", service=name, price=price, delay=delay))
        return True

    if step == "details":
        data["details"] = text[:500]
        _clear_flow(chat_id)
        _insert("quotes", {
            "chat_id": str(chat_id), "service": data.get("service", ""),
            "price": data.get("price", ""), "delay": data.get("delay", ""),
            "details": data.get("details", ""), "created_at": _now(),
        })
        bot.send_message(
            chat_id,
            t(
                lang, "devis_done",
                service=data.get("service", ""), price=data.get("price", ""),
                delay=data.get("delay", ""), details=data.get("details", ""),
                whatsapp=WHATSAPP_FALLBACK,
            ),
            parse_mode="Markdown",
        )
        notify_admin(
            bot,
            f"📄 DEVIS EXPRESS\n🛠️ {data.get('service','')}\n📝 {data.get('details','')}\n👤 chat_id: {chat_id}",
        )
        return True

    _clear_flow(chat_id)
    return False


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
    elif kind == "text" and low in {"passer", "skip", "نم"}:
        answer = ""

    _insert("survey_answers", {
        "chat_id": str(chat_id), "question_id": question["id"],
        "answer": answer[:500], "created_at": _now(),
    })

    nxt = idx + 1
    if nxt < len(SURVEY_QUESTIONS):
        _save_flow(chat_id, "survey", str(nxt), data)
        bot.send_message(chat_id, t(lang, "survey_next", question=SURVEY_QUESTIONS[nxt]["fr"]))
    else:
        _clear_flow(chat_id)
        bot.send_message(chat_id, t(lang, "survey_done"))
        notify_admin(bot, f"⭐ NOUVEAU SONDAGE (chat_id {chat_id}) — dernière réponse : {answer[:60]}")
    return True


# ---------------------------------------------------------------------------
# Commandes admin : /stats et /rapport
# ---------------------------------------------------------------------------

def _admin_command(bot, chat_id: int, command: str, lang: str) -> bool:
    if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
        bot.send_message(chat_id, t(lang, "admin_only"))
        return True

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
        return True

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
# Message hors horaires (1 fois par jour et par client)
# ---------------------------------------------------------------------------

def _agency_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)


def is_off_hours() -> bool:
    """Vrai si l'agence est fermée : week-end (optionnel) ou hors 9h-18h."""
    now = _agency_now()
    if WEEKEND_OFF and now.weekday() >= 5:
        return True
    return now.hour < WORK_START or now.hour >= WORK_END


def maybe_off_hours_notice(bot, chat_id: int, lang: str) -> None:
    """Prévient le client (1x/jour) que le bureau est fermé, sans bloquer."""
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
    hours = f"{WORK_START}h-{WORK_END}h"
    bot.send_message(chat_id, t(lang, "off_hours", hours=hours))


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
