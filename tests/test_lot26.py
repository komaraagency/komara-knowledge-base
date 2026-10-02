# -*- coding: utf-8 -*-
"""test_lot26.py — Lot 26 : anti-double-envoi + règle « ponctuation seule ».

Corrige le bug signalé (screenshots 29/09, 03:33) : le bot envoyait 2
(parfois 3) réponses différentes pour un seul message client, typiquement
juste après un redéploiement Railway (chevauchement ancien/nouveau worker
qui reçoivent tous les deux le même update Telegram).

Vérifie :
  1. Un même (chat_id, message_id) n'est JAMAIS traité deux fois, même
     appelé deux fois de suite (`handle_message`) — 0 envoi supplémentaire.
  2. Un même clic bouton (callback_query.id) n'est jamais rejoué deux fois.
  3. Un message composé UNIQUEMENT de ponctuation (« [[ », « ... », « / »,
     « ' », etc.) reçoit la réponse fixe dédiée, dans les 4 langues.
  4. Un message avec de VRAIES lettres/chiffres/emoji n'est JAMAIS
     confondu avec la règle « ponctuation seule » (pas de faux positif).
Total attendu : 14 OK / 0 KO.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ACTIONS_DIR"] = str(ROOT / "data_lot26")
os.environ.setdefault("MEMORY_DIR", str(ROOT / "data_lot26"))
import shutil as _sh
_sh.rmtree(str(ROOT / "data_lot26"), ignore_errors=True)
os.environ["MEMORY_DIR"] = str(ROOT / "data_lot26")

import logging  # noqa: E402
logging.disable(logging.INFO)
import rag_bot as rb  # noqa: E402

OK = KO = 0


def check(name, cond, info=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"OK  {name}")
    else:
        KO += 1
        print(f"KO  {name} -> {str(info)[:120]}")


rb.init_memory_db()
SENT: list[tuple[int, str]] = []
rb.bot.send_message = lambda c, t, **k: SENT.append((c, t))
rb.bot.answer_callback_query = lambda *a, **k: None


class _Chat:
    def __init__(self, cid):
        self.id = cid


class _Msg:
    def __init__(self, chat_id, mid, text):
        self.chat = _Chat(chat_id)
        self.message_id = mid
        self.text = text
        self.voice = self.audio = self.document = None
        self.location = self.photo = None
        self.from_user = types.SimpleNamespace(first_name="Test")


# 1. Anti-doublon message : même message_id envoyé 2x → 1 seul envoi
SENT.clear()
rb.handle_message(_Msg(20001, 111, "Bonjour"))
n1 = len(SENT)
check("1er message traité normalement", n1 == 1, n1)
rb.handle_message(_Msg(20001, 111, "Bonjour"))
n2 = len(SENT)
check("doublon EXACT (même message_id) ignoré", n2 == n1, f"{n1} -> {n2}")

# nouveau message_id (vrai nouveau message) → doit bien répondre
rb.handle_message(_Msg(20001, 112, "Salut"))
n3 = len(SENT)
check("nouveau message_id traité normalement", n3 == n2 + 1, f"{n2} -> {n3}")

# 2. Anti-doublon callback_query : même call.id rejoué 2x
class _Call:
    def __init__(self, cid, chat_id):
        self.id = cid
        self.message = types.SimpleNamespace(chat=_Chat(chat_id))
        self.data = "noop"


import catalogue  # noqa: E402
_orig_handle_callback = catalogue.handle_callback
calls_seen = []
catalogue.handle_callback = lambda bot, call, lang: calls_seen.append(call.id)

rb.handle_callback_query(_Call("cbA", 20002))
rb.handle_callback_query(_Call("cbA", 20002))  # même id rejoué
check("callback dupliqué (même call.id) traité une seule fois",
      calls_seen.count("cbA") == 1, calls_seen)
rb.handle_callback_query(_Call("cbB", 20002))
check("nouveau callback (call.id différent) bien traité",
      calls_seen.count("cbB") == 1, calls_seen)
catalogue.handle_callback = _orig_handle_callback

# 3. Règle « ponctuation seule » — 4 langues
PUNCT_CASES = [
    ("[[", "fr", "boss"),
    ("...", "fr", "boss"),
    ("//", "en", "boss"),
    ("' '", "es", "jefe"),
    ("؟", "ar", "رئيس"),
    (".", "fr", "boss"),
]
for txt, lg, needle in PUNCT_CASES:
    SENT.clear()
    rb._process_text(30000, txt, lg)
    ans = SENT[-1][1] if SENT else ""
    check(f"ponctuation seule [{lg}] «{txt}»", needle in ans, ans[:70])

# 4. Pas de faux positif : lettres/chiffres/emoji ne déclenchent JAMAIS
# la règle "ponctuation seule"
for txt in ("1", "salut", "😊", "bonjour!", "42"):
    check(f"pas de faux positif sur «{txt}»", rb._is_punctuation_only(txt) is False)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
