#!/usr/bin/env python3
"""Lot 28 — /apprends mode FAQ (alias), anti-doublon, anti-crash.
Le boss apprend au bot en chat admin : /apprends <question> || <réponse>.
- Doublon (même sujet déjà connu) -> « désolé j'ai déjà une réponse
  similaire !! », RIEN n'est ajouté.
- Aucune entrée, même tordue, ne doit faire planter le bot.
"""
import json
import logging
import os
import sys
import types
from pathlib import Path

logging.disable(logging.INFO)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TELEGRAM_TOKEN"] = "123:TEST"
os.environ["ADMIN_CHAT_ID"] = "99999"
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_lot28")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_lot28")
import shutil as _sh
_sh.rmtree("/tmp/kb-repo/data_lot28", ignore_errors=True)

import rag_bot as rb
import actions

results = []
SENT = []
ADMIN = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("✅" if ok else "❌"), name, ("— " + str(detail)[:90] if detail and not ok else ""))


rb.init_memory_db()
rb.bot.send_message = lambda c, t, **k: SENT.append(t)
rb.bot.send_photo = lambda *a, **k: None
actions.notify_admin = lambda bot, m: ADMIN.append(m)
rb.notify_admin = lambda bot, m: ADMIN.append(m)

_mid = [0]


def say(chat, text, name="Boss"):
    _mid[0] += 1
    SENT.clear()
    m = types.SimpleNamespace(
        chat=types.SimpleNamespace(id=chat), message_id=_mid[0], text=text,
        voice=None, audio=None, document=None, location=None, photo=None,
        from_user=types.SimpleNamespace(first_name=name))
    rb.handle_message(m)
    return list(SENT)


ADM = actions.ADMIN_CHAT_ID

# ── 1. Commande : mode FAQ via chat admin (pas de CSV) ──────────────────
r = say(ADM, "/apprends test incomplet")
check("1a. usage expliqué si pas de ||", r and "Usage : /apprends <question> || <réponse>" in r[0])

r = say(ADM, "/apprends vous livrez à Kindia || Oui, partout en Guinée 🇬🇳 livraison offerte !")
check("1b. ajout FAQ accepté", r and "✅ Connaissance ajoutée" in r[0])

# ── 2. Le client reçoit la connaissance apprise immédiatement ────────────
r = say(77881, "vous livrez à Kindia ?")
check("2a. le client obtient la réponse apprise",
      r and "Guinée" in r[0], r[0][:60] if r else "rien")

# ── 3. RÈGLE BOSS : doublon -> « déjà une réponse similaire » ────────────
r = say(ADM, "/apprends vous livrez à Kindia || autre réponse")
check("3a. doublon EXACT refusé",
      r and "j'ai déjà une réponse similaire" in r[0])
r = say(ADM, "/apprends livrez vous a kindia || encore autre")
check("3b. PARAPHRASE refusée (mots porteurs identiques)",
      r and "j'ai déjà une réponse similaire" in r[0])
r = say(ADM, "/apprends c est quoi la 5g || reseau mobile")
check("3c. question DÉJÀ dans la KB refusée",
      r and "j'ai déjà une réponse similaire" in r[0])
# pas de faux doublon : sujet différent même si mots-outils communs
r = say(ADM, "/apprends vous faites des tshirts personnalisés || Oui, designs uniques")
check("3d. PAS de faux doublon (sujet nouveau accepté)",
      r and "✅ Connaissance ajoutée" in r[0], r[0][:80] if r else "rien")
r = say(ADM, "/apprends vous livrez à Kankan || Oui aussi")
check("3e. ville DIFFÉRENTE acceptée (kindia ≠ kankan)",
      r and "✅ Connaissance ajoutée" in r[0], r[0][:80] if r else "rien")

# ── 4. Alias de la commande (le boss tape /apprendre ou /apprendres) ────
r = say(ADM, "/apprendres vous réparez les ordinateurs || Non, pas pour le moment")
check("4a. alias /apprendres", r and "✅ Connaissance ajoutée" in r[0], r[0][:80] if r else "rien")
r = say(ADM, "/apprendre vous faites des cartes de visite || Oui, cartes de visite premium")
check("4b. alias /apprendre", r and "✅ Connaissance ajoutée" in r[0], r[0][:80] if r else "rien")

# ── 5. STABILITÉ : rien ne fait planter le bot ───────────────────────────
weird = [
    "/apprends ||",
    "/apprends   ||   ",
    "/apprends ?||",
    "/apprends a||b",
    "/apprends " + "x" * 900 + " || réponse",
    "/apprends مرحبا بالعربية || جواب عربي",
    "/apprends émojis 🎉🤖 🚀 || réponse 🚀",
    "/apprends",
    "/apprends || || ||",
]
crashes = 0
for w in weird:
    try:
        say(ADM, w)
    except Exception:
        crashes += 1
check("5a. 9 entrées tordues : 0 crash", crashes == 0, f"{crashes} crash(s)")
# le bot répond toujours après les entrées tordues
r = say(77882, "bonjour")
check("5b. le bot fonctionne toujours après les cas tordus", bool(r))

# ── 6. Persistance : kb_custom.json réchargé au démarrage ───────────────
path = Path(os.environ["ACTIONS_DIR"]) / "kb_custom.json"
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
check("6a. kb_custom.json persisté", len(data) >= 4, f"{len(data)} fiche(s)")
# doublon refusé => pas écrit dans le fichier
dups = [e for e in data if "kindia" in e["question"].lower() and "vous livrez à kindia" == e["question"].lower()]
check("6b. le doublon n'est PAS persisté", len(dups) <= 1)

# ── 7. Le mode CSV (/kb_import) n'est plus le chemin indiqué ─────────────
r = say(ADM, "/apprends")
check("7a. la commande seule affiche le mode FAQ", r and "||" in r[0])

ok = sum(1 for _, o, _ in results if o)
ko = len(results) - ok
print(f"\nTOTAL: {ok} OK / {ko} KO")
sys.exit(0 if ko == 0 else 1)
