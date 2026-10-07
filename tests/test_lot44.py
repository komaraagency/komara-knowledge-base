# -*- coding: utf-8 -*-
"""Lot 44 — Boss 07/10 03h49 : "il saute de coq à l'âne, je ne comprends
rien avec ce bot" (screenshots : évaluation 85%, 3 formulations ratées,
« Qui est tu » non répondu malgré 172 entrées enseignées).

Deux bugs réels trouvés et corrigés :
1. CONJUGAISONS DE « ÊTRE » (même famille de bug que lot43 sur « faire ») :
   « qui es tu » (seed) ne matchait pas « Qui est tu » (faute très
   courante client) ni « qui êtes-vous » (vouvoiement) — es/est/êtes/
   suis/sommes/sont étaient des tokens différents, trop courts pour le
   suffixe générique de _stem.
2. TRONCATURE DE RELECTURE INCOHÉRENTE AVEC L'ÉCRITURE (régression
   lot42) : lot42 avait élargi la limite d'ÉCRITURE dans le Sheet à
   500/5000 caractères, mais la RELECTURE au démarrage (initialize_
   resources, refresh_resources, add_custom_kb_entry) était restée à
   200/1500 — une réponse enseignée en entier revenait COUPÉE en plein
   mot à chaque redémarrage du bot : exactement le symptôme « coq à
   l'âne » décrit par le Boss sur une base de 172 fiches.

Note transparence (pas un bug de code) : les screenshots du Google
Sheet montrent aussi des lignes qui sont des FRAGMENTS DE CONVERSATION
collés tels quels (« bien de mon côté » → réponse qui n'a aucun rapport,
« Ici boss je propose... ») plutôt que de vraies paires Q/R. Aucun
algorithme de scoring ne peut deviner une bonne réponse à partir d'une
donnée incohérente à la source — ça doit être nettoyé côté Sheet, pas
dans le code. Voir message à l'utilisateur.
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

print("── 1. Conjugaisons de ÊTRE convergent (comme FAIRE au lot43) ──")
forms = ["es", "est", "etes", "suis", "sommes", "sont"]
stems = {f: _stem(f) for f in forms}
check("es/est/êtes(sans accent)/suis/sommes/sont partagent le même stem",
      len(set(stems.values())) == 1, stems)
check("le stem de ÊTRE est bien distinct de celui de FAIRE (pas de collision)",
      _stem("es") != _stem("fais"), (_stem("es"), _stem("fais")))

print("── 2. Les phrases exactes du screenshot (07/10 03h49) ──")
import rag_bot, knowledge_store, aya_seed
knowledge_store.initialize_resources(
    rag_bot.LANG_RESOURCES, lambda lang: {"kb": [], "faq": [], "dialogues": []})
aya_seed.ensure_seed("fr")

for question in ["Qui est tu", "qui es tu", "qui êtes vous", "tu es qui"]:
    ans = rag_bot.trouver_meilleure_reponse_multilingue(question, "fr")
    check(f"« {question} » trouve la fiche identité",
          ans is not None and ans.startswith("Je suis Aya"), (ans or "FALLBACK")[:60])

print("── 3. Les 3 formulations flaggées par /evaluation répondent juste (seed) ──")
import aya_pipeline
flagged = [
    ("c'est combien le logo", "300"),
    ("c'est quoi un chatbot", "logiciel"),
    ("c'est quoi le délai pour un logo", "2-3 jours"),
]
for probe, expected in flagged:
    ans = rag_bot.trouver_meilleure_reponse_multilingue(probe, "fr") or ""
    check(f"« {probe} » contient « {expected} »",
          expected.lower() in ans.lower(), ans[:70] or "(aucune réponse)")

print("── 4. FIX TRONCATURE : relecture Sheet n'écrase plus une réponse longue ──")
import knowledge_store as ks
long_answer = "Réponse détaillée du Boss. " * 150  # ~4100 caractères, > ancienne limite 1500
assert 1500 < len(long_answer) <= 5000, len(long_answer)
# On exerce le MÊME chemin que le redémarrage (refresh_resources), sans
# toucher au singleton _INITIALIZED pour ne pas perturber les tests
# suivants de ce fichier (qui dépendent du seed déjà chargé).
ks._CUSTOM_ROWS = [{"question": "fiche longue test lot44", "answer": long_answer, "lang": "fr"}]
ks.refresh_resources("fr")
loaded = next((f for f in ks.LANG_RESOURCES["fr"]["kb"]
               if "fiche longue test lot44" in f.get("questions", [])), None)
check("la réponse apprise (4100+ car.) est intacte après rechargement, pas coupée à 1500",
      loaded is not None and loaded.get("answer") == long_answer,
      len(loaded.get("answer", "")) if loaded else "fiche absente")
ks._CUSTOM_ROWS = []
ks.refresh_resources("fr")  # on remet le seed propre pour la suite du fichier
aya_seed.ensure_seed("fr")

# add_custom_kb_entry (helper compat) aligné lui aussi sur 500/5000
long_answer2 = "B" * 2000  # > ancienne limite 1500, <= nouvelle 5000
ks.add_custom_kb_entry("fiche compat test lot44", long_answer2, "fr")
loaded2 = next((f for f in ks.LANG_RESOURCES["fr"]["kb"]
                if "fiche compat test lot44" in f.get("questions", [])), None)
check("add_custom_kb_entry : réponse 2000 car. non tronquée (ancienne limite 1500)",
      loaded2 is not None and len(loaded2.get("answer", "")) == 2000,
      len(loaded2.get("answer", "")) if loaded2 else "fiche absente")

print("── 5. Non-régression lot43 (faire) + seed d'origine ──")
regress = {
    "Que fait Aya": "Je vends pour toi",
    "Je souhaite créer un site web": "Simple",
    "tu fais quoi": "Je vends pour toi",
    "services": "Nos services",
    "merci": "Avec plaisir",
}
for q, expected_start in regress.items():
    ans = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"non-régression « {q} »",
          ans is not None and ans.startswith(expected_start), (ans or "FALLBACK")[:60])

print(f"\nTOTAL: {OK} OK / {KO} KO")
