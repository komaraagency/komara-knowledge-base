# -*- coding: utf-8 -*-
"""Lot 42 — Modifications Boss 06/10 (matin) :
1. kb_import : séparateur « || » prioritaire (les questions à variantes
   « Q1 | Q2 || réponse » ne sont plus coupées au premier « | »).
2. aya_pipeline step1 : la VRAIE question est conservée (pas la clé
   casefoldée), « || » parasite → « | », dernière version gagne.
3. memory_sheets : limites élargies (question 500, réponse 5000,
   conversation 2000) + hygiène fix_sheet_typos AVANT écriture.
4. actions : chaque lead = INSERT local + append Google Sheet « Leads »
   (définitif), asynchrone et tolérant.
5. pack_patron : SELECT assurance_refusee blindé (jamais de crash sur
   un déploiement frais).
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
import actions
import kb_import
import aya_pipeline

print("── 1. kb_import : « || » prioritaire sur « | » ──")
ents = kb_import.parse_txt("C'est quoi ? | Comment ça marche ? || Un agent IA répond 24h/24 👍")
check("question à variantes non coupée au premier « | »",
      len(ents) == 1 and ents[0][0] == "C'est quoi ? | Comment ça marche ?"
      and ents[0][1] == "Un agent IA répond 24h/24 👍", ents)
ents2 = kb_import.parse_txt("Quel est le prix ? | 150€ pour un logo")
check("format legacy « | » simple toujours accepté",
      len(ents2) == 1 and ents2[0] == ("Quel est le prix ?", "150€ pour un logo"), ents2)
ents3 = kb_import.parse_txt("Ligne sans séparateur du tout")
check("ligne sans séparateur ignorée", ents3 == [], ents3)

print("── 2. aya_pipeline : vraie question + q_clean + dernière version ──")
import aya_seed
raw = aya_pipeline.step1_donnees()
check("step1 renvoie des (question, réponse)", all(len(x) == 2 for x in raw), raw[:2])
seed_q = aya_seed.SEED_QR[0][0]
found = [q for q, a in raw if q.casefold() == seed_q.strip().casefold()]
check("la VRAIE question (casse/accents d'origine) est conservée",
      found and found[0] == seed_q.strip(), (seed_q, found[:1]))
# Dernière version gagne : RAM remplace le seed sur la même question
knowledge_store._CUSTOM_ROWS = [
    {"question": seed_q, "answer": "NOUVELLE RÉPONSE test"},
    {"question": "Question avec || double pipe || variante", "answer": "A"},
]
raw2 = aya_pipeline.step1_donnees()
rep = [a for q, a in raw2 if q.casefold() == seed_q.strip().casefold()]
check("la version RAM (plus récente) écrase le seed", rep == ["NOUVELLE RÉPONSE test"], rep)
qdp = [q for q, a in raw2 if "double pipe" in q]
check("« || » parasite redevient « | » dans la question",
      qdp and "||" not in qdp[0] and "Question avec | double pipe | variante" == qdp[0], qdp)
knowledge_store._CUSTOM_ROWS = []

print("── 3. memory_sheets : limites élargies + hygiène avant écriture ──")
calls = []
with patch("memory_sheets.get_memory_sheet_id", return_value="SHEET123"), \
     patch("memory_sheets._token", return_value="tok"), \
     patch("memory_sheets.append_rows", side_effect=lambda tab, rows: calls.append((tab, rows)) or True):
    memory_sheets.save_learned("Tape un bouton ci_dessous " + "x" * 900,
                                "Réponse très longue " + "y" * 6000, "fr")
tab, rows = calls[0]
check("écrit dans l'onglet Dialogues", tab == "Dialogues", tab)
q_saved, a_saved = rows[0][2], rows[0][3]
check("hygiène appliquée à l'écriture (ci_dessous → ci-dessous)",
      "ci-dessous" in q_saved, q_saved[:40])
check("question tronquée à 500", len(q_saved) == 500, len(q_saved))
check("réponse tronquée à 5000", len(a_saved) == 5000, len(a_saved))
# batch kb_import : mêmes limites
calls.clear()
knowledge_store.LANG_RESOURCES = {"fr": {"kb": []}}
with patch("memory_sheets.get_memory_sheet_id", return_value="SHEET123"), \
     patch("memory_sheets.append_rows", side_effect=lambda tab, rows: calls.append((tab, rows)) or True), \
     patch("knowledge_store.refresh_resources", return_value=False):
    knowledge_store.learn_entries_batch([("Q ? ", "A " + "z" * 6000)], "fr")
tab2, rows2 = calls[0]
check("batch : réponse tronquée à 5000", len(rows2[0][3]) == 5000, len(rows2[0][3]))

conv_calls = []
with patch("memory_sheets._append_async", side_effect=lambda tab, row: conv_calls.append((tab, row))):
    memory_sheets.log_conversation(123, "Client", "user", "M" * 3000, "fr")
tab3, row3 = conv_calls[0]
check("log_conversation : onglet Conversations, contenu 2000",
      tab3 == "Conversations" and len(row3[4]) == 2000, (tab3, len(row3[4])))

print("── 4. Lead = INSERT local + Google Sheet « Leads » ──")
actions.init_db()
lead_calls = []
with patch("memory_sheets._append_async", side_effect=lambda tab, row: lead_calls.append((tab, row))):
    rid = actions._insert_lead({"chat_id": "42", "name": "Test", "phone": "+224",
                                "sector": "chatbot", "need": "bot WhatsApp",
                                "budget": "", "created_at": "2026-10-06T05:00:00"})
check("lead local inséré (row id)", rid >= 1, rid)
check("miroir Google Sheet « Leads » parti",
      lead_calls and lead_calls[0][0] == "Leads", lead_calls[:1])
sheet_row = (lead_calls[0][1][0] if lead_calls and lead_calls[0][1] else [])
check("ligne Sheet Leads complète (7 colonnes)",
      len(sheet_row) == 7 and sheet_row[1] == "42" and sheet_row[4] == "chatbot", sheet_row)
# tolérance : Google en panne → le lead local survit quand même
err = []
with patch("memory_sheets._append_async", side_effect=Exception("Google HS")):
    try:
        actions._insert_lead({"chat_id": "43", "name": "Panne", "phone": "",
                              "sector": "auto", "need": "test panne",
                              "budget": "", "created_at": "2026-10-06T05:01:00"})
    except Exception as e:
        err.append(e)
check("Google en panne → AUCUNE exception (lead local sauvé)", not err, err)
with actions.DB_LOCK:
    n = actions.DB_CONN.execute("SELECT COUNT(*) FROM leads WHERE chat_id='43'").fetchone()[0]
check("lead local bien présent malgré la panne Google", n >= 1, n)

print("── 5. pack_patron : SELECT assurance blindé ──")
import pack_patron
class FakeBot:
    def __init__(self): self.sent = []
    def send_message(self, cid, text=None, *a, **k): self.sent.append(text); return True
class BoomConn:
    def execute(self, *a, **k): raise Exception("no such column: assurance_refusee")
fb = FakeBot()
with patch.object(actions, "DB_CONN", BoomConn()), \
     patch.object(pack_patron, "_produit_id_of", return_value=0):
    try:
        pack_patron.maybe_offer_assurance(fb, 777, "fr")
        crashed = False
    except Exception:
        crashed = True
check("SELECT en échec → pas de crash (pas de produit éligible = pas d'offre)",
      not crashed and not fb.sent, (crashed, fb.sent))
with patch.object(actions, "DB_CONN", BoomConn()), \
     patch.object(pack_patron, "_produit_id_of", return_value=pack_patron.CHATBOT_PROD):
    offered = pack_patron.maybe_offer_assurance(fb, 777, "fr")
check("blindé : l'offre Maintenance part quand même (jamais refusée = offrable)",
      offered is True and fb.sent, (offered, fb.sent[:1]))

print(f"\nTOTAL: {OK} OK / {KO} KO")
