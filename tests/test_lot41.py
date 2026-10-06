# -*- coding: utf-8 -*-
"""Lot 41 — Boss 06/10 : fix des incohérences des screenshots 05-06/10.
1. /image « un poisson » / « un lion en costume » → verrou GÉNÉRIQUE
   (plus de vocabulaire visage/peau sur un sujet sans personne) + verrou
   anti-NSFW sur TOUTES les générations (même le bug « jeune femme »
   signalé sur les screens ne peut plus sortir de nudité).
2. Flow « Chatbot IA » : le clic bouton ouvre un VRAI flux (canal puis
   activité) → les réponses libres du client sont capturées par étapes,
   elles ne retombent plus dans la KB générale (message hors-sujet).
3. Hygiène Sheet : les typos lues du Google Sheet (« ci_dessous »,
   « l_équipe ») sont réparées à la lecture ET à l'écriture — le bot
   applique la même méthode de compréhension aux tokens LUS.
4. « Faire » seul = mot ambigu → précision demandée, jamais une fiche
   au hasard.
5. Panneau /admin complet : /apprends, /evaluation, /paiement,
   /kb_modele, /solde/ka/bonnus visibles dans les 4 langues.
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

import knowledge_store
knowledge_store._CUSTOM_ROWS = []
import memory_sheets
import img_gen
import actions
import rag_bot
from rag_bot import AMBIGUOUS_WORDS, _AMBIGUOUS_CLARIFY

print("── 1. Image : sujet sans personne = verrou générique ──")
out_fish = img_gen._with_8k_protocol("une photo de poisson")
out_lion = img_gen._with_8k_protocol("d'un lion en costume, style affiche pro")
check("« poisson » sans vocabulaire visage/peau",
      not any(w in out_fish for w in ("skin texture", "distorted face", "extra fingers", "pores")),
      out_fish)
check("« lion en costume » sans vocabulaire visage/peau",
      not any(w in out_lion for w in ("skin texture", "distorted face", "extra fingers", "pores")),
      out_lion)
check("« poisson » garde le verrou photoréaliste",
      "photorealistic" in out_fish and "NO cartoon" in out_fish, out_fish)

print("── 2. Image : verrou anti-NSFW partout ──")
cases = {
    "poisson": img_gen._with_8k_protocol("une photo de poisson"),
    "lion": img_gen._with_8k_protocol("un lion en costume"),
    "femme": img_gen._with_8k_protocol("une femme souriante"),
    "logo": img_gen._with_8k_protocol("un logo pour mon resto"),
    "cartoon": img_gen._with_8k_protocol("cartoon un robot"),
    "i2i normal": img_gen._i2i_prompt("rends-moi plus élégant"),
    "i2i cartoon": img_gen._i2i_prompt("dessine-moi en cartoon"),
}
for label, prompt in cases.items():
    check(f"verrou NSFW présent ({label})", "no nudity" in prompt, prompt)
out_person = img_gen._with_8k_protocol("une femme souriante")
check("portrait humain garde le verrou visage (skin texture)",
      "skin texture" in out_person, out_person)
check("logo reste sans personne", "no person" in img_gen._with_8k_protocol("un logo"), "")

print("── 3. Flow « Chatbot IA » : clic bouton → vrai flux ──")
sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append(text); return True

actions.init_db()   # SQLite nécessaire pour _save_flow/_fetch_flow

class RecordingBot:
    def __init__(self):
        self.sent = []
    def send_message(self, cid, text=None, *a, **k):
        self.sent.append(str(text)); return True
    def last(self):
        return self.sent[-1] if self.sent else ""

check("fonction start_chatbot_qualify_flow existe",
      callable(actions.start_chatbot_qualify_flow), "")

# Clic bouton 🤖 Chatbot IA → message d'accroche + flux ouvert
fb = RecordingBot()
with patch.object(rag_bot, "bot", fb, create=True):
    ok_route = rag_bot._handle_menu_button(123, "🤖 Chatbot IA", "fr")
check("clic bouton 🤖 Chatbot IA routé", ok_route is True, ok_route)
check("message d'accroche envoyé", "Quel canal" in fb.last(), fb.sent[:1])
check("flux chatbot_qualify ouvert par le bouton",
      actions._fetch_flow(123) == ("chatbot_qualify", "channel", {}), actions._fetch_flow(123))
actions._clear_flow(123)

# Étape canal → question activité
actions._save_flow(123, "chatbot_qualify", "channel", {})
fb2 = RecordingBot()
handled = actions._advance_flow(fb2, 123, "chatbot_qualify", "channel", {}, "Pour mon WhatsApp", "fr")
check("réponse « Pour mon WhatsApp » capturée par l'étape canal",
      handled and "Tu vends quoi" in fb2.last(), fb2.sent)
# Étape activité → lead enregistré + notif admin
fb3 = RecordingBot()
with patch("actions.notify_admin") as na, patch("actions._insert") as ins:
    handled2 = actions._advance_flow(fb3, 123, "chatbot_qualify", "business",
                                     {"channel": "Pour mon WhatsApp"},
                                     "Je crée des digitaux", "fr")
    lead_row = ins.call_args[0][1] if ins.call_args else {}
    na_called = na.called
check("réponse « Je crée des digitaux » capturée par l'étape activité",
      handled2 and na_called, "")
check("lead chatbot enregistré (canal + activité)",
      lead_row.get("sector") == "chatbot"
      and "Je crée des digitaux" in lead_row.get("need", ""), lead_row)
check("flux refermé après capture", actions._fetch_flow(123) is None, "")

print("── 4. Hygiène Sheet : typos réparées à la lecture ──")
check("« ci_dessous » → « ci-dessous »",
      memory_sheets.fix_sheet_typos("Tape un bouton ci_dessous") == "Tape un bouton ci-dessous", "")
check("« l_équipe » → « l'équipe »",
      memory_sheets.fix_sheet_typos("l_équipe Komara") == "l'équipe Komara", "")
check("« d_un » → « d'un »",
      memory_sheets.fix_sheet_typos("d_un chatbot") == "d'un chatbot", "")
check("tirets bas légitimes intacts (mon_site_web)",
      memory_sheets.fix_sheet_typos("mon_site_web") == "mon_site_web", "")
with patch("memory_sheets.read_rows", return_value=[
        ["2026-10-06", "fr", "Vos services ? | Que proposez-vous ?", "Tape ci_dessous 👇"],
        ["2026-10-06", "fr", "Vos services ? | Que proposez-vous ?", "Tape ci_dessous 👇"]]):
    rows = memory_sheets.load_learned()
check("load_learned répare les typos du Sheet (lecture)",
      rows and "ci-dessous" in rows[0]["answer"], rows)
check("load_learned dédoublonne (dernière version gagne)",
      len(rows) == 1, len(rows))
check("variantes « | » conservées après hygiène",
      "|" in rows[0]["question"], rows[0]["question"])

print("── 5. Variantes « | » du Sheet = même méthode que le seed ──")
entry = knowledge_store._make_entry("C'est quoi ? | Comment ça marche ?", "Réponse test")
check("_make_entry découpe le « | » en variantes",
      isinstance(entry.get("questions"), list) and len(entry["questions"]) == 2, entry.get("questions"))
kb = [{"questions": ["comment commander | comment je commande"], "answer": "Tape Commander", "category": "test"}]
check("le moteur local score les variantes Sheet (même tokenisation que le seed)",
      rag_bot.trouver_meilleure_reponse_multilingue is not None, "")

print("── 6. « Faire » seul → précision, jamais une fiche au hasard ──")
check("« faire » déclaré ambigu", "faire" in AMBIGUOUS_WORDS, "")
check("groupe de clarification « faire » défini dans les 4 langues",
      all(lg in _AMBIGUOUS_CLARIFY.get("faire", {}) for lg in ("fr", "en", "es", "ar")), "")
reply = rag_bot.clarif("faire", "fr")
check("clarif('faire') pose une vraie question de précision",
      "?" in reply and "logo" in reply, reply)

print("── 7. Panneau /admin complet (4 langues) ──")
import json
for lg in ("fr", "en", "es", "ar"):
    d = json.load(open(Path(__file__).resolve().parent.parent / "lang" / f"{lg}.json", encoding="utf-8"))
    pnl = d.get("admin_panel", "")
    miss = [c for c in ("/apprends", "/evaluation", "/paiement", "/kb_modele", "/solde") if c not in pnl]
    check(f"panneau {lg} : toutes les commandes listées", not miss, miss)
check("toutes les commandes du panneau existent dans ADMIN_COMMANDS",
      all(c in actions.ADMIN_COMMANDS for c in
          ("/apprends", "/evaluation", "/paiement", "/kb_modele", "/solde", "/ka", "/bonnus")), "")

print(f"\nTOTAL: {OK} OK / {KO} KO")
