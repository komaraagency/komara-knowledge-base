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
from datetime import datetime, timedelta
from pathlib import Path

import catalogue
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
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "0"))


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
    "Logo": "2-3 jours",
    "Visuels": "24-48h",
    "Site web": "1-2 semaines",
    "Agent IA": "3-5 jours",
    "Formation": "selon planning",
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
    "parrainage": {
        "/parrainage", "parrainage", "parrain", "programme de parrainage",
        "referral", "parraine", "je parraine",
    },
    "survey": {
        "⭐ Avis", "⭐ Feedback", "⭐ Opinión",
        "/sondage", "sondage", "donner mon avis", "laisser un avis", "mon avis",
    },
}

ADMIN_COMMANDS = {"/admin", "/msg", "/broadcast", "/pause", "/reprend", "/prend", "/stats", "/rapport", "/export", "/maj", "/update", "/commandes", "/orders", "/promo", "/promos", "/rdvs", "/clients", "/produit", "/produits", "/kb_import", "/google", "/facture", "/backup", "/hebdo"}

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

T = {
    "fr": {
        "complaint_ack": "Merci pour ton message, il a été transmis à l'équipe dirigeante qui examine personnellement ton dossier. Tu seras contacté sous 24h 🙏",
        "human_ask": '🤝 Pas de souci, un expert KOMARA te contacte sous 5 min ⚡\nLaisse-moi ton numéro WhatsApp 👇',
        "human_done": "✅ C'est noté ! L'équipe KOMARA te contacte sur WhatsApp sous 5 min ⚡\nEn attendant, je reste dispo ici 24/7 😊",
        "admin_panel": '🎛️ PANNEAU ADMIN — Komara Agency 🇬🇳\nTout le bot, depuis ton téléphone 👇\n\n💰 ARGENT & RAPPORTS\n📊 /stats — compteurs + argent (devis, mois, commandes)\n📈 /rapport — rapport complet\n🗓️ /hebdo — rapport hebdomadaire\n📤 /export — export des données\n\n👥 CLIENTS & VENTE\n📞 /prend — tes 10 derniers clients + numéros\n👤 /prend <nom> — fiche client complète\n✉️ /msg <id|numéro> <texte> — écrire à un client via le bot\n📢 /broadcast <texte> — promo à tous les clients\n🛒 /commandes — commandes du catalogue\n🧑\u200d🤝\u200d🧑 /clients — liste des clients\n📅 /rdvs — rendez-vous\n\n🛍️ CATALOGUE & PROMOS\n📦 /produit — liste des produits\n➕ /produit add <cat>|<nom>|<desc>|<prix>\n💱 /produit maj <id>|<prix> — changer un prix\n❌ /produit del <id> — retirer un produit\n🎟️ /promo CODE 20 [max] — créer un code (ex : /promo TABASKI20 20 = -20%)\n🚫 /promo off CODE — désactiver un code\n📋 /promos — codes actifs\n\n🤖 PILOTAGE DU BOT\n🔒 /pause — fermer le bot (clients → message de fermeture)\n✅ /reprend — rouvrir le bot\n🔄 /maj — recharger la base de connaissances\n📄 /facture — facture PDF\n💾 /backup — sauvegarde Drive manuelle\n🔗 /google — connexion Google\n📥 /kb_import — importer des fiches\n\n💡 Combo gagnant : /prend pour voir un client, /msg pour lui écrire, /broadcast pour une promo générale. Seul ton ID peut exécuter tout ça 🔐',
        "code_usage": '🎟️ Pour vérifier un code promo : /code TONCODE\nExemple : /code TABASKI20 😊',
        "code_ok": "🎟️ Code {code} VALIDE : -{pct:g}% de réduction ! 🎉\nTape 'devis' ou 'commander' pour en profiter maintenant 🚀",
        "code_bad": "❌ Code {code} invalide, expiré ou déjà trop utilisé.\nVérifie l'orthographe, ou demande un code à l'équipe 😊",
        "msg_usage": '✉️ Écrire à un client via le bot :\n\n/msg <chat_id|numéro|nom> <message>\n\nExemples :\n/msg 123456 Bonjour, votre commande est prête ✅\n/msg Mariama Ton visuel Tabaski est prêt ! 🎨\n\nLes numéros et chat_id sont dans /prend 📞',
        "msg_sent": '✅ Message livré à {target} 👍',
        "msg_failed": "❌ Livraison impossible : ce client n'a peut-être jamais démarré le bot, ou il a bloqué les messages du bot.",
        "msg_not_found": '❌ Aucun client trouvé pour « {q} ».\nTape /prend pour la liste de tes clients 📞',
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
        "bot_closed": "🌙 Komara Agency 🇬🇳 est fermée pour le moment.\nMais pas de stress : laisse ton message ici, on te répond à l'ouverture 🙏\n\nEn attendant, découvre nos réalisations : /menu 😊",
        "pause_on": "🔒 Bot FERMÉ !\n\nLes clients reçoivent maintenant le message de fermeture.\nTape /reprend pour rouvrir quand tu veux.",
        "pause_off": "✅ Bot RÉOUVERT ! 🚀\n\nTous les clients peuvent à nouveau discuter avec moi.",
        "broadcast_usage": "📢 Pour envoyer une promo à TOUS tes clients :\n\n/broadcast Ton message ici\n\nExemple :\n/broadcast 🔥 Promo week-end : -20% sur tous les logos ! Écris-moi pour en profiter 👇",
        "broadcast_done": "📢 Promo envoyée !\n\n✅ Envoyée : {sent} client(s)\n❌ Échecs : {failed}",
        "broadcast_none": "😅 Aucun client enregistré pour le moment.\nDès que des clients discutent avec le bot, ils seront ici.",
        "prend_usage": "📞 Pour voir les numéros de tes clients :\n\n/prend → liste des 10 derniers clients\n/prend <nom, numéro ou chat_id> → fiche complète du client",
        "prend_none": "😅 Aucun client trouvé pour « {q} ».",
        "prend_card": "👤 {name}\n📞 {phone}\n🆔 {chat_id}\n💼 {activity}\n🛒 {orders} commande(s)\n📅 Dernière visite : {last_seen}\n\n👉 Écris-lui directement, ou tape /broadcast pour une promo générale.",
        "prend_list": "📞 Tes 10 derniers clients :\n\n{lines}\n\nPour la fiche complète : /prend <nom ou numéro>",
        "admin_money": "💰 *L'argent — Komara Agency 🇬🇳*\n\n📄 Devis émis : {n} (total ~{total}€)\n📅 Ce mois-ci : {mn} devis (~{mtot}€)\n🛒 Commandes en attente : {pending}\n\nLes devis sont générés par le module et confirmés par l'équipe.",
        "off_hours": "🌙 Komara Agency 🇬🇳 est fermée en ce moment.\nBureau ouvert : {hours} (lun-ven).\n\nPas de stress : je prends ta commande et tes questions 24/7, un humain te répond à l'ouverture 👍",
        "export_sent": "📤 Export en cours...",
        "devis_promo": "🎟️ Tu as un code promo ?\nTape le code, ou 'passer' si tu n'en as pas.",
        "promo_invalid": "❌ Code invalide ou expiré. Tape un code valide, ou 'passer'.",
        "tracking_none": "📦 Pas encore de commande chez nous.\nTape 'commander' pour lancer ton projet 🚀",
        "tracking_head": "📦 Suivi de ta commande :",
        "tracking_order": "Commande",
        "tracking_since": "depuis le",
        "devis_activity": "Super 👍 C'est pour quel type d'activité ?\n(boutique, restaurant, immo, formation...)",
        "devis_deadline": "Et ton délai souhaité ?\n(urgent, 2 semaines, flexible...)",
        "devis_calc": "📄 *Devis {service}* — Komara Agency 🇬🇳\n\nCalcul de ton projet :\n{calc}\n\n➡️ Total estimé : *{total}€*\n⏱️ Délai : {delay}\n\n⚠️ Estimation : le prix final est confirmé par l'équipe avant de commencer.\nUne question ? Écris ici ou sur WhatsApp {whatsapp} 🚀",
        "devis_promo_ok": "🎟️ Code *{code}* appliqué : -{pct:g}% !",
        "known_greeting": "Re-bonjour {name} 👋 Content de te revoir chez Komara Agency 🇬🇳 !\nComment je peux t'aider aujourd'hui ?",
        "rdv_known_start": "Re-bonjour {name} 👋\nSur quel sujet veux-tu un RDV ?",
        "rdv_already": "📅 Tu as déjà un RDV : {slot}\n📝 Sujet : {topic}\n\nPour en prendre un autre, tape 'nouveau rdv'.",
        "order_known_name": "Je te connais déjà, {name} 😊\nTape 'ok' pour garder ce nom, ou écris le bon.",
        "order_known_phone": "Je garde aussi ton numéro : {phone}\nTape 'ok' pour confirmer, ou écris le nouveau.",
    },
    "en": {
        "complaint_ack": 'Thank you for your message — it has been forwarded to the leadership team, who will personally review your case. You will be contacted within 24h 🙏',
        "human_ask": '🤝 No problem, a KOMARA expert will contact you within 5 min ⚡\nDrop your WhatsApp number 👇',
        "human_done": "✅ Noted! The KOMARA team will reach you on WhatsApp within 5 min ⚡\nMeanwhile, I'm still here 24/7 😊",
        "admin_panel": '🎛️ ADMIN PANEL — Komara Agency 🇬🇳\nYour whole bot, from your phone 👇\n\n💰 MONEY & REPORTS\n📊 /stats — counters + money (quotes, month, orders)\n📈 /rapport — full report\n🗓️ /hebdo — weekly report\n📤 /export — data export\n\n👥 CLIENTS & SALES\n📞 /prend — your 10 latest clients + numbers\n👤 /prend <name> — full client card\n✉️ /msg <id|number> <text> — message a client via the bot\n📢 /broadcast <text> — promo to all clients\n🛒 /commandes — catalogue orders\n🧑\u200d🤝\u200d🧑 /clients — client list\n📅 /rdvs — appointments\n\n🛍️ CATALOGUE & PROMOS\n📦 /produit — product list\n➕ /produit add <cat>|<name>|<desc>|<price>\n💱 /produit maj <id>|<price> — change a price\n❌ /produit del <id> — remove a product\n🎟️ /promo CODE 20 [max] — create a code (e.g. /promo TABASKI20 20 = -20%)\n🚫 /promo off CODE — deactivate a code\n📋 /promos — active codes\n\n🤖 BOT CONTROL\n🔒 /pause — close the bot (clients get the closed message)\n✅ /reprend — reopen the bot\n🔄 /maj — reload the knowledge base\n📄 /facture — PDF invoice\n💾 /backup — manual Drive backup\n🔗 /google — Google connection\n📥 /kb_import — import entries\n\n💡 Winning combo: /prend to see a client, /msg to write to them, /broadcast for a general promo. Only your ID can run all of this 🔐',
        "code_usage": '🎟️ To check a promo code: /code YOURCODE\nExample: /code TABASKI20 😊',
        "code_ok": "🎟️ Code {code} VALID: -{pct:g}% off! 🎉\nType 'quote' or 'order' to use it now 🚀",
        "code_bad": '❌ Code {code} invalid, expired or fully used.\nCheck the spelling, or ask the team for a code 😊',
        "msg_usage": '✉️ Message a client via the bot:\n\n/msg <chat_id|number|name> <message>\n\nExamples:\n/msg 123456 Hello, your order is ready ✅\n/msg Mariama Your Tabaski visual is ready! 🎨\n\nNumbers and chat_id are in /prend 📞',
        "msg_sent": '✅ Message delivered to {target} 👍',
        "msg_failed": '❌ Delivery failed: this client may have never started the bot, or has blocked it.',
        "msg_not_found": '❌ No client found for “{q}”.\nType /prend for your client list 📞',
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
        "devis_promo": "🎟️ Got a promo code?\nType the code, or 'pass' if you don't.",
        "promo_invalid": "❌ Invalid or expired code. Type a valid one, or 'pass'.",
        "tracking_none": "📦 No order with us yet.\nType 'order' to start your project 🚀",
        "tracking_head": "📦 Your order tracking:",
        "tracking_order": "Order",
        "tracking_since": "since",
        "devis_activity": "Great 👍 What type of business is it for?\n(shop, restaurant, real estate, training...)",
        "devis_deadline": "And your preferred timeline?\n(urgent, 2 weeks, flexible...)",
        "devis_calc": "📄 *Quote {service}* — Komara Agency 🇬🇳\n\nYour project calculation:\n{calc}\n\n➡️ Estimated total: *{total}€*\n⏱️ Timeline: {delay}\n\n⚠️ Estimate: the final price is confirmed by the team before we start.\nA question? Write here or on WhatsApp {whatsapp} 🚀",
        "devis_promo_ok": "🎟️ Code *{code}* applied: -{pct:g}%!",
        "known_greeting": "Hello again {name} 👋 Welcome back to Komara Agency 🇬🇳!\nHow can I help you today?",
        "rdv_known_start": "Hello again {name} 👋\nWhat's the appointment about?",
        "rdv_already": "📅 You already have an appointment: {slot}\n📝 Topic: {topic}\n\nTo book another one, type 'new appointment'.",
        "order_known_name": "I remember you, {name} 😊\nType 'ok' to keep this name, or write the right one.",
        "order_known_phone": "I also remember your number: {phone}\nType 'ok' to confirm, or write a new one.",
    },
    "es": {
        "complaint_ack": 'Gracias por tu mensaje — ha sido enviado al equipo directivo, que revisará personalmente tu caso. Te contactarán en 24h 🙏',
        "human_ask": '🤝 Sin problema, un experto KOMARA te contacta en 5 min ⚡\nDéjame tu número de WhatsApp 👇',
        "human_done": '✅ ¡Anotado! El equipo KOMARA te contacta por WhatsApp en 5 min ⚡\nMientras tanto, sigo aquí 24/7 😊',
        "admin_panel": '🎛️ PANEL ADMIN — Komara Agency 🇬🇳\nTodo tu bot, desde tu teléfono 👇\n\n💰 DINERO & INFORMES\n📊 /stats — contadores + dinero (presupuestos, mes, pedidos)\n📈 /rapport — informe completo\n🗓️ /hebdo — informe semanal\n📤 /export — exportación de datos\n\n👥 CLIENTES & VENTAS\n📞 /prend — tus 10 últimos clientes + números\n👤 /prend <nombre> — ficha completa del cliente\n✉️ /msg <id|número> <texto> — escribir a un cliente por el bot\n📢 /broadcast <texto> — promo a todos los clientes\n🛒 /commandes — pedidos del catálogo\n🧑\u200d🤝\u200d🧑 /clients — lista de clientes\n📅 /rdvs — citas\n\n🛍️ CATÁLOGO & PROMOS\n📦 /produit — lista de productos\n➕ /produit add <cat>|<nombre>|<desc>|<precio>\n💱 /produit maj <id>|<precio> — cambiar un precio\n❌ /produit del <id> — quitar un producto\n🎟️ /promo CODE 20 [max] — crear un código (ej : /promo TABASKI20 20 = -20%)\n🚫 /promo off CODE — desactivar un código\n📋 /promos — códigos activos\n\n🤖 CONTROL DEL BOT\n🔒 /pause — cerrar el bot (los clientes reciben el mensaje de cierre)\n✅ /reprend — reabrir el bot\n🔄 /maj — recargar la base de conocimientos\n📄 /facture — factura PDF\n💾 /backup — copia manual en Drive\n🔗 /google — conexión Google\n📥 /kb_import — importar fichas\n\n💡 Combo ganador: /prend para ver un cliente, /msg para escribirle, /broadcast para una promo general. Solo tu ID puede ejecutar todo esto 🔐',
        "code_usage": '🎟️ Para verificar un código: /code TUCODIGO\nEjemplo: /code TABASKI20 😊',
        "code_ok": "🎟️ Código {code} VÁLIDO: ¡-{pct:g}% de descuento! 🎉\nEscribe 'presupuesto' o 'pedir' para aprovecharlo 🚀",
        "code_bad": '❌ Código {code} inválido, caducado o agotado.\nRevisa la ortografía o pide un código al equipo 😊',
        "msg_usage": '✉️ Escribir a un cliente por el bot:\n\n/msg <chat_id|número|nombre> <mensaje>\n\nEjemplos:\n/msg 123456 Hola, tu pedido está listo ✅\n/msg Mariama ¡Tu visual Tabaski está listo! 🎨\n\nLos números están en /prend 📞',
        "msg_sent": '✅ Mensaje entregado a {target} 👍',
        "msg_failed": '❌ Entrega imposible: quizás este cliente nunca inició el bot o lo ha bloqueado.',
        "msg_not_found": '❌ Ningún cliente encontrado para « {q} ».\nEscribe /prend para ver tu lista de clientes 📞',
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
        "devis_promo": "🎟️ ¿Tienes un código promo?\nEscribe el código, o 'pasar' si no tienes.",
        "promo_invalid": "❌ Código inválido o expirado. Escribe uno válido, o 'pasar'.",
        "tracking_none": "📦 Aún no tienes pedidos con nosotros.\nEscribe 'ordenar' para empezar tu proyecto 🚀",
        "tracking_head": "📦 Seguimiento de tu pedido:",
        "tracking_order": "Pedido",
        "tracking_since": "desde el",
        "devis_activity": "Genial 👍 ¿Para qué tipo de negocio?\n(tienda, restaurante, inmobiliaria, formación...)",
        "devis_deadline": "¿Y tu plazo preferido?\n(urgente, 2 semanas, flexible...)",
        "devis_calc": "📄 *Presupuesto {service}* — Komara Agency 🇬🇳\n\nCálculo de tu proyecto:\n{calc}\n\n➡️ Total estimado: *{total}€*\n⏱️ Plazo: {delay}\n\n⚠️ Estimación: el precio final lo confirma el equipo antes de empezar.\n¿Una pregunta? Escribe aquí o por WhatsApp {whatsapp} 🚀",
        "devis_promo_ok": "🎟️ Código *{code}* aplicado: -{pct:g}%!",
        "known_greeting": "¡Hola de nuevo {name} 👋 ¡Bienvenido otra vez a Komara Agency 🇬🇳!\n¿Cómo te ayudo hoy?",
        "rdv_known_start": "¡Hola de nuevo {name} 👋\n¿Sobre qué tema es la cita?",
        "rdv_already": "📅 Ya tienes una cita: {slot}\n📝 Tema: {topic}\n\nPara otra, escribe 'nueva cita'.",
        "order_known_name": "Te conozco, {name} 😊\nEscribe 'ok' para confirmar, o el nombre correcto.",
        "order_known_phone": "También guardo tu número: {phone}\nEscribe 'ok' para confirmar, o el nuevo.",
    },
    "ar": {
        "complaint_ack": 'شكرا على رسالتك — تم تحويلها إلى الفريق الإداري الذي سيراجع حالتك شخصيا. سيتم التواصل معك خلال 24 ساعة 🙏',
        "human_ask": '🤝 لا مشكلة، خبير كومارا سيتصل بك خلال 5 دقائق ⚡\nاترك رقم واتساب 👇',
        "human_done": '✅ تم التسجيل! فريق كومارا سيتصل بك على واتساب خلال 5 دقائق ⚡\nوأنا هنا 24/7 في انتظارك 😊',
        "admin_panel": '🎛️ لوحة الأدمن — كومارا أجنسلي 🇬🇳\nالبوت كله من هاتفك 👇\n\n💰 المال والتقارير\n📊 /stats — الأرقام + المال\n📈 /rapport — تقرير كامل\n🗓️ /hebdo — تقرير أسبوعي\n📤 /export — تصدير البيانات\n\n👥 العملاء والمبيعات\n📞 /prend — آخر 10 عملاء + أرقام\n👤 /prend <اسم> — بطاقة العميل الكاملة\n✉️ /msg <معرف|رقم> <نص> — مراسلة عميل عبر البوت\n📢 /broadcast <نص> — عرض لكل العملاء\n🛒 /commandes — طلبات الكتالوج\n🧑\u200d🤝\u200d🧑 /clients — قائمة العملاء\n📅 /rdvs — المواعيد\n\n🛍️ الكتالوج والعروض\n📦 /produit — قائمة المنتجات\n➕ /produit add <فئة>|<اسم>|<وصف>|<سعر>\n💱 /produit maj <id>|<سعر> — تغيير سعر\n❌ /produit del <id> — حذف منتج\n🎟️ /promo CODE 20 [max] — إنشاء كود (مثال : /promo TABASKI20 20 = -20%)\n🚫 /promo off CODE — تعطيل كود\n📋 /promos — الأكواد النشطة\n\n🤖 التحكم في البوت\n🔒 /pause — إغلاق البوت\n✅ /reprend — إعادة فتح البوت\n🔄 /maj — إعادة تحميل قاعدة المعرفة\n📄 /facture — فاتورة PDF\n💾 /backup — نسخ احتياطي يدوي\n🔗 /google — ربط Google\n📥 /kb_import — استيراد أجوبة\n\n💡 المزيج الرابح: /pend لرؤية العميل، /msg للمراسلة، /broadcast للعرض العام. فقط معرّفك يمكنه تنفيذ كل هذا 🔐',
        "code_usage": '🎟️ للتحقق من كود الخصم: /code الكود\nمثال: /code TABASKI20 😊',
        "code_ok": "🎟️ الكود {code} صالح: خصم {pct:g}%! 🎉\nاكتب 'devis' أو 'commander' للاستفادة الآن 🚀",
        "code_bad": '❌ الكود {code} غير صالح أو منتهي أو مستهلك.\nتحقق من الكتابة أو اطلب كوداً من الفريق 😊',
        "msg_usage": '✉️ مراسلة عميل عبر البوت:\n\n/msg <chat_id|رقم|اسم> <رسالة>\n\nأمثلة:\n/msg 123456 مرحباً، طلبك جاهز ✅\n/msg Mariama تصميمك جاهز! 🎨\n\nالأرقام في /prend 📞',
        "msg_sent": '✅ تم تسليم الرسالة إلى {target} 👍',
        "msg_failed": '❌ تعذّر التسليم: ربما لم يبدأ العميل المحادثة مع البوت أو حظره.',
        "msg_not_found": '❌ لا يوجد عميل بهذا الاسم « {q} ».\nاكتب /prend لقائمة عملائك 📞',
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

    text_clean = text.strip()

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

    # 0ter-bis. Catalogue, panier, ajouter/vider
    if catalogue.handle_client(bot, chat_id, text_clean, lang):
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
            # Panier non vide → tunnel catalogue au lieu du flux commande
            if flow == "parrainage":
                return _parrainage_reply(bot, chat_id, lang)
            if flow == "order" and catalogue.start_checkout(bot, chat_id, lang):
                return True
            return start_flow(bot, chat_id, flow, lang)

    # 2bis. 'nouveau rdv' force un RDV même si un existe déjà
    if low in NEW_RDV_WORDS:
        return start_flow(bot, chat_id, "rdv", lang, force=True)

    # 3. Étape d'un flux actif
    active = _fetch_flow(chat_id)
    if active:
        flow, step, data = active
        return _advance_flow(bot, chat_id, flow, step, data, text_clean, lang)

    # 4. Client connu : salutation personnalisée (mémoire longue)
    if low in GREETING_WORDS and client_greeting(bot, chat_id, lang):
        return True

    return False


def start_flow(bot, chat_id: int, flow: str, lang: str, force: bool = False) -> bool:
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
        _save_flow(chat_id, "devis", "service", {})
        bot.send_message(chat_id, t(lang, "devis_start", grid=grid))
    elif flow == "lead":
        _save_flow(chat_id, "lead", "name", {})
        bot.send_message(chat_id, t(lang, "lead_start"))
    elif flow == "survey":
        _save_flow(chat_id, "survey", "0", {})
        if lang == "fr":
            # random.choice : jamais 2 fois la même demande d'avis
            bot.send_message(chat_id, random.choice(SURVEY_ASK_VARIANTS_FR))
        else:
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
    if flow == "human":
        return _step_human(bot, chat_id, step, data, text, lang)
    if flow == "checkout":
        return catalogue.step_checkout(bot, chat_id, step, data, text, lang)
    _clear_flow(chat_id)
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

        recap = (
            f"📌 Commande n°{order_id}\n"
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
    (["e-commerce", "ecommerce", "boutique en ligne", "panier", "paiement en ligne",
      "orange money", "wave", "paypal"], 40, "Boutique/paiement en ligne"),
    (["multilingue", "plusieurs langues", "anglais et", "en arabe", "en espagnol"], 20, "Version multilingue"),
    (["crm", "google agenda", "notion", "google sheet", "formulaire"], 15, "Intégrations (agenda/CRM)"),
    (["messenger", "instagram", "tiktok", "multi-canal", "multi canal", "4 canaux"], 25, "Canaux supplémentaires"),
    (["vidéo", "animation", "motion"], 30, "Contenu vidéo"),
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
        _save_flow(chat_id, "devis", "activity", data)
        bot.send_message(chat_id, t(lang, "devis_activity"))
        return True

    if step == "activity":
        data["activity"] = text[:200]
        _save_flow(chat_id, "devis", "deadline", data)
        bot.send_message(chat_id, t(lang, "devis_deadline"))
        return True

    if step == "deadline":
        data["deadline"] = text[:100]
        _save_flow(chat_id, "devis", "promo", data)
        bot.send_message(chat_id, t(lang, "devis_promo"))
        return True

    if step == "promo":
        low_answer = text.strip().lower()
        if low_answer in {"passer", "pass", "skip", "pasar", "نم"}:
            data["promo"] = None
        else:
            result = check_promo_code(text)
            if not result:
                result = _referral_as_promo(bot, chat_id, text)
            if not result:
                bot.send_message(chat_id, t(lang, "promo_invalid"))
                return True
            code, pct = result
            with DB_LOCK:
                DB_CONN.execute(
                    "UPDATE promo_codes SET uses = uses + 1 WHERE code =?", (code,)
                )
                DB_CONN.commit()
            data["promo"] = {"code": code, "pct": pct}
            bot.send_message(chat_id, t(lang, "devis_promo_ok", code=code, pct=pct),
                            parse_mode="Markdown")
        return _finish_devis(bot, chat_id, data, lang)

    _clear_flow(chat_id)
    return False


def _finish_devis(bot, chat_id: int, data: dict, lang: str) -> bool:
    _clear_flow(chat_id)
    promo = data.get("promo")

    # Calcul complet : base + complexité + urgence - promo
    calc_lines, total = calc_devis(data)
    calc_display = "\n".join("• " + ln for ln in calc_lines)

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
        ),
    )
    upsert_client(
        chat_id,
        event=f"Devis express : {data.get('service','')}"
        + (f" (code {promo['code']} -{promo['pct']:g}%)" if promo else ""),
    )
    admin_note = f" (code {promo['code']} -{promo['pct']:g}%)" if promo else ""
    notify_admin(
        bot,
        f"📄 DEVIS EXPRESS{admin_note}\n🛠️ {data.get('service','')}\n"
        f"💰 {total:g}€\n📝 {data.get('details','')}\n👤 chat_id: {chat_id}",
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


def _is_complaint(low: str) -> bool:
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

    if command in {"/produit", "/produits"}:
        catalogue.admin_product(bot, chat_id, args, lang)
        return True

    if command == "/admin":
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
            "SELECT name, phone, activity, events, first_seen FROM clients WHERE chat_id =?",
            (key,),
        ).fetchone()
        if row:
            old_name, old_phone, old_activity, old_events, first_seen = row
            name = name or old_name
            phone = phone or old_phone
            activity = activity or old_activity
            events = json.loads(old_events or "[]")
        else:
            events = []
            first_seen = now
        if event:
            events.append(f"{now[:10]} : {event}")
            events = events[-50:]  # 50 derniers évènements max
        DB_CONN.execute(
            "INSERT OR REPLACE INTO clients "
            "(chat_id, name, phone, activity, events, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, name[:100], phone[:50], activity[:150],
             json.dumps(events, ensure_ascii=False), first_seen, now),
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
    """Salutation personnalisée pour un client déjà connu."""
    client = get_client(chat_id)
    if not client or not client["name"]:
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
    if not parts:
        bot.send_message(
            chat_id,
            "Usage :\n/promo CODE 10 → crée CODE (-10%)\n"
            "/promo CODE 20 50 → -20%, max 50 utilisations\n"
            "/promo off CODE → désactive\n/promos → liste",
        )
        return True

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
    hours = f"{_fmt_hour(WORK_START)}-{_fmt_hour(WORK_END)}"
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
