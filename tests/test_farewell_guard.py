# ──────────────────────────────────────────────────────────────────────────
# test_farewell_guard — « Avant de partir » seulement aux vraies fins
# (Boss 09/10)
#
# Capture : le client visite le portfolio, dit « Merci », et le bot sort
# « Avant de partir : ... À tout de suite ! » comme s'il partait.
# Pire (découvert au test) : « Merci » pendant le devis était enregistré
# comme ACTIVITÉ -> devis « activité : Merci ».
#
# Règles :
#   1. Remerciement seul pendant une conversation ACTIVE (flux, question
#      en attente, liste envoyée) -> petit ack, JAMAIS la clôture.
#   2. « Merci » ne nourrit JAMAIS un flux actif (pas de devis « Merci »).
#   3. Vrais départs (« au revoir », « bye »...) -> clôture inchangée.
# ──────────────────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")

import farewell_guard as FG

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

print("── 1. Détection remerciement seul ──")
for txt, exp in [("merci", True), ("Merci beaucoup", True), ("merci !", True),
                 ("ok merci", False), ("super", True), ("thanks", True),
                 ("thank you", True), ("gracias", True), ("شكرا", True),
                 ("au revoir", False), ("merci pour le devis", False),
                 ("", False), ("merci au revoir", False)]:
    check(f"is_thanks_only({txt!r})={exp}", FG.is_thanks_only(txt) == exp, FG.is_thanks_only(txt))

print("── 2. Détection vrai départ ──")
for txt, exp in [("au revoir", True), ("bye", True), ("à plus tard", True),
                 ("bonne journée", True), ("ciao", True), ("adios", True),
                 ("مع السلامة", True), ("merci", False), ("salut", False)]:
    check(f"is_leaving({txt!r})={exp}", FG.is_leaving(txt) == exp, FG.is_leaving(txt))

print("── 3. conversation_active : question en attente = actif ──")
check("question du bot -> actif", FG.conversation_active(
    555001, "Tu vends quoi exactement ?"), "")
check("liste numérotée -> actif", FG.conversation_active(
    555002, "Portfolio KOMARA 💎\n1️⃣ photo\n2️⃣ photo"), "")
check("invite 👇 -> actif", FG.conversation_active(
    555003, "Tape le numéro 👇"), "")
check("simple info neutre -> pas actif",
      FG.conversation_active(555004, "Voici l'image de ton logo.") is False, "")
check("info tarifs sans question -> pas actif",
      FG.conversation_active(555005, "Voici nos tarifs : 50€.") is False, "")

print("── 4. ack par langue (règle : réponse dans la langue du client) ──")
check("fr", "plaisir" in FG.mid_thanks_reply("fr"), "")
check("en", "welcome" in FG.mid_thanks_reply("en"), "")
check("es", "gusto" in FG.mid_thanks_reply("es"), "")
check("ar", "الرحب" in FG.mid_thanks_reply("ar"), "")
check("langue inconnue -> fr", "plaisir" in FG.mid_thanks_reply("zz"), "")

print("── 5. scénario bout en bout : pas de devis « Merci » ──")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch
import actions

sent = []
class FakeBot:
    def send_message(self, cid, text, **k): sent.append(str(text))
    def send_photo(self, *a, **k): pass

devis_done = []
flow = {}
def save_flow(cid, f, s=None, d=None): flow.update({"flow": f, "step": s, "data": d or {}})

with patch.object(actions, "_save_flow", save_flow), \
     patch.object(actions, "_clear_flow", lambda cid: flow.clear()), \
     patch.object(actions, "_fetch_flow", lambda cid: (("devis", flow.get("step", "service"), flow.get("data", {})) if flow else None)), \
     patch.object(actions, "_finish_devis", lambda *a, **k: devis_done.append(a)):
    # devis actif à l'étape activité, le client dit « Merci »
    flow.update({"flow": "devis", "step": "activity", "data": {"service": "Bot Scripté"}})
    b = FakeBot()
    handled = actions.handle(b, 1, "Merci", "fr")
    check("« Merci » géré sans avancer le flux", handled is True, handled)
    check("ack envoyé (pas de devis)", "Avec plaisir" in sent[-1] and not devis_done, sent[-1][:60])
    check("flux TOUJOURS à l'étape activité", flow.get("step") == "activity", flow)
    # puis la vraie réponse avance le flux normalement
    b = FakeBot()
    actions.handle(b, 1, "boutique de vêtements", "fr")
    check("ensuite « boutique » -> devis OK", len(devis_done) == 1, devis_done)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
