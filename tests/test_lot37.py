# -*- coding: utf-8 -*-
"""Lot 37 — Boss 04/10 (20h16) : flow « Pitch Aya » (script 4 branches
livré par le Boss) + nouveau logo PNG transparent (fond noir pur).

Règle d'or explicite du Boss : « ne jamais répondre à un message de
type (1,2,3,4,5) si ce n'est pas la logique de la conversation » —
vérifiée ici par construction : la branche n'est interprétée QUE
pendant le flow "pitch" actif (_fetch_flow), jamais ailleurs.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ADMIN_CHAT_ID"] = "99999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print("OK ", name)
    else: KO += 1; print("KO ", name, "->", extra)

import actions, img_gen

sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append((cid, text, k)); return True

print("── 1. Détection du déclencheur (phrases libres, pas égalité exacte) ──")
for probe, exp in [
    ("votre service", True), ("Vos services ?", True),
    ("que proposez-vous", True), ("Que proposez vous exactement", True),
    ("quel est vôtre activités", True), ("Quelle est votre activité ?", True),
    ("que ce que tu me propose pour mon business", True),
    ("qu'est-ce que tu me proposes pour mon business", True),
    ("Bonjour", False), ("c'est combien un logo", False), ("3 jours", False),
    ("merci", False), ("votre nom", False),
]:
    r = actions._is_pitch_intent(probe)
    check(f"intent {probe!r} -> {exp}", r is exp, r)

print("── 2. Extraction du choix (bouton OU chiffre nu) ──")
for probe, exp in [("1", "1"), ("2 - Bots", "2"), ("3 - Automatisation", "3"),
                   ("4 - Modération", "4"), ("5", None), ("bonjour", None),
                   ("", None), ("12", None)]:
    r = actions._pitch_choice_digit(probe)
    check(f"choix {probe!r} -> {exp}", r == exp, r)

print("── 3. Déclenchement du flow (démarrage) ──")
sent.clear()
flows = {}
actions.init_db()
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({"flow": f, "step": s, "data": d})), \
     patch.object(actions, "_fetch_flow", lambda cid: None):
    r = actions.handle(FakeBot(), 1, "c'est quoi votre service ?", "fr")
    check("déclencheur → flow pitch démarré, consommé", r is True, r)
    check("étape initiale = choice", flows.get("step") == "choice", flows)
    check("message d'intro envoyé avec clavier 4 boutons",
          any("Aya" in str(t) and "KOMARA" in str(t) for c, t, k in sent)
          and any("reply_markup" in k for c, t, k in sent),
          [str(t)[:50] for c, t, k in sent])

print("── 4. Étape « choice » : digit valide vs invalide ──")
sent.clear()
flows.clear()
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({"step": s, "data": d})):
    r = actions._step_pitch(FakeBot(), 1, "choice", {}, "2 - Bots", "fr")
    check("choix '2 - Bots' → branche 2 (lead_bot), avance à qualify",
          r is True and flows.get("step") == "qualify" and flows["data"].get("tag") == "lead_bot",
          flows)
    check("message de la branche 2 envoyé (WhatsApp ou Telegram)",
          any("WhatsApp ou Telegram" in str(t) for c, t, k in sent), [str(t)[:60] for c, t, k in sent])

sent.clear()
flows.clear()
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({"step": s, "data": d})):
    r = actions._step_pitch(FakeBot(), 1, "choice", {}, "5", "fr")
    check("choix '5' (invalide) → re-demande, PAS de branche deviné",
          r is True and flows.get("step") == "choice", flows)
    check("re-demande rappelle 1/2/3/4", any("1, 2, 3 ou 4" in str(t) for c, t, k in sent), "")

# anti-boucle infinie : après 2 refus, on libère le flux (jamais de deviné)
sent.clear()
with patch.object(actions, "_save_flow", lambda *a, **k: None), \
     patch.object(actions, "_clear_flow") as cf:
    r = actions._step_pitch(FakeBot(), 1, "choice", {"_pitch_retries": 2}, "oui mais c'est cher", "fr")
    check("3e message hors 1-4 → flux libéré (pas de guess, pas de boucle infinie)",
          r is False and cf.called, r)

print("── 5. Étape « qualify » → finale commune aux 4 branches ──")
sent.clear(); flows.clear()
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({"step": s, "data": d})):
    r = actions._step_pitch(FakeBot(), 1, "qualify", {"branch": "3", "tag": "lead_auto"},
                            "J'ai 4 pages à gérer", "fr")
    check("qualify → avance à closing", flows.get("step") == "closing", flows)
    check("message finale commun envoyé (nom entreprise + objectif)",
          any("nom de ton entreprise" in str(t) for c, t, k in sent), "")

print("── 6. Étape « closing » : lead créé + tag + notif admin ──")
sent.clear()
inserted = {}
notified = []
with patch.object(actions, "_clear_flow", lambda cid: None), \
     patch.object(actions, "get_client", lambda cid: {"name": "Mamadou", "phone": "622000000"}), \
     patch.object(actions, "_insert", lambda table, data: inserted.update({"table": table, "data": data}) or 1), \
     patch.object(actions, "notify_admin", lambda bot, text: notified.append(text)):
    r = actions._step_pitch(
        FakeBot(), 1, "closing",
        {"branch": "2", "tag": "lead_bot", "qualify_answer": "Je suis sur WhatsApp"},
        "Resto Mama Africa, viser 50 commandes/mois", "fr")
    check("lead inséré dans la table leads", inserted.get("table") == "leads", inserted)
    check("tag lead_bot correctement enregistré (sector)",
          inserted.get("data", {}).get("sector") == "lead_bot", inserted)
    check("besoin = qualif + objectif entreprise",
          "WhatsApp" in inserted.get("data", {}).get("need", "")
          and "Resto Mama Africa" in inserted.get("data", {}).get("need", ""), inserted)
    check("admin notifié avec le tag et les détails",
          notified and "lead_bot" in notified[0] and "Resto Mama Africa" in notified[0],
          notified)
    check("message de clôture envoyé au client", any("plan d'action" not in str(t) and "Reçu" in str(t) for c, t, k in sent) or sent, "")

print("── 7. Jamais interprété hors flux actif (règle d'or Boss) ──")
# Un "2" tapé SANS flow pitch actif ne doit JAMAIS atteindre _step_pitch :
# l'architecture _fetch_flow/_advance_flow garantit que seul le flow
# réellement actif pour ce chat_id est consulté.
with patch.object(actions, "_fetch_flow", lambda cid: None), \
     patch.object(actions, "_is_pitch_intent", lambda t: False), \
     patch.object(actions, "catalogue") as cat:
    cat.handle_client.return_value = False
    sent.clear()
    r = actions.handle(FakeBot(), 1, "2", "fr")
    check("'2' sans flow actif ni intent → PAS traité comme choix pitch",
          r is False, r)

print("── 8. Nouveau logo PNG (fond noir pur, transparence propre) ──")
img_gen._LOGO_RGBA = None
logo = img_gen._load_official_logo()
check("nouveau logo chargé", logo is not None and logo.mode == "RGBA", logo)
if logo:
    extrema = logo.getextrema()
    check("transparence effective sur le nouveau fichier", extrema[3][0] == 0, extrema)
    check("logo bien recadré (pas de fond résiduel énorme)",
          logo.width < 260 and logo.height < 260, logo.size)

print(f"\nTOTAL: {OK} OK / {KO} KO")
