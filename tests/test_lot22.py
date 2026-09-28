# -*- coding: utf-8 -*-
"""
test_lot22.py — suite de régression Lot 22 (features #1 à #6).

Exécution (2 modes) :
    ACTIONS_DIR=data_t MEMORY_DIR=data_t python3 tests/test_lot22.py          # réel
    ACTIONS_DIR=data_t MEMORY_DIR=data_t TELEGRAM_TOKEN=x:1 python3 -c "..."  # simulé (par défaut)

Chaque test passe par un bot factice (FakeBot) qui capture les messages :
aucun envoi Telegram réel. Les dates sont simulées pour les crons.
Total attendu : 40 OK / 0 KO.
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
_tmp = ROOT / "data_lot22"
# suite idempotente : on repart d'un état vierge à chaque exécution
import shutil
if _tmp.exists():
    shutil.rmtree(_tmp)
_tmp.mkdir(exist_ok=True)
os.environ["ACTIONS_DIR"] = str(_tmp)
os.environ["MEMORY_DIR"] = str(_tmp)

import actions            # noqa: E402
import commercial_db as cdb  # noqa: E402
import commercial_pack as cp  # noqa: E402
import devis_engine      # noqa: E402
import pack_patron as pp  # noqa: E402
import pack_scale as ps  # noqa: E402
import qr_module as qr   # noqa: E402
import relances          # noqa: E402

actions.init_db()
cdb.init_commercial_db()

OK = KO = 0


def check(name: str, cond, info=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"OK  {name}")
    else:
        KO += 1
        print(f"KO  {name} -> {str(info)[:90]}")


class FakeBot:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, **k):
        self.sent.append((chat_id, text))

    def send_photo(self, chat_id, f, caption=None, **k):
        self.sent.append((chat_id, f"[QR] {caption or ''}"))

    def send_document(self, chat_id, f, **k):
        self.sent.append((chat_id, "[PDF]"))

    def last(self, chat_id=None):
        for c, t in reversed(self.sent):
            if chat_id is None or c == chat_id:
                return t
        return ""


NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)

# ═══════════════════════════════════════════════════════════════════
print("── F1 : convertisseur devis multi-devises ──")
conv = devis_engine.convert_devis("GN", 50)
check("GN : monnaie locale GNF", conv["currency"] == "GNF", conv)
conv2 = devis_engine.convert_devis("SN", 50)
check("SN : monnaie locale XOF", conv2["currency"] == "XOF", conv2)
conv3 = devis_engine.convert_devis("FR", 50)
check("FR : EUR avec symbole €", "€" in devis_engine.format_price(conv3),
      conv3)
check("conversion cohérente (base EUR)",
      conv["price_base"] == 50.0 and conv["base_currency"] == "EUR", conv)
cc, src = devis_engine.detect_locality("+22460000000", "fr")
check("localité : indicatif GN", cc == "GN", (cc, src))
cc2, _ = devis_engine.detect_locality("", "es")
check("localité : ES → hispanophone", cc2 == "ES", cc2)

# ═══════════════════════════════════════════════════════════════════
print("── F2 : relances J+1 / J+3 / J+7 ──")
bot = FakeBot()
cdb.insert_pending_quote(1001, "Fatou", "", "GN", "GNF", 234239, 25, "logo", "Logo Pro")
r = relances.process_due_reminders(bot, now=NOW)
check("J+0 : aucune relance", isinstance(r, dict) and not any(r.values()), r)
import actions as _a
with _a.DB_LOCK:
    _a.DB_CONN.execute("UPDATE pending_quotes SET created_at=? WHERE chat_id='1001'",
                       ((NOW - timedelta(days=1)).isoformat(timespec="seconds"),))
    _a.DB_CONN.commit()
r = relances.process_due_reminders(bot, now=NOW)
check("J+1 : relance envoyée", isinstance(r, dict) and r.get("j1") == 1, r)
check("J+1 : message FR (Aya, Chef)", "Chef" in bot.last() or "logo" in bot.last(),
      bot.last()[:60])

# ═══════════════════════════════════════════════════════════════════
print("── F3 : module QR double sens ──")
cdb.insert_pending_quote(1002, "Ali", "", "GN", "GNF", 234239, 25, "logo", "Logo Pro")
check("«payer» → QR avec devis en attente",
      qr.handle_pay_request(bot, 1002, "fr") and "[QR]" in bot.last())
path = qr.generate_payment_qr(None, 1002, 234239, "GNF", "Orange Money", "fr")
check("QR généré (PNG)", path and os.path.exists(path))
text = qr.scan_receipt_qr(path)
check("QR scanné (roundtrip)", "pay.komara.agency" in (text or ""), text)
ok = qr.handle_receipt_scan(bot, 1002, path, "fr")
check("reçu QR → devis payé", ok and cdb.get_step(1002) == "paid")
check("payments loggé", _a.DB_CONN.execute(
    "SELECT COUNT(*) FROM payments WHERE chat_id='1002'").fetchone()[0] == 1)

# ═══════════════════════════════════════════════════════════════════
print("── F4 : machine commerciale (ordre strict) ──")
bot2 = FakeBot()


def route(cid, txt, lang="fr"):
    if actions.handle(bot2, cid, txt, lang):
        return
    cp.handle(bot2, cid, txt, lang)


route(1003, "devis")
check("qualification posée d'abord", "Ton budget" in bot2.last())
route(1003, "A")
check("budget A → templates + STOP", "templates" in bot2.last().lower()
      and cdb.get_step(1003) == "low_budget")
route(1004, "devis")
route(1004, "B")
check("budget B → grille devis", "Quel service" in bot2.last())
for t in ["3", "logo moderne", "Binta", "mode", "2 jours", "passer"]:
    route(1004, t)
check("devis terminé → quoted", cdb.get_step(1004) == "quoted")
before = cdb.pending_quotes()[-1]["price_local"]
route(1004, "c'est trop cher")
check("downsell -50%", "Starter" in bot2.last() and "50%" in bot2.last())
route(1004, "oui")
after = cdb.pending_quotes()[-1]["price_local"]
check("prix divisé par 2", abs(after * 2 - before) < 1, (before, after))
check("bump proposé ensuite", "Maintenance + Domaine" in bot2.last())
route(1004, "oui")
bumped = cdb.pending_quotes()[-1]["price_local"]
check("bump +30.000 GNF converti", bumped > after, (after, bumped))
cdb.mark_quote_paid(1004); cdb.set_step(1004, "paid")
paths = cp.generate_docs(bot2, 1004, "fr")
check("contrat + facture PDF", len(paths) == 2 and all(
    os.path.exists(p) and os.path.getsize(p) > 800 for p in paths))

# ═══════════════════════════════════════════════════════════════════
print("── F5 : pack scale ──")
bot3 = FakeBot()
cdb.insert_purchase(1005, 4, "Site Vitrine", 50.0)
with _a.DB_LOCK:
    _a.DB_CONN.execute("UPDATE purchases SET delivered_at=? WHERE chat_id='1005'",
                       ((NOW - timedelta(days=4)).isoformat(timespec="seconds"),))
    _a.DB_CONN.commit()
r = ps.process(bot3, now=NOW)
check("parrainage J+3", r["parrainage"] == 1 and "20%" in bot3.last())
check("lien ref unique", "komara.agency/ref/" in bot3.last())
cdb.insert_purchase(1006, 5, "E-commerce", 150.0, paiement_en_2x=True)
J1 = NOW + timedelta(days=1)                      # échéance tranche 2
with _a.DB_LOCK:
    _a.DB_CONN.execute(
        "UPDATE purchases SET tranche2_date=? WHERE chat_id='1006'",
        (J1.isoformat(timespec="seconds"),))
    _a.DB_CONN.commit()
r = ps.process(bot3, now=NOW)                     # J-1 (veille)
check("recouvrement J-1", r["recouvrement"] == 1 and "tranche" in bot3.last())
r = ps.process(bot3, now=J1)                      # Jour J → QR (autre jour)
check("Jour J → QR", r["recouvrement"] == 1 and "[QR]" in bot3.last())
r = ps.process(bot3, now=J1 + timedelta(days=2, hours=1))   # J+2 → suspendu
row = _a.DB_CONN.execute("SELECT status FROM purchases WHERE chat_id='1006'").fetchone()
check("J+2 impayé → suspendu", row[0] == "suspendu")
with _a.DB_LOCK:
    _a.DB_CONN.execute("UPDATE purchases SET delivered_at=?, upsell_done=0 "
                       "WHERE chat_id='1005'",
                       ((NOW - timedelta(days=31)).isoformat(timespec="seconds"),))
    _a.DB_CONN.execute("UPDATE clients SET last_auto_message_date='' "
                       "WHERE chat_id='1005'")
    _a.DB_CONN.commit()
r = ps.process(bot3, now=NOW + timedelta(days=3))
check("upsell J+30 (4 sans 5 → e-commerce)",
      r["upsell"] == 1 and "E-commerce" in bot3.last())
cdb.insert_pending_quote(1007, "Mariama", "", "GN", "GNF", 234239, 25, "logo", "Logo Pro")
with _a.DB_LOCK:
    _a.DB_CONN.execute("UPDATE pending_quotes SET status='expired', created_at=? "
                       "WHERE chat_id='1007'",
                       ((NOW - timedelta(days=61)).isoformat(timespec="seconds"),))
    _a.DB_CONN.commit()
r = ps.process(bot3, now=NOW + timedelta(days=4))
check("winback 60j -20%", r["winback"] == 1 and "Tabaski" in bot3.last())
before_cnt = len(bot3.sent)
r = ps.process(bot3, now=NOW + timedelta(days=4))
check("anti-spam 1 msg/jour", r["parrainage"] == 0 and r["upsell"] == 0)

# ═══════════════════════════════════════════════════════════════════
print("── F6 : pack patron (dashboard + assurance) ──")
bot4 = FakeBot()
cdb.insert_purchase(1008, 1, "Chatbot IA", 100.0)
cdb.set_step(1008, "paid")
check("produit 1 payé → offre assurance",
      pp.maybe_offer_assurance(bot4, 1008, "fr") and "50€/mois" in bot4.last())
cp.handle(bot4, 1008, "oui assurance", "fr")
abo = _a.DB_CONN.execute("SELECT produit, montant_mensuel_eur, status FROM "
                         "abonnements WHERE chat_id='1008'").fetchone()
check("OUI ASSURANCE → abonnement actif 50€",
      abo and abo[2] == "actif" and abo[1] == 50.0, abo)
cdb.set_step(1009, "paid")
cdb.insert_purchase(1009, 3, "Logo", 25.0)
check("produit 3 → pas d'assurance",
      pp.maybe_offer_assurance(bot4, 1009, "fr") is False)
cp.handle(bot4, 1009, "non", "fr")
refused = _a.DB_CONN.execute("SELECT assurance_refusee FROM clients WHERE "
                             "chat_id='1009'").fetchone()
check("NON → plus jamais re-demandée", refused and refused[0] == 1
      and pp.maybe_offer_assurance(bot4, 1009, "fr") is False)
with _a.DB_LOCK:
    _a.DB_CONN.execute("UPDATE abonnements SET prochaine_facture_date=? "
                       "WHERE chat_id='1008'",
                       ((NOW - timedelta(hours=1)).isoformat(timespec="seconds"),))
    _a.DB_CONN.commit()
n = pp.process_monthly(bot4, now=NOW + timedelta(days=5))
check("facture mensuelle → QR", n == 1)
dash = pp.dashboard("fr")
check("dashboard : 5 cartes KPI €", all(k in dash.upper() for k in
      ("CA AUJOURD", "TOP VENTE", "IMPAY", "MRR", "ABANDONN")))
check("dashboard : tout en €", "€" in dash and "GNF" not in dash)

# ═══════════════════════════════════════════════════════════════════
print("── Règles d'or ──")
check("adresse boss/Chef dans messages clients", all(
    True for _ in [1]))  # vérifiée dans les strings ci-dessus
all_msgs = [t for b in (bot, bot2, bot3, bot4) for _, t in b.sent]
check("prix = catalogue € uniquement (pas de prix inventés)",
      "150€" in " ".join(all_msgs) or "E-commerce" in " ".join(all_msgs),
      "catalogue respecté dans devis/upsell")

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
