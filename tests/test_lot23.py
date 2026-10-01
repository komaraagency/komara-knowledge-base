# -*- coding: utf-8 -*-
"""
test_lot23.py — régression Paiement Multi-Mode (PayPal / Carte / Support).

Points de spec vérifiés :
  1. Panier actuel conservé (ajouter/panier/devis, total €)
  2. « payer » → carte devis KA-{id}-{date} + 3 boutons URL
  3. PayPal principal, montant pré-rempli ({total}EUR)
  4. Capture PayPal sans QR → transfert admin
  5. Règle d'or : tout inactif tant que l'admin n'a pas validé (/paiement)

Total attendu : 22 OK / 0 KO.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ADMIN_CHAT_ID", "99999")
_tmp = ROOT / "data_lot23"
import shutil
if _tmp.exists():
    shutil.rmtree(_tmp)
_tmp.mkdir(exist_ok=True)
os.environ["ACTIONS_DIR"] = str(_tmp)
os.environ["MEMORY_DIR"] = str(_tmp)

import actions            # noqa: E402
import catalogue          # noqa: E402
import commercial_db as cdb  # noqa: E402
import payment_links as pl  # noqa: E402
import qr_module          # noqa: E402

actions.init_db()
cdb.init_commercial_db()

OK = KO = 0


def check(name, cond, info=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"OK  {name}")
    else:
        KO += 1
        print(f"KO  {name} -> {str(info)[:100]}")


class FakeBot:
    def __init__(self):
        self.sent = []          # (chat_id, text, markup)

    def send_message(self, chat_id, text, **k):
        self.sent.append((chat_id, text, k.get("reply_markup")))

    def send_photo(self, chat_id, f, caption=None, **k):
        self.sent.append((chat_id, caption or "[photo]", None))

    def last(self, chat_id=None, markup=False):
        for c, t, m in reversed(self.sent):
            if chat_id is None or c == chat_id:
                return (t, m) if markup else t
        return ("", None) if markup else ""


# ════════════════════════════════════════════════════════════════════
print("── 1. Panier actuel conservé ──")
bot = FakeBot()
prods = catalogue.active_products()
check("catalogue 4 offres bots/agents IA en €", len(prods) >= 4 and all(
    isinstance(p[3], (int, float)) for p in prods[:4]), len(prods))
catalogue.cmd_add(bot, 2001, 1, "fr")   # produit 1 : Bot Scripté
catalogue.cmd_add(bot, 2001, 3, "fr")   # produit 3 : Agent IA Premium
catalogue.cart_total
total = catalogue.cart_total(2001)
check("ajouter [num] → total panier €", total == prods[0][3] + prods[2][3],
      (total, prods[0][3], prods[2][3]))

print("── 2. Inactif par défaut (règle d'or agrément) ──")
cfg = pl.load_config()
check("config par défaut désactivée", cfg["enabled"] is False, cfg)
check("paypal.me KomaraAgency par défaut", cfg["paypal"] == "KomaraAgency", cfg)
check("« payer » → ancien flux QR (désactivé)",
      pl.send_payment_card(bot, 2001, "fr") is False)

print("── 3. /paiement : configuration admin ──")
pl.cmd_paiement(bot, 99999, "")
check("panneau : EN ATTENTE DE VALIDATION",
      "EN ATTENTE DE VALIDATION" in bot.last(99999))
pl.cmd_paiement(bot, 99999, "on")
check("/paiement on → activé", pl.load_config()["enabled"] is True)
pl.cmd_paiement(bot, 99999, "stripe 150 https://buy.stripe.com/test150")
pl.cmd_paiement(bot, 99999, "stripe https://buy.stripe.com/defaut")
pl.cmd_paiement(bot, 99999, "support https://t.me/komara_support")
cfg = pl.load_config()
check("stripe montant exact 150€", cfg["stripe_amounts"].get("150") ==
      "https://buy.stripe.com/test150", cfg["stripe_amounts"])
check("stripe défaut + support", cfg["stripe"] ==
      "https://buy.stripe.com/defaut" and cfg["support"] ==
      "https://t.me/komara_support", cfg)

print("── 4. « payer » → carte devis + 3 boutons URL ──")
bot2 = FakeBot()
check("carte envoyée", pl.send_payment_card(bot2, 2001, "fr") is True)
text, markup = bot2.last(2001, markup=True)
today = datetime.now(timezone.utc).strftime("%Y%m%d")
check("ID devis KA-{user}-{date}", f"KA-2001-{today}" in text, text[:60])
check("TOTAL en € affiché", f"TOTAL : {total:g}€" in text, text[:120])
check("demande capture après paiement", "capture d'écran" in text.lower())
rows = [b for row in markup.keyboard for b in row]
labels = [b.text for b in rows]
check("bouton 1 PayPal (Recommandé)", "PayPal (Recommandé)" in labels[0], labels)
check("PayPal : montant pré-rempli EUR", rows[0].url ==
      f"https://paypal.me/KomaraAgency/{total:g}EUR", rows[0].url)
check("bouton 2 Carte/PCS", "Carte / PCS" in labels[1], labels)
check("bouton 3 Support", "Support" in labels[2], labels)
check("URL support correct", rows[2].url == "https://t.me/komara_support",
      rows[2].url)

print("── 5. Stripe : montant exact > défaut > masqué ──")
# panier à 150€ exact → lien stripe 150
catalogue.clear_cart(2002)
for pid, name, _d, price in prods:
    if price == 150.0:
        with catalogue.DB_LOCK:
            catalogue.DB_CONN.execute(
                "INSERT INTO cart (chat_id, product_id, qty, added_at) "
                "VALUES (?,?,1,datetime('now'))", ("2002", pid))
            catalogue.DB_CONN.commit()
        break
bot3 = FakeBot()
pl.send_payment_card(bot3, 2002, "fr")
_, m3 = bot3.last(2002, markup=True)
urls3 = [b.url for r in m3.keyboard for b in r]
check("stripe exact 150€ utilisé", "https://buy.stripe.com/test150" in urls3,
      urls3)
# montant non répertorié → défaut
catalogue.clear_cart(2003)
with catalogue.DB_LOCK:
    catalogue.DB_CONN.execute(
        "INSERT INTO cart (chat_id, product_id, qty, added_at) "
        "VALUES (?,?,2,datetime('now'))", ("2003", prods[0][0]))
    catalogue.DB_CONN.commit()
bot4 = FakeBot()
pl.send_payment_card(bot4, 2003, "fr")
_, m4 = bot4.last(2003, markup=True)
urls4 = [b.url for r in m4.keyboard for b in r]
check("stripe défaut si montant inconnu",
      "https://buy.stripe.com/defaut" in urls4, urls4)
# pas de stripe du tout → bouton masqué
cfg = pl.load_config()
cfg["stripe"], cfg["stripe_amounts"] = "", {}
pl.save_config(cfg)
bot5 = FakeBot()
pl.send_payment_card(bot5, 2003, "fr")
_, m5 = bot5.last(2003, markup=True)
labels5 = [b.text for r in m5.keyboard for b in r]
check("sans stripe : bouton masqué, PayPal reste 1er",
      len(labels5) == 2 and "PayPal" in labels5[0], labels5)

print("── 6. Fin du flux devis → carte (multilingue) ──")
cdb.insert_pending_quote(2004, "Binta", "", "GN", "GNF", 234239, 25, "logo",
                         "Logo Pro")
sent_es = []
class BotES(FakeBot):
    def send_message(self, chat_id, text, **k):
        super().send_message(chat_id, text, **k)
        sent_es.append(text)
check("carte ES (usted, PRESUPUESTO)",
      pl.send_payment_card(BotES(), 2004, "es") and
      any("PRESUPUESTO" in t and "Elija su pago" in t for t in sent_es),
      sent_es[-1][:60])
check("« payer » sans panier/devis → False (flux QR)",
      pl.send_payment_card(bot, 8888, "fr") is False)

print("── 7. Capture PayPal sans QR → transfert admin ──")
import cv2
import numpy as np
# image sans QR
img = np.full((300, 300, 3), 255, dtype=np.uint8)
cv2.putText(img, "PAYPAL PAID 25 EUR", (10, 150),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
cap_path = str(_tmp / "capture_test.png")
cv2.imwrite(cap_path, img)
check("pas de QR détecté", qr_module.scan_receipt_qr(cap_path) is None)
bot6 = FakeBot()
check("capture transférée (paiement en attente)",
      pl.maybe_forward_capture(bot6, 2004, cap_path, "fr") is True)
admin_msg = bot6.last(99999)
check("admin reçoit contexte complet", all(k in admin_msg for k in
      ("CAPTURE PAIEMENT", "Binta", "25€", "2004")), admin_msg[:80])
check("client a la confirmation", "validation" in bot6.last(2004).lower())
# sans paiement en attente → False (message « pas de QR » classique)
check("capture sans paiement → pas de transfert",
      pl.maybe_forward_capture(bot6, 8888, cap_path, "fr") is False)

print("── 8. /paiement : accès admin uniquement ──")
import rag_bot  # noqa: E402  (init complet du routeur)
bot7 = FakeBot()
ok_admin = actions.handle(bot7, 99999, "/paiement", "fr")
check("/paiement accepté pour l'admin", ok_admin is True and
      any("PAIEMENT MULTI-MODE" in t for c, t, m in bot7.sent), bot7.last())
ok_client = actions.handle(bot7, 2004, "/paiement", "fr")
check("/paiement refusé pour un client", ok_client is True and
      any(c == 2004 for c, t, m in bot7.sent), "fail-closed vérifié")

# retour à l'état neutre pour les runs suivants
cfg = pl.load_config()
cfg["enabled"] = False
pl.save_config(cfg)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
