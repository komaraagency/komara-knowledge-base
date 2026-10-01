# -*- coding: utf-8 -*-
"""
test_lot24.py — régression LETTRE FINALE : Prix FIXE € + conversion locale.

Règle d'or : le prix de vente est TOUJOURS fixe en €. La conversion
locale (base EUR) est une INFO indicative, jamais un prix de vente.
Jamais USD comme base.

Vérifie :
  BUG 1  — priorité panier (ajouter/panier/payer/devis/je commence)
  BUG 2  — base EUR, jamais USD, pays inconnu → € fixe
  BUG 3  — « payer » → message config € + acompte 30€ + JE COMMENCE
  PART 2 — relance panier abandonné 10 min (unique, pas de spam)
  AFFICHAGE — € en premier, « Chez toi : ~X DEV (indicatif) »

Total attendu : 26 OK / 0 KO.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ADMIN_CHAT_ID", "99999")
_tmp = ROOT / "data_lot24"
import shutil
if _tmp.exists():
    shutil.rmtree(_tmp)
_tmp.mkdir(exist_ok=True)
os.environ["ACTIONS_DIR"] = str(_tmp)
os.environ["MEMORY_DIR"] = str(_tmp)

import actions            # noqa: E402
import cart_nudge         # noqa: E402
import catalogue          # noqa: E402
import commercial_db as cdb  # noqa: E402
import devis_engine       # noqa: E402
import qr_module          # noqa: E402

actions.init_db()
cdb.init_commercial_db()
cart_nudge.init_db()

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
        self.sent = []

    def send_message(self, chat_id, text, **k):
        self.sent.append((chat_id, text))

    def send_photo(self, chat_id, f, caption=None, **k):
        self.sent.append((chat_id, caption or "[photo]"))

    def last(self, chat_id=None):
        for c, t in reversed(self.sent):
            if chat_id is None or c == chat_id:
                return t
        return ""


# ════════════════════════════════════════════════════════════════════
print("── BUG 2 : base EUR, jamais USD ──")
conv = devis_engine.convert_devis("GN", 100)
check("GN 100€ → monnaie locale GNF (pas USD)",
      conv["currency"] == "GNF", conv)
check("GN : prix local cohérent (~937.000 GNF)",
      800000 < conv["price_local"] < 1100000, conv["price_local"])
conv_ma = devis_engine.convert_devis("MA", 100)
check("MA 100€ → MAD (~1.100)", conv_ma["currency"] == "MAD"
      and 900 < conv_ma["price_local"] < 1300, conv_ma)
conv_sn = devis_engine.convert_devis("SN", 100)
check("SN 100€ → XOF (~65.000)", conv_sn["currency"] == "XOF"
      and 55000 < conv_sn["price_local"] < 80000, conv_sn)
conv_fr = devis_engine.convert_devis("FR", 100)
check("FR 100€ → EUR, pas de conversion utile",
      conv_fr["currency"] == "EUR" and conv_fr["price_local"] == 100, conv_fr)
conv_xx = devis_engine.convert_devis("XY", 10)
check("pays inconnu → € FIXE (plus de fallback USD)",
      conv_xx["currency"] == "EUR" and conv_xx["symbol"] == "€"
      and conv_xx["price_local"] == 10, conv_xx)
check("base_currency = EUR partout",
      all(c["base_currency"] == "EUR" for c in
          (conv, conv_ma, conv_sn, conv_fr, conv_xx)))
check("affichage : ligne locale vide pour client €",
      devis_engine.local_info_line("FR", 100, "fr") == "")
line_gn = devis_engine.local_info_line("GN", 100, "fr")
check("affichage GN : « Chez toi ~ » + prix € fait foi",
      "Chez toi" in line_gn and ("FG" in line_gn or "GNF" in line_gn)
      and "fait foi" in line_gn, line_gn)

print("── BUG 1 : priorité panier (Screen 1) ──")
import rag_bot  # noqa: E402
bot = FakeBot()
rag_bot.bot.send_message = bot.send_message
CHAT = 4200
# flux ACTIF (le bug : 'ajouter 1' était avalé par le flux)
actions._save_flow(CHAT, "devis", "service", {})
rag_bot._process_text(CHAT, "ajouter 1", "fr")
check("'ajouter 1' pendant flux actif → panier",
      catalogue.cart_count(CHAT) == 1 and "ajouté au panier" in bot.last(),
      bot.last()[:50])
bot.sent.clear()
rag_bot._process_text(CHAT, "panier", "fr")
check("'panier' pendant flux actif → panier affiché",
      "Ton panier" in bot.last() or "panier" in bot.last().lower(),
      bot.last()[:40])
bot.sent.clear()
rag_bot._process_text(CHAT, "je commence", "fr")
check("'je commence' + panier → tunnel checkout",
      "finalise ta commande" in bot.last() or "commande" in bot.last().lower(),
      bot.last()[:40])

print("── BUG 3 : « payer » → message config € (Screen 7) ──")
cdb.insert_pending_quote(4201, "Fatou", "", "GN", "GNF", 936957, 100,
                         "chatbot", "Chatbot IA")
bot2 = FakeBot()
check("config envoyée (pas de QR)",
      qr_module.handle_pay_request(bot2, 4201, "fr") is True
      and "[QR]" not in bot2.last())
msg = bot2.last()
check("Total 100€ FIXE en premier", "100€ (fixe international)" in msg, msg[:60])
check("acompte 30€ fixe + info locale",
      "30€" in msg and "Acompte" in msg, msg)
check("Chez toi FG indicatif (symbole GNF)",
      "Chez toi" in msg and "FG" in msg, msg)
check("Paiement € bientôt dispo + JE COMMENCE +212701986219",
      "bientôt dispo" in msg and "JE COMMENCE" in msg
      and "+212701986219" in msg, msg)
cdb.insert_pending_quote(4202, "Carlos", "", "MA", "MAD", 1082, 100,
                         "chatbot", "Chatbot IA")
bot3 = FakeBot()
qr_module.handle_pay_request(bot3, 4202, "es")
check("config ES multilingue (presupuesto fijo)",
      "fijo internacional" in bot3.last() and "EMPIEZO" in bot3.last(),
      bot3.last()[:60])

print("── PARTIE 2.4 : relance panier abandonné 10 min ──")
bot4 = FakeBot()
catalogue.cmd_add(bot4, 4203, 2, "fr")
catalogue.show_cart(bot4, 4203, "fr")          # vue du panier → mark_seen
n = cart_nudge.process_nudges(bot4)
check("avant 10 min : aucune relance", n == 0, n)
with cart_nudge.DB_LOCK:
    cart_nudge.DB_CONN.execute(
        "UPDATE cart_watch SET seen_at=? WHERE chat_id='4203'",
        ((datetime.now(timezone.utc) - timedelta(minutes=11))
         .isoformat(timespec="seconds"),))
    cart_nudge.DB_CONN.commit()
n = cart_nudge.process_nudges(bot4)
check("après 10 min : relance envoyée", n == 1, n)
check("relance : prix € fixe + 20h",
      "100€" in bot4.last(4203) and "20h" in bot4.last(4203),
      bot4.last(4203)[:60])
n = cart_nudge.process_nudges(bot4)
check("relance unique (pas de spam)", n == 0, n)
catalogue.clear_cart(4203)
n = cart_nudge.process_nudges(bot4)
check("panier vidé → plus de relance", n == 0, n)

print("── Régression rapide flux devis (€ en tête) ──")
bot5 = FakeBot()
conv2 = devis_engine.convert_devis("SN", 150)
check("devis 150€ Sénégal : XOF info seulement",
      conv2["price_base"] == 150 and conv2["base_currency"] == "EUR", conv2)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
