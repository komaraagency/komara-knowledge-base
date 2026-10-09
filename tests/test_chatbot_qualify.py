# ──────────────────────────────────────────────────────────────────────────
# test_chatbot_qualify — captures Boss 09/10 (02:36-02:37)
#
#   Bot    : « Quel canal vous intéresse ? »   (sans numéros)
#   Client : « 2 »
#   Bot    : « Parfait 2 👌 C'est le plus rentable »   <- 2 = quoi ?
#   Client : « 3 »
#   Bot    : lead admin « Activité : 3 » + « Je transmets ça à l'équipe »
#
# Désormais : canaux numérotés, « 2 » = Telegram, « 3 »/« 5 » refusés comme
# activité (re-demande), JAMAIS de faux lead envoyé à l'équipe.
# ──────────────────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")

import actions
from unittest.mock import patch

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

class FakeBot:
    def __init__(self): self.sent = []
    def send_message(self, cid, text, **k): self.sent.append(str(text))
    def send_photo(self, *a, **k): pass

print("── 1. resolve_channel ──")
for raw, exp in [("1", "WhatsApp"), ("2", "Telegram"), ("3", "TikTok"),
                 ("4", "Facebook / Instagram"), ("Telegram", "Telegram"),
                 ("whatsapp", "WhatsApp"), ("sur watsap", "WhatsApp"),
                 ("tiktok stp", "TikTok"), ("insta", "Facebook / Instagram"),
                 ("5", ""), ("0", ""), ("blabla", ""), ("", ""), ("22", "")]:
    check(f"canal {raw!r} -> {exp!r}", actions.resolve_channel(raw) == exp, actions.resolve_channel(raw))

print("── 2. tunnel : « 2 » = Telegram, « 3 » refusé comme activité ──")
leads, admin = [], []
flow = {}
def save_flow(cid, f, s=None, d=None): flow.update({"flow": f, "step": s, "data": d})
with patch.object(actions, "_save_flow", save_flow), \
     patch.object(actions, "_clear_flow", lambda cid: flow.clear()), \
     patch.object(actions, "_insert_lead", lambda d: leads.append(d)), \
     patch.object(actions, "notify_admin", lambda *a, **k: admin.append(a)), \
     patch.object(actions, "get_client", lambda cid: {}):
    b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "channel", {}, "2", "fr")
    check("« 2 » -> étape business", flow.get("step") == "business", flow)
    check("message nomme Telegram (pas « Parfait 2 »)",
          "Telegram" in b.sent[-1] and "Parfait 2" not in b.sent[-1], b.sent[-1][:60])

    d = dict(flow["data"]); b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "business", d, "3", "fr")
    check("« 3 » refusé comme activité", "ne me dit pas ton activité" in b.sent[-1], b.sent[-1][:60])
    check("aucun faux lead", leads == [] and admin == [], (leads, admin))

    d = dict(flow["data"]); b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "business", d, "5", "fr")
    check("« 5 » refusé aussi, toujours pas de lead", leads == [], leads)

    d = dict(flow["data"]); b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "business", d, "boutique de vêtements", "fr")
    check("vraie activité -> 1 lead", len(leads) == 1, leads)
    check("lead propre", "Telegram" in leads[0]["need"] and "boutique" in leads[0]["need"], leads[0]["need"])
    check("clôture propose DEVIS/RDV (pas de question « voir tarifs »)",
          "DEVIS" in b.sent[-1] and "RDV" in b.sent[-1], b.sent[-1][:80])

print("── 3. canal invalide -> options numérotées ──")
flow.clear()
with patch.object(actions, "_save_flow", save_flow), patch.object(actions, "_clear_flow", lambda c: flow.clear()):
    b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "channel", {}, "5", "fr")
    check("« 5 » -> redemande avec 1️⃣ à 4️⃣", all(x in b.sent[-1] for x in ("1️⃣", "4️⃣")), b.sent[-1][:60])
    check("reste à l'étape canal", flow.get("step") == "channel", flow)

print("── 4. anti-blocage : après 2 re-demandes d'activité on accepte ──")
leads.clear()
with patch.object(actions, "_save_flow", save_flow), \
     patch.object(actions, "_clear_flow", lambda cid: flow.clear()), \
     patch.object(actions, "_insert_lead", lambda d: leads.append(d)), \
     patch.object(actions, "notify_admin", lambda *a, **k: None), \
     patch.object(actions, "get_client", lambda cid: {}):
    b = FakeBot()
    actions._step_chatbot_qualify(b, 1, "business", {"channel": "Telegram", "_activity_retries": 2}, "3", "fr")
    check("3e tentative -> tunnel terminé (jamais bloqué)", len(leads) == 1, leads)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
