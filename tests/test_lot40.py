# -*- coding: utf-8 -*-
"""Lot 40 — Boss 05/10 : évaluation hebdomadaire automatique de la
mémoire Aya (aya_eval_cron.py), même pattern que weekly_report.py.
Chaque lundi 08h00 UTC, pipeline complet + rapport à l'admin.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
import aya_pipeline
import aya_eval_cron

print("── 1. Message de rapport ──")
report = aya_pipeline.run_evaluation("fr")
msg = aya_eval_cron.build_message(report)
check("rapport mentionne la précision", "Précision" in msg, msg[:60])
check("rapport mentionne le nombre d'entrées indexées",
      str(report["pretrain"]["docs"]) in msg, msg[:60])
check("rapport titré « ÉVALUATION HEBDO »", "ÉVALUATION HEBDO" in msg, msg[:40])

print("── 2. send_weekly_evaluation : envoi + miroir des manques ──")
sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append((cid, text)); return True

unanswered = []
import actions
with patch.object(actions, "ADMIN_CHAT_ID", 99999), \
     patch("aya_pipeline.run_evaluation", return_value=report), \
     patch("memory_sheets.log_unanswered", side_effect=lambda q, l, c, n: unanswered.append(q)):
    aya_eval_cron.send_weekly_evaluation(FakeBot())
check("rapport envoyé à l'admin (99999)",
      sent and sent[0][0] == 99999, sent)
check("manques (s'il y en a) tracés dans Questions sans réponse",
      len(unanswered) == len(report["posttrain"]["misses"]), unanswered)

print("── 3. Sans ADMIN_CHAT_ID : rien n'est envoyé (fail-closed) ──")
sent.clear()
with patch.object(actions, "ADMIN_CHAT_ID", 0):
    aya_eval_cron.send_weekly_evaluation(FakeBot())
check("aucun envoi sans admin configuré", sent == [], sent)

print("── 4. Anti-crash : pipeline en échec ne bloque pas le thread ──")
sent.clear()
with patch.object(actions, "ADMIN_CHAT_ID", 99999), \
     patch("aya_pipeline.run_evaluation", side_effect=RuntimeError("boom")):
    try:
        aya_eval_cron.send_weekly_evaluation(FakeBot())
        crashed = False
    except Exception:
        crashed = True
check("pipeline HS → pas d'exception propagée (thread démon protégé)",
      not crashed, "")
check("aucun message à moitié envoyé en cas d'échec", sent == [], sent)

print("── 5. Calcul du prochain lundi 08h00 UTC ──")
secs = aya_eval_cron._next_monday_8am()
check("délai positif et borné à 7 jours + 1s",
      0 < secs <= 7 * 24 * 3600 + 1, secs)
target = datetime.now(timezone.utc) + timedelta(seconds=secs)
check("la cible calculée tombe bien un lundi 08h00 UTC",
      target.weekday() == 0 and target.hour == 8 and target.minute == 0,
      (target.weekday(), target.hour, target.minute))

print(f"\nTOTAL: {OK} OK / {KO} KO")
