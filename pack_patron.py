# -*- coding: utf-8 -*-
"""
pack_patron.py — Feature #6 : Pack Patron V2 (Dashboard + Maintenance).

Fonction Avis/Preuve sociale #10 : NON CODÉE (supprimée à la demande
du client — lettre #6).

9. DASHBOARD PATRON /admin (local, lecture DB seule) — 5 cartes KPI :
   • CA aujourd'hui / cette semaine / ce mois (paniers payés, €)
   • Paniers abandonnés (panier non vide + pas paid depuis 24h)
   • Top vente (produit 1-7 le plus vendu)
   • Impayés (2èmes tranches en attente, €)
   • Abonnements actifs — MRR (€/mois)

11. MAINTENANCE (MRR) : après paiement Agent IA/Chatbot/Bot →
    offre Maintenance (Agent IA & Chatbot 50€/mois, Bot 20€/mois).
    OUI MAINTENANCE → table abonnements + facture J+30 (cron mensuel :
    QR + message). NON → assurance_refusee=1, on ne re-demande jamais.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import commercial_db as cdb

logger = logging.getLogger("komara")

# Ids alignés sur catalogue.py : 1=Bot, 2=Chatbot, 3=Agent IA, 4=Maintenance.
CATALOG = {1: ("Bot Scripté", 50.0), 2: ("Chatbot IA Vendeur", 100.0),
           3: ("Agent IA Premium", 150.0), 4: ("Maintenance mensuelle", 50.0)}
BOT_PROD, CHATBOT_PROD, AGENT_PROD, MAINT_PROD = 1, 2, 3, 4
MAINT_MONTH_AGENT = 50.0     # €/mois (Agent IA ou Chatbot)
MAINT_MONTH_BOT = 20.0       # €/mois (Bot scripté)
MAINT_INTERV = 50.0          # €/intervention hors maintenance


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 9. DASHBOARD PATRON /admin
# ---------------------------------------------------------------------------

def dashboard(lang: str = "fr") -> str:
    """5 cartes KPI en €, lecture seule de la DB locale."""
    import actions
    conn = actions.DB_CONN

    # CA : payments (reçus validés) par période
    now = _now()
    ca = {"jour": 0.0, "semaine": 0.0, "mois": 0.0}
    for row in conn.execute("SELECT amount, currency, created_at FROM payments").fetchall():
        eur = _to_eur(row[0] or 0, row[1] or "USD")
        t = _parse(row[2])
        if not t:
            continue
        if now - t <= timedelta(days=1):
            ca["jour"] += eur
        if now - t <= timedelta(days=7):
            ca["semaine"] += eur
        if now - t <= timedelta(days=30):
            ca["mois"] += eur

    # paniers abandonnés : commandes catalogue en attente depuis 24h
    abandoned = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status='en attente' AND created_at < ?",
        ((now - timedelta(hours=24)).isoformat(timespec="seconds"),),
    ).fetchone()[0]

    # top vente (produits 1-7)
    rows = conn.execute(
        "SELECT produit_id, COUNT(*) c FROM purchases GROUP BY produit_id "
        "ORDER BY c DESC LIMIT 1").fetchall()
    top = f"{CATALOG[rows[0][0]][0]} (x{rows[0][1]})" if rows and rows[0][0] in CATALOG else "—"

    # impayés : 2èmes tranches en attente
    impayes = conn.execute(
        "SELECT SUM(prix_eur/2) FROM purchases WHERE paiement_en_2x=1 AND "
        "tranche2_payee=0 AND status='livre'").fetchone()[0] or 0.0

    # MRR : abonnements actifs
    mrr = conn.execute(
        "SELECT SUM(montant_mensuel_eur) FROM abonnements WHERE status='actif'"
    ).fetchone()[0] or 0.0
    n_abo = conn.execute(
        "SELECT COUNT(*) FROM abonnements WHERE status='actif'").fetchone()[0]

    if lang != "fr":
        labels = {
            "en": ["💰 REVENUE TODAY / WEEK / MONTH", "🛒 Abandoned carts (24h)",
                   "🏆 Top sale", "⏳ Unpaid (2nd instalments)",
                   "🛡️ Active subscriptions — MRR"],
            "es": ["💰 INGRESOS HOY / SEMANA / MES", "🛒 Carritos abandonados (24h)",
                   "🏆 Top de ventas", "⏳ Impagos (2ª fracción)",
                   "🛡️ Suscripciones activas — MRR"],
        }
        L = labels.get(lang, labels["en"])
        return (
            f"📊 DASHBOARD PATRON — Komara Agency 🇬🇳\n\n"
            f"━━━━━━━━━━━━━━━━\n{L[0]}\n"
            f"📅 {ca['jour']:g}€  |  🗓️ {ca['semaine']:g}€  |  📆 {ca['mois']:g}€\n"
            f"━━━━━━━━━━━━━━━━\n{L[1]}\n🛍️ {abandoned}\n"
            f"━━━━━━━━━━━━━━━━\n{L[2]}\n🥇 {top}\n"
            f"━━━━━━━━━━━━━━━━\n{L[3]}\n💸 {impayes:g}€\n"
            f"━━━━━━━━━━━━━━━━\n{L[4]}\n🛡️ {n_abo} ×  →  {mrr:g}€/mois\n"
            f"━━━━━━━━━━━━━━━━"
        )
    return (
        "📊 DASHBOARD PATRON — Komara Agency 🇬🇳\n\n"
        "━━━━━━━━━━━━━━━━\n💰 CA AUJOURD'HUI / SEMAINE / MOIS\n"
        f"📅 {ca['jour']:g}€  |  🗓️ {ca['semaine']:g}€  |  📆 {ca['mois']:g}€\n"
        f"━━━━━━━━━━━━━━━━\n🛒 PANIERS ABANDONNÉS (24h)\n🛍️ {abandoned}\n"
        f"━━━━━━━━━━━━━━━━\n🏆 TOP VENTE\n🥇 {top}\n"
        f"━━━━━━━━━━━━━━━━\n⏳ IMPAYÉS (2èmes tranches)\n💸 {impayes:g}€\n"
        f"━━━━━━━━━━━━━━━━\n🛡️ ABONNEMENTS ACTIFS — MRR\n"
        f"🛡️ {n_abo} abonnement(s) → {mrr:g}€/mois\n"
        "━━━━━━━━━━━━━━━━"
    )


def _to_eur(amount: float, currency: str) -> float:
    import devis_engine
    v = devis_engine.convert(amount, currency or "USD", "EUR")
    return v if v is not None else 0.0


# ---------------------------------------------------------------------------
# 11. MAINTENANCE (MRR)
# ---------------------------------------------------------------------------

ASSUR_OFFER = {
    "fr": ("🛡️ Ton {produit} est actif. Question rapide boss : Tu prends "
           "la Maintenance tranquillité ?\n\n"
           "• Agent IA / Chatbot : 50€/mois, on répare tout en 2h + mises à "
           "jour IA incluses. Sans maintenance : 50€ / intervention.\n"
           "• Bot Scripté : 20€/mois, corrections + mises à jour incluses. "
           "Sans maintenance : 50€ / intervention.\n\n"
           "Tu l'actives pour {prix}€/mois ? Réponds OUI MAINTENANCE ou NON"),
    "en": ("🛡️ Your {produit} is live. Quick question boss: taking the "
           "peace-of-mind Maintenance?\n\n"
           "• AI Agent / Chatbot: €50/month, we fix everything within 2h + "
           "AI updates included. Without: €50 / intervention.\n"
           "• Scripted Bot: €20/month, fixes + updates included. "
           "Without: €50 / intervention.\n\n"
           "Activate it for {prix}€/month? Reply YES MAINTENANCE or NO"),
    "es": ("🛡️ Su {produit} está activo. Pregunta rápida jefe: ¿toma el "
           "Mantenimiento de tranquilidad?\n\n"
           "• Agente IA / Chatbot: 50€/mes, reparamos todo en 2h + "
           "actualizaciones de IA incluidas. Sin mantenimiento: 50€ / "
           "intervención.\n"
           "• Bot programado: 20€/mes, correcciones + actualizaciones "
           "incluidas. Sin mantenimiento: 50€ / intervención.\n\n"
           "¿Activarlo por {prix}€/mes? Responda SÍ MANTENIMIENTO o NO"),
    "ar": ("🛡️ {produit} الخاص بك مفعّل. سؤال سريع زعيم: هل تأخذ "
           "الصيانة المريحة؟\n\n"
           "• وكيل IA / شات بوت: 50€/شهرياً، نصلح كل شيء خلال ساعتين + "
           "تحديثات AI. بدون صيانة: 50€ / تدخل.\n"
           "• بوت برمجي: 20€/شهرياً، إصلاحات + تحديثات. بدون صيانة: "
           "50€ / تدخل.\n\n"
           "تفعيله مقابل {prix}€/شهرياً؟ أجب نعم صيانة أو لا"),
}
ASSUR_OK = {
    "fr": "🛡️ Maintenance activée Chef ! {prix}€/mois, première facture dans "
          "30 jours avec QR de paiement. On veille sur ton {produit} 🔥",
    "en": "🛡️ Maintenance activated boss! €{prix}/month, first invoice in 30 "
          "days with payment QR. We've got your {produit} covered 🔥",
    "es": "🛡️ ¡Mantenimiento activado jefe! {prix}€/mes, primera factura en "
          "30 días con QR de pago. Cuidamos su {produit} 🔥",
    "ar": "🛡️ تم تفعيل الصيانة زعيم! {prix}€/شهرياً، أول فاتورة بعد 30 يوماً "
          "مع رمز الدفع. نعتني بـ {produit} الخاص بك 🔥",
}


def _produit_id_of(chat_id) -> int:
    """Dernier achat payé du client → id produit (pour l'assurance)."""
    import actions
    row = actions.DB_CONN.execute(
        "SELECT produit_id FROM purchases WHERE chat_id=? "
        "ORDER BY id DESC LIMIT 1", (str(chat_id),)).fetchone()
    return row[0] if row and row[0] else 0


def maybe_offer_assurance(bot, chat_id: int, lang: str = "fr") -> bool:
    """Après paiement Agent IA/Chatbot/Bot → offre Maintenance. Les autres
    produits : rien. Déjà refusée : jamais re-demandée."""
    import actions
    conn = actions.DB_CONN
    row = conn.execute("SELECT assurance_refusee FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    if row and row[0]:
        return False
    pid = _produit_id_of(chat_id)
    if pid in (CHATBOT_PROD, AGENT_PROD):
        prix = MAINT_MONTH_AGENT
    elif pid == BOT_PROD:
        prix = MAINT_MONTH_BOT
    else:
        return False  # Maintenance déjà achetée : pas de double offre
    produit = CATALOG[pid][0]
    bot.send_message(chat_id, ASSUR_OFFER.get(lang, ASSUR_OFFER["fr"])
                     .format(produit=produit, prix=f"{prix:g}"))
    return True


def handle_assurance_reply(bot, chat_id: int, text: str, lang: str = "fr") -> bool:
    """« OUI MAINTENANCE » / « NON » après l'offre. True = consommé."""
    import actions
    low = text.strip().lower()
    conn = actions.DB_CONN
    oui_kw = {"oui assurance", "oui maintenance", "yes insurance",
              "yes maintenance", "sí seguro", "si seguro",
              "sí mantenimiento", "si mantenimiento", "نعم تأمين",
              "نعم صيانة", "oui"}
    if lang and low in oui_kw:
        # éviter le double-abonnement
        row = conn.execute(
            "SELECT COUNT(*) FROM abonnements WHERE chat_id=? AND status='actif'",
            (str(chat_id),)).fetchone()
        if row and row[0] and low == "oui":
            return False  # « oui » seul trop ambigu si déjà abonné
        pid = _produit_id_of(chat_id)
        if pid in (CHATBOT_PROD, AGENT_PROD):
            prix = MAINT_MONTH_AGENT
        elif pid == BOT_PROD:
            prix = MAINT_MONTH_BOT
        else:
            return False
        cdb.insert_abonnement(chat_id, pid, CATALOG[pid][0], prix)
        bot.send_message(chat_id, ASSUR_OK.get(lang, ASSUR_OK["fr"])
                         .format(prix=f"{prix:g}", produit=CATALOG[pid][0]))
        logger.info("🛡️ Maintenance activée (chat %s, %s€/mois)", chat_id, prix)
        return True
    if low in {"non", "no", "لا"}:
        with actions.DB_LOCK:
            conn.execute(
                """INSERT INTO clients (chat_id, first_seen, last_seen, assurance_refusee)
                   VALUES (?,?,?,1)
                   ON CONFLICT(chat_id) DO UPDATE SET assurance_refusee=1""",
                (str(chat_id), _now().isoformat(timespec="seconds"),
                 _now().isoformat(timespec="seconds")))
            conn.commit()
        bot.send_message(chat_id, {
            "fr": "Pas de souci Chef 😊 Sans maintenance, chaque intervention "
                  "reste à 50€ si besoin. On reste dispo 🙌",
            "en": "No problem boss 😊 Without maintenance, each intervention "
                  "stays at €50 if needed. We're here 🙌",
            "es": "Sin problema jefe 😊 Sin mantenimiento, cada intervención "
                  "queda en 50€ si hace falta. Aquí estamos 🙌",
            "ar": "لا مشكلة زعيم 😊 بدون صيانة، كل تدخل يبقى 50€ عند الحاجة. "
                  "نحن هنا 🙌",
        }.get(lang, ""))
        return True
    return False


# ---------------------------------------------------------------------------
# CRON MENSUEL : facture assurance J+30 → QR + message
# ---------------------------------------------------------------------------

def process_monthly(bot, now=None) -> int:
    now = now or _now()
    import actions
    conn = actions.DB_CONN
    rows = conn.execute(
        "SELECT id, chat_id, produit, montant_mensuel_eur, "
        "prochaine_facture_date FROM abonnements WHERE status='actif'"
    ).fetchall()
    cols = ["id", "chat_id", "produit", "montant_mensuel_eur",
            "prochaine_facture_date"]
    sent = 0
    for r in rows:
        d = dict(zip(cols, r))
        due = _parse(d["prochaine_facture_date"])
        if not due or now < due:
            continue
        if not cdb.can_auto_message(d["chat_id"], now.date().isoformat()):
            continue
        import qr_module, devis_engine
        cc = _country(conn, d["chat_id"])
        conv = devis_engine.convert_devis(cc, d["montant_mensuel_eur"])
        qr_module.generate_payment_qr(
            bot, int(d["chat_id"]), conv["price_local"], conv["currency"],
            "Orange Money / Wave", "fr")
        bot_message = (f"Assurance {d['produit']} — {d['montant_mensuel_eur']:g}€ "
                       f"ce mois :")
        try:
            if bot is None:
                logger.info("[SIMU] %s (chat %s)", bot_message, d["chat_id"])
            else:
                bot.send_message(int(d["chat_id"]), bot_message)
        except Exception as e:
            logger.error("Envoi facture assurance : %s", e)
        with actions.DB_LOCK:
            conn.execute(
                "UPDATE abonnements SET prochaine_facture_date=? WHERE id=?",
                ((now + timedelta(days=30)).isoformat(timespec="seconds"),
                 d["id"]))
            conn.commit()
        sent += 1
    return sent


def _country(conn, chat_id) -> str:
    row = conn.execute("SELECT phone FROM clients WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    import devis_engine
    cc, _ = devis_engine.detect_locality(row[0] if row else "", "fr")
    return cc or "GN"
