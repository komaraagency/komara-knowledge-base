# -*- coding: utf-8 -*-
"""Lot 43 — Boss 06/10 13h47 : "le bot calcule les mauvais tokens".
Diagnostic confirmé en lisant local_search._stem : chaque conjugaison du
verbe FAIRE était un token DIFFÉRENT (fais→fai, fait→fait, faites→faite,
faisons→faison...) — zéro overlap entre « tu fais quoi » (seed) et
« que fait Aya » / « que faites-vous » / « que fait Komara Agency »
(questions client réelles, screenshots). Résultat : fallback générique
ou pire, une fiche sans rapport (apprise via /apprends) gagnait par
défaut sur le seul token commun restant.

Fix :
1. local_search._stem : table de conjugaisons irrégulières du verbe
   « faire » (incl. le typo courant « faite » sans s) → stem commun.
2. aya_seed.py : variantes explicites ajoutées aux fiches « tu fais
   quoi » (identité/services) et « comment commander » (envie de
   créer un site/bot) — ceinture ET bretelles : le stemmer généralise,
   les variantes garantissent un score décisif même face à une fiche
   Sheet vague.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ADMIN_CHAT_ID"] = "99999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print("OK ", name)
    else: KO += 1; print("KO ", name, "->", extra)

from local_search import _stem

print("── 1. Stemming : conjugaisons de FAIRE convergent ──")
forms = ["fais", "fait", "faite", "faites", "faisons", "faisez", "font"]
stems = {f: _stem(f) for f in forms}
check("toutes les conjugaisons de « faire » (+ typo « faite ») partagent le même stem",
      len(set(stems.values())) == 1, stems)
check("« faisait »/« ferait » (futur/imparfait) convergent aussi",
      _stem("faisait") == _stem("fais") and _stem("ferait") == _stem("fais"), "")
check("un mot non lié à « faire » n'est pas affecté (« portfolio »)",
      _stem("portfolios") == "portfolio", _stem("portfolios"))

print("── 2. Les 5 phrases exactes du screenshot client (06/10 13h47) ──")
import rag_bot
import knowledge_store
knowledge_store.initialize_resources(
    rag_bot.LANG_RESOURCES, lambda lang: {"kb": [], "faq": [], "dialogues": []})

cases = {
    "Je souhaite créer un site web": "Simple",       # comment commander
    "Je souhaite créer des bot": "Simple",            # comment commander
    "Que faite vous": "Je vends pour toi",            # tu fais quoi
    "Que faite komara Agency": "Je vends pour toi",   # tu fais quoi
    "Que fait Aya": "Je vends pour toi",              # tu fais quoi
}
for question, expected_start in cases.items():
    ans = rag_bot.trouver_meilleure_reponse_multilingue(question, "fr")
    check(f"« {question} » trouve enfin une vraie réponse",
          ans is not None and ans.startswith(expected_start),
          (ans or "FALLBACK")[:60])

print("── 3. Non-régression : fiches seed d'origine toujours servies ──")
regress = {
    "tu fais quoi": "Je vends pour toi",
    "qui es tu": "Je suis Aya",
    "services": "Nos services",
    "comment ça va": "Ça roule",
    "c'est combien le logo": "Tarifs",
    "merci": "Avec plaisir",
    "comment commander": "Simple",
}
for q, expected_start in regress.items():
    ans = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"non-régression « {q} »",
          ans is not None and ans.startswith(expected_start), (ans or "FALLBACK")[:60])

print("── 4. Pas de collision avec la garde « faire » seul (lot41) ──")
check("« faire » (1 mot) déclenche toujours la clarification, pas la fiche identité",
      rag_bot.clarif("faire", "fr").startswith("Faire quoi") or
      "logo" in rag_bot.clarif("faire", "fr"), rag_bot.clarif("faire", "fr")[:50])
# Les phrases à 2+ tokens significatifs ne passent PAS par l'ambigu
from local_search import significant_token_count
check("« que fait Aya » a 2+ tokens significatifs (bypass ambigu, direct match)",
      significant_token_count("Que fait Aya") >= 2, significant_token_count("Que fait Aya"))

print(f"\nTOTAL: {OK} OK / {KO} KO")
