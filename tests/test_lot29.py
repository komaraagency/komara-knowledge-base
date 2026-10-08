# -*- coding: utf-8 -*-
"""Lot 29 — RÈGLE BOSS (02/10) : bot AUTO-APPRENANT, ZÉRO donnée locale.

Nouveau régime :
1. Le repo ne porte PLUS aucun corpus de dialogues (kb.json, dialogues/,
   faq, aya2 supprimés). Le bot démarre avec une base VIDE.
2. Le boss enseigne TOUT via /apprends (ou /kb_import) : persistance
   APPEND-ONLY dans le Google Sheet dédié « Komara Bot - Mémoire ».
3. RIEN ne fuit sur GitHub ni sur le disque Railway : conversations,
   questions sans réponse et dialogues appris vivent dans Google Sheets ;
   la mémoire SQLite n'est qu'un cache éphémère.

Ce lot vérifie le régime complet avec un FAUX Google Sheet en RAM.
"""
import os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TELEGRAM_TOKEN"] = "123:TEST"
os.environ["ADMIN_CHAT_ID"] = "99999"
TESTDIR = Path(tempfile.mkdtemp(prefix="lot29_"))
os.environ["ACTIONS_DIR"] = str(TESTDIR)
os.environ["MEMORY_DIR"] = str(TESTDIR)

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

# ── Faux Google Sheet en RAM ──────────────────────────────────────────────
import memory_sheets

class FakeSheet:
    def __init__(self):
        self.dialogues = []      # lignes [date, lang, question, réponse]
        self.conversations = []  # lignes [date, chat, nom, rôle, contenu, langue]
        self.unanswered = []     # lignes [date, question, langue, chat, occurrences]
        self.linked = True

    def save_learned(self, question, answer, lang):
        if not self.linked:
            raise RuntimeError("Google non lié (test)")
        self.dialogues.append(["t", lang, question, answer])

    def load_learned(self):
        learned = {}
        for row in self.dialogues:
            learned[row[2].strip().casefold()] = {"question": row[2], "answer": row[3],
                                                  "lang": row[1], "date": row[0]}
        return list(learned.values())

    def log_conversation(self, chat_id, name, role, content, lang=""):
        self.conversations.append(["t", str(chat_id), name, role, content, lang])

    def log_unanswered(self, question, lang, chat_id, count):
        self.unanswered.append(["t", question, lang, str(chat_id), str(count)])


sheet = FakeSheet()
import rag_bot, actions, knowledge_store

# ── 1. Démarrage : base VIDE, aucun corpus requis ─────────────────────────
print("── BASE VIDE AU DÉMARRAGE ──")
with patch.object(memory_sheets, "load_learned", sheet.load_learned):
    rag_bot.init_memory_db()
fr = rag_bot.LANG_RESOURCES["fr"]
check("corpus retiré (kb.json absent), seed Aya chargée",
      not (rag_bot.BASE_DIR / "kb.json").exists() and len(fr["kb"]) >= 20,
      len(fr["kb"]))
check("faq vide", fr["faq"] == [], len(fr["faq"]))
check("dialogues vides", fr["dialogues"] == [], len(fr["dialogues"]))
check("kb.json absent du repo", not (rag_bot.BASE_DIR / "kb.json").exists())
check("dossier dialogues/ absent", not (rag_bot.BASE_DIR / "dialogues").exists())
check("mémoire éphémère (pas de volume /data)",
      str(rag_bot.MEMORY_DIR).startswith(tempfile.gettempdir()) or str(TESTDIR) in str(rag_bot.MEMORY_DIR),
      rag_bot.MEMORY_DIR)

# ── 2. Question inconnue → fallback + onglet Questions sans réponse ────────
print("── QUESTIONS SANS RÉPONSE ──")
sent = []
class FakeBot:
    def send_message(self, cid, text=None, *args, **k):
        sent.append((cid, text)); return True
    def send_chat_action(self, *a, **k): return True

actions.ADMIN_CHAT_ID = 99999
with patch.object(memory_sheets, "log_unanswered", sheet.log_unanswered):
    actions.notify_unanswered(FakeBot(), 12345, "vous livrez a kindia ?", "fr")
check("question sans réponse → Google Sheet (pas de fichier)",
      len(sheet.unanswered) == 1 and "kindia" in sheet.unanswered[0][1], sheet.unanswered)
check("admin notifié Telegram (une fois)",
      any("QUESTION NON RÉPONDUE" in t for c, t in sent if c == 99999), sent[:2])
check("AUCUN fichier unanswered_questions.json",
      not (TESTDIR / "unanswered_questions.json").exists())

# ── 3. Le boss enseigne : /apprends → Sheets + réponse servie ─────────────
print("── /apprends (le bot apprend depuis l'admin) ──")
with patch.object(memory_sheets, "save_learned", sheet.save_learned):
    actions._admin_apprends(FakeBot(), 99999,
                            "vous livrez a kindia || Oui partout en Guinée 🇬🇳 !", "fr")
check("dialogue appendé dans Google Sheet", len(sheet.dialogues) == 1, sheet.dialogues)
r = rag_bot.trouver_meilleure_reponse_multilingue("vous livrez a kindia ?", "fr")
check("réponse apprise servie au client",
      r is not None and "Guinée" in r, str(r)[:80])

with patch.object(memory_sheets, "save_learned", sheet.save_learned):
    actions._admin_apprends(FakeBot(), 99999,
                            "vous livrez a kindia || Non, seulement Conakry.", "fr")
# la question enseignée est SIMILAIRE au seed 'livrez vous à kindia'
# → elle le REMPLACE (runtime) : on garde 20 dialogues actifs, pas 21.
check("réapprendre similaire remplace le seed (40 actifs, 25 + 11 conv. Boss 04/10 + 4 définitions Boss 08/10)",
      len(knowledge_store._CUSTOM_ROWS) == 40, len(knowledge_store._CUSTOM_ROWS))
r2 = rag_bot.trouver_meilleure_reponse_multilingue("vous livrez a kindia ?", "fr")
check("la NOUVELLE réponse est servie", r2 is not None and "Conakry" in r2, str(r2)[:80])

# ── 4. Google HS → aucun faux succès, rien publié ─────────────────────────
print("── ÉCHEC GOOGLE : AUCUN FAUX SUCCÈS ──")
sent.clear()
sheet.linked = False
with patch.object(memory_sheets, "save_learned", sheet.save_learned):
    actions._admin_apprends(FakeBot(), 99999, "zzz wxyz inconnu || autre réponse", "fr")
check("pas de ✅ annoncé quand Google est HS",
      not any("✅" in t for c, t in sent), sent)
check("rien publié en runtime",
      rag_bot.trouver_meilleure_reponse_multilingue("zzz wxyz inconnu", "fr") is None)
sheet.linked = True

# ── 5. Miroir des réponses (wrap send_message installé par run()) ───────
print("── MIROIR CONVERSATIONS ──")
sheet.conversations.clear()
rag_bot.bot = FakeBot()          # bot de test, comme les anciens lots
rag_bot._install_reply_mirroring()
check("send_message wrappé par le miroir",
      getattr(rag_bot.bot.send_message, "_komara_mirror", False) is True)
with patch.object(memory_sheets, "log_conversation", sheet.log_conversation):
    rag_bot.bot.send_message(777, "Bonjour 👋 je peux t'aider ?", "fr")
    rag_bot.bot.send_message(777, "Voici le menu 👇", "fr")
import time as _t; _t.sleep(0.3)
check("chaque réponse du bot est miroitée vers Google Sheet",
      len(sheet.conversations) == 2 and all(row[3] == "bot" for row in sheet.conversations),
      sheet.conversations)
check("le message original part quand même (le client voit sa réponse)",
      any("menu" in t for c, t in sent if c == 777), sent[-2:])

# ── 6. Aucune donnée locale persistée ─────────────────────────────────────
print("── ZÉRO DONNÉE LOCALE ──")
leftovers = sorted(p.name for p in TESTDIR.iterdir()
                   if p.name not in ("memory.db",))
check("aucun kb_custom.json / kb_purged.json / unanswered.json",
      not any(n in leftovers for n in ("kb_custom.json", "kb_purged.json", "unanswered_questions.json")),
      leftovers)
check("memory.db = cache mémoire uniquement", (TESTDIR / "memory.db").exists())

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
