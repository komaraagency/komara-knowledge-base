# -*- coding: utf-8 -*-
"""
payment_links.py — Paiement Multi-Mode (PayPal / Carte / Support).

SPEC (demande du 29/09/2026) :
  • Panier actuel conservé : catalogue 1-7 (€), 'ajouter [num]',
    'panier', 'devis', total en €
  • « payer » (et fin du flux devis) → carte devis avec 3 boutons URL :
      1. 💳 PayPal (Recommandé) → https://paypal.me/{username}/{total}EUR
      2. 💳 Carte / PCS        → lien Stripe (exact montant, défaut, sinon masqué)
      3. 📲 Support            → t.me (masqué si non configuré)
  • ID devis : KA-{user_id}-{YYYYMMDD}
  • Après paiement : capture d'écran demandée → reçue sans QR →
    transfert auto vers l'admin avec contexte client.

VALIDATION DES LIENS (règle d'or « attend l'agrément ») :
  Tout est codé mais INACTIF par défaut (enabled=false) : « payer »
  garde l'ancien flux QR. L'admin active/configure via /paiement :
      /paiement                  → statut complet
      /paiement on / off         → activer / désactiver les boutons
      /paiement paypal <user>    → username paypal.me
      /paiement stripe <url>     → lien Stripe par défaut
      /paiement stripe 150 <url> → lien Stripe pour le total exact 150€
      /paiement support <url>    → lien support Telegram
  Config persistée dans {ACTIONS_DIR}/payment_links.json
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("komara")

DEFAULTS = {
    "enabled": False,       # s'active après agrément des liens
    "paypal": "KomaraAgency",
    "stripe": "",           # lien par défaut (dynamique)
    "stripe_amounts": {},   # {"150": "https://buy.stripe.com/..."}
    "support": "",          # https://t.me/...
}

CONFIG_NAME = "payment_links.json"


def _config_path() -> str:
    base = os.environ.get("ACTIONS_DIR", "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, CONFIG_NAME)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(_config_path(), encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError):
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    with open(_config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def quote_id(chat_id: int) -> str:
    """KA-{user_id}-{YYYYMMDD} — spec point 2b."""
    d = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"KA-{chat_id}-{d}"


# ---------------------------------------------------------------------------
# Source du total : panier > devis en attente > rien
# ---------------------------------------------------------------------------

def payment_target(chat_id: int) -> tuple[float, list[str], str] | None:
    """(total_eur, [détail produits], source) ou None si rien à payer."""
    import catalogue
    items = catalogue.cart_items(chat_id)
    if items:
        total = sum(line for _n, _q, line in items)
        detail = [f"{name} x{qty}" for name, qty, _l in items]
        return total, detail, "panier"
    # sinon : dernier devis en attente (étapes du tunnel commercial)
    import commercial_db as cdb
    import actions
    row = actions.DB_CONN.execute(
        "SELECT service, project_desc, price_eur, id FROM pending_quotes "
        "WHERE chat_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (str(chat_id),)).fetchone()
    if row:
        service = row[0] or row[1] or "Projet"
        return float(row[2] or 0), [service], "devis"
    return None


def has_pending_payment(chat_id: int) -> bool:
    """Le client a-t-il un paiement attendu (panier ou devis) ?
    Sert au transfert des captures PayPal vers l'admin."""
    try:
        return payment_target(chat_id) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Carte devis + boutons URL
# ---------------------------------------------------------------------------

CARD_TEXT = {
    "fr": ("🧾 DEVIS {qid} — KOMARA AGENCY 🇬🇳\n"
           "Détail : {detail}\n"
           "TOTAL : {total:g}€\n\n"
           "Choisis ton paiement 👇\n"
           "PayPal est recommandé : le montant est déjà rempli ✅\n\n"
           "📸 Après paiement, envoie la capture d'écran PayPal ici "
           "pour validation."),
    "en": ("🧾 QUOTE {qid} — KOMARA AGENCY 🇬🇳\n"
           "Details: {detail}\n"
           "TOTAL: {total:g}€\n\n"
           "Choose your payment 👇\n"
           "PayPal recommended: amount pre-filled ✅\n\n"
           "📸 After payment, send the PayPal screenshot here "
           "for validation."),
    "es": ("🧾 PRESUPUESTO {qid} — KOMARA AGENCY 🇬🇳\n"
           "Detalle: {detail}\n"
           "TOTAL: {total:g}€\n\n"
           "Elija su pago 👇\n"
           "PayPal recomendado: importe ya rellenado ✅\n\n"
           "📸 Tras el pago, envíe aquí la captura de PayPal "
           "para validación."),
    "ar": ("🧾 عرض سعر {qid} — كومارا أجنسلي 🇬🇳\n"
           "التفاصيل: {detail}\n"
           "المجموع: {total:g}€\n\n"
           "اختر طريقة الدفع 👇\n"
           "باي بال موصى به: المبلغ مُعبّأ مسبقاً ✅\n\n"
           "📸 بعد الدفع، أرسل لقطة شاشة باي بال هنا للتحقق."),
}

BTN_LABELS = {
    "fr": ("💳 PayPal (Recommandé)", "💳 Carte / PCS", "📲 Support"),
    "en": ("💳 PayPal (Recommended)", "💳 Card / PCS", "📲 Support"),
    "es": ("💳 PayPal (Recomendado)", "💳 Tarjeta / PCS", "📲 Soporte"),
    "ar": ("💳 PayPal (موصى به)", "💳 بطاقة / PCS", "📲 الدعم"),
}


def _stripe_link(cfg: dict, total: float) -> str:
    """Lien Stripe : montant exact > défaut > '' (bouton masqué)."""
    amounts = cfg.get("stripe_amounts") or {}
    key = f"{total:g}"
    if key in amounts:
        return amounts[key]
    # total entier → « 150.0 » doit matcher « 150 »
    alt = f"{int(total)}" if float(total).is_integer() else None
    if alt and alt in amounts:
        return amounts[alt]
    return cfg.get("stripe") or ""


def build_markup(cfg: dict, total: float, lang: str):
    """3 boutons URL (telebot InlineKeyboardMarkup) selon la spec."""
    from telebot import types
    lbl = BTN_LABELS.get(lang, BTN_LABELS["fr"])
    rows = []

    paypal_user = cfg.get("paypal") or "KomaraAgency"
    paypal_url = f"https://paypal.me/{paypal_user}/{total:g}EUR"
    rows.append([types.InlineKeyboardButton(lbl[0], url=paypal_url)])

    stripe = _stripe_link(cfg, total)
    if stripe:
        rows.append([types.InlineKeyboardButton(lbl[1], url=stripe)])

    support = cfg.get("support") or ""
    if support:
        rows.append([types.InlineKeyboardButton(lbl[2], url=support)])

    markup = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        markup.row(*row)
    return markup


def send_payment_card(bot, chat_id: int, lang: str = "fr") -> bool:
    """« payer » avec panier/devis → carte devis + 3 boutons URL.
    False si rien à payer (le routeur garde alors l'ancien flux)."""
    cfg = load_config()
    if not cfg.get("enabled"):
        return False
    target = payment_target(chat_id)
    if not target:
        return False
    total, detail, _src = target
    if total <= 0:
        return False
    text = CARD_TEXT.get(lang, CARD_TEXT["fr"]).format(
        qid=quote_id(chat_id), detail=", ".join(detail)[:120], total=total)
    try:
        markup = build_markup(cfg, total, lang)
        bot.send_message(chat_id, text, reply_markup=markup)
    except TypeError:
        # bot factice des tests sans kwargs markup
        bot.send_message(chat_id, text)
    # trace admin : un client vient de recevoir la carte paiement
    logger.info("🧾 Carte paiement envoyée (chat %s, %g€, %s)",
                chat_id, total, quote_id(chat_id))
    return True


# ---------------------------------------------------------------------------
# Capture PayPal sans QR → transfert admin
# ---------------------------------------------------------------------------

def maybe_forward_capture(bot, chat_id: int, image_path: str,
                          lang: str = "fr") -> bool:
    """Photo sans QR + paiement en attente → forward vers l'admin.
    Retour True = consommé (on ne renvoie pas le message « pas de QR »)."""
    import actions
    if not actions.ADMIN_CHAT_ID:
        return False
    if not has_pending_payment(chat_id):
        return False
    client = actions.get_client(chat_id) or {}
    target = payment_target(chat_id)
    total, detail, _src = target
    # nom/tél complétés depuis le dernier devis si fiche client vide
    name = client.get("name")
    phone = client.get("phone") or ""
    if not name or not phone:
        row = actions.DB_CONN.execute(
            "SELECT client_name, phone FROM pending_quotes WHERE chat_id=? "
            "ORDER BY id DESC LIMIT 1", (str(chat_id),)).fetchone()
        if row:
            name = name or row[0]
            phone = phone or row[1] or ""
    caption = (
        f"📸 CAPTURE PAIEMENT — {quote_id(chat_id)}\n"
        f"Client : {name or 'inconnu'} (id {chat_id})\n"
        f"Tél : {phone or '—'}\n"
        f"Détail : {', '.join(detail)[:80]}\n"
        f"TOTAL : {total:g}€ (source {_src})\n"
        f"À valider : confirme ou tape /msg {chat_id} ... pour répondre."
    )
    try:
        with open(image_path, "rb") as fh:
            bot.send_photo(actions.ADMIN_CHAT_ID, fh, caption=caption)
    except Exception as e:
        logger.error("Transfert capture vers admin impossible : %s", e)
    confirm = {
        "fr": "Capture reçue Chef ✅ Je l'ai transmise à Komara Agency pour "
              "validation. Tu recevras la confirmation très vite 🔥",
        "en": "Screenshot received boss ✅ Sent to Komara Agency for "
              "validation. You'll get the confirmation very soon 🔥",
        "es": "Captura recibida jefe ✅ Enviada a Komara Agency para su "
              "validación. Recibirá la confirmación muy pronto 🔥",
        "ar": "تم استلام اللقطة زعيم ✅ أُرسلت إلى كومارا أجنسلي للتحقق. "
              "ستتلقى التأكيد قريباً جداً 🔥",
    }.get(lang, "fr")
    bot.send_message(chat_id, confirm)
    return True


# ---------------------------------------------------------------------------
# Commande admin /paiement
# ---------------------------------------------------------------------------

ADMIN_HELP = (
    "🧾 PAIEMENT MULTI-MODE — Komara Agency 🇬🇳\n\n"
    "Statut : {etat}\n"
    "PayPal : paypal.me/{paypal}\n"
    "Stripe défaut : {stripe}\n"
    "Stripe par montant : {n_amounts} lien(s)\n"
    "Support : {support}\n\n"
    "⚙️ Configuration :\n"
    "/paiement on — activer les boutons (après agrément des liens)\n"
    "/paiement off — désactiver (retour au flux QR)\n"
    "/paiement paypal KomaraAgency — username paypal.me\n"
    "/paiement stripe https://... — lien Stripe par défaut\n"
    "/paiement stripe 150 https://... — lien pour le total 150€\n"
    "/paiement support https://t.me/... — lien support\n\n"
    "Tant que c'est OFF, « payer » garde l'ancien flux QR."
)


def cmd_paiement(bot, chat_id: int, args: str = "", lang: str = "fr") -> None:
    """Panneau de configuration admin des liens de paiement."""
    cfg = load_config()
    parts = args.split()
    if not parts:
        n = len(cfg.get("stripe_amounts") or {})
        bot.send_message(chat_id, ADMIN_HELP.format(
            etat="🟢 ACTIF" if cfg.get("enabled") else "🔴 EN ATTENTE DE VALIDATION",
            paypal=cfg.get("paypal") or "—",
            stripe=cfg.get("stripe") or "— (non configuré)",
            n_amounts=n,
            support=cfg.get("support") or "— (non configuré)",
        ))
        return
    key = parts[0].lower()
    if key in ("on", "off"):
        cfg["enabled"] = key == "on"
        save_config(cfg)
        bot.send_message(chat_id, "✅ Boutons paiement "
                       + ("ACTIVÉS. Teste « payer » avec un panier."
                          if cfg["enabled"] else
                          "désactivés — retour au flux QR."))
        return
    if key == "paypal" and len(parts) >= 2:
        cfg["paypal"] = parts[1].strip().rstrip("/")
        save_config(cfg)
        bot.send_message(chat_id, f"✅ PayPal → paypal.me/{cfg['paypal']}")
        return
    if key == "support" and len(parts) >= 2:
        cfg["support"] = parts[1].strip()
        save_config(cfg)
        bot.send_message(chat_id, f"✅ Support → {cfg['support']}")
        return
    if key == "stripe" and len(parts) >= 2:
        if len(parts) >= 3 and parts[1].isdigit():
            montant, url = parts[1], parts[2].strip()
            cfg.setdefault("stripe_amounts", {})[montant] = url
            save_config(cfg)
            bot.send_message(chat_id, f"✅ Stripe {montant}€ → {url}")
        else:
            cfg["stripe"] = parts[1].strip()
            save_config(cfg)
            bot.send_message(chat_id, f"✅ Stripe défaut → {cfg['stripe']}")
        return
    bot.send_message(chat_id, "❌ Commande invalide. Tape juste /paiement "
                              "pour l'aide.")
