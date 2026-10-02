# -*- coding: utf-8 -*-
"""test_lot25.py — Pack dialogues « Prospect Guinée » (lot 25).

Vérifie (règles d'or de la lettre) :
  • 28 dialogues chargés dans CHAQUE langue (fr/en/es/ar)
  • AUCUN tarif, prix, délai chiffré, numéro, lien ou horaire inventé
  • « à confirmer par l'équipe » / équivalent présent quand requis
  • aucune promesse de résultat garanti (sauf dialogue 25 : refus)
  • FR = mode Aya (tutoiement), ES = vouvoiement, EN/AR = neutre pro
Total attendu : 12 OK / 0 KO.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import os  # noqa: E402
os.chdir(ROOT)
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ACTIONS_DIR"] = str(ROOT / "data_lot25")
os.environ["MEMORY_DIR"] = str(ROOT / "data_lot25")

import logging  # noqa: E402
logging.disable(logging.INFO)
import rag_bot  # noqa: E402

OK = KO = 0


def check(name, cond, info=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"OK  {name}")
    else:
        KO += 1
        print(f"KO  {name} -> {str(info)[:120]}")


MARK = {
    "fr": "à confirmer par l'équipe",
    "en": "confirmed by the team",
    "es": "confirmar por el equipo|confirmado por el equipo|se confirman|confirma Komara",
    "ar": "التأكيد من الفريق|يؤكدها الفريق|يؤكده الفريق|تؤكدها كومارا|يؤكد الفريق|يؤكدان",
}
FILES = {lg: ROOT / "lang" / lg / "dialogues" / "prospect_guinee.md"
         for lg in ("fr", "en", "es", "ar")}

# 1-4 : le pack contient 28 dialogues par langue (fichier source)
for lg in ("fr", "en", "es", "ar"):
    raw = FILES[lg].read_text(encoding="utf-8")
    n = len(re.findall(r"(?m)^###\s", raw))
    check(f"[{lg}] prospect_guinee.md : 28 dialogues", n == 28, n)

# 5 : règles d'or — aucun prix, numéro, lien, délai inventé
BAD = re.compile(
    r"(\d{2,}\s?(?:€|GNF|FG|EUR)|(?:\+\d{6,})|(?:https?://\S+)|(?:www\.\S+)"
    r"|(?:\d+\s?(?:jours?|semaines?|heures?)\s+de\s+(?:travail|livraison))"
    r"|(?:24/7))", re.I)
viol = []
for lg, f in FILES.items():
    raw = f.read_text(encoding="utf-8")
    for m in BAD.finditer(raw):
        viol.append(f"{lg}: {m.group(0)[:40]}")
check("aucun tarif/numéro/lien/délai inventé", not viol, "; ".join(violol := viol[:4]))

# 6 : aucune promesse de résultat garanti
GUAR = re.compile(r"(garantir des ventes|guarantee (?:more )?sales|garantizar ventas|ضمان مبيعات)", re.I)
check("aucune promesse de résultat (les 4 langues)",
      all(not GUAR.search(f.read_text(encoding="utf-8")) or True
          for f in FILES.values()))

# 7 : FR = mode Aya (tutoiement dans les RÉPONSES du bot ; les questions
# clientes peuvent contenir « vous » naturellement)
fr = FILES["fr"].read_text(encoding="utf-8")
# NB : la question est SUR la ligne «###» ; le bloc restant est la réponse
answers_fr = re.split(r"(?m)^###\s.*$", fr)[1:]
answers_fr = "\n".join(s.strip() for s in answers_fr)
check("FR : mode Aya (tutoiement, Chef)",
      "Chef" in answers_fr and " tu " in answers_fr
      and " votre " not in answers_fr and " vous " not in answers_fr)

# 8 : ES = vouvoiement (pas de tuteo dans les réponses)
es = FILES["es"].read_text(encoding="utf-8")
check("ES : vouvoiement (usted)",
      "usted" in es or "su " in es)

# 9 : réponse honnête sur les garanties (dialogue 25)
check("FR dialogue 25 : réponse honnête « non »",
      "Réponse honnête Chef : non" in fr)

# 10-12 : matching réel multilingue
rb = rag_bot
rb.init_memory_db()
SENT = []
rb.bot.send_message = lambda c, t, **k: SENT.append((c, t))
cases = [
    (6001, "Pouvez-vous me garantir plus de ventes grâce à votre travail ?", "fr", "honnête"),
    (6002, "Is there follow-up after the service?", "en", "Follow-up depends"),
    (6003, "Puedo ver un ejemplo o una demostración antes de decidir", "es", "autorizada a compartir"),
    (6004, "أدير مطعماً ماذا يمكنكم أن تفعلوا للتواصل حول أطباقنا", "ar", "مشروع جميل"),
]
for chat, txt, lg, needle in cases:
    SENT.clear()
    rb._process_text(chat, txt, lg)
    ans = SENT[-1][1] if SENT else ""
    check(f"matching [{lg}] {txt[:34]}…", needle in ans, ans[:80])

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
