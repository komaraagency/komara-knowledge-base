# -*- coding: utf-8 -*-
"""Lot 38 — Boss 04/10 20h36 : les 4 conversations commerciales Aya
(agents IA, modération FB/IG, retour devis, suivi post-devis) ajoutées
à la connaissance du bot, + support des VARIANTES « q1 | q2 | q3 » dans
le format seed (une entrée, plusieurs formulations clientes).
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

import rag_bot
import aya_seed
import knowledge_store

print("── 1. Les 11 Q/R conversations sont dans le seed ──")
CONV_QR = [
    "je veux en savoir plus sur vos agents ia",
    "comment fonctionne le chatbot pour mon site",
    "quels sont vos tarifs pour un chatbot",
    "je veux en savoir plus sur vos services de modération",
    "j'ai trop de messages et de commentaires à gérer",
    "comment fonctionne la modération des pages",
    "j'ai des questions sur le devis du chatbot",
    "je veux ajouter une fonctionnalité au chatbot",
    "combien de temps pour mettre en place le chatbot",
    "vous offrez un support après la mise en place",
    "je suis prêt à donner mon accord",
]
seed_qs = {q.strip().casefold() for q, a in aya_seed.SEED_QR}
for q in CONV_QR:
    in_seed = any(q in sq for sq in seed_qs)  # variantes « | » incluses
    check(f"seed contient « {q[:45]} »", in_seed, "")
check("36 Q/R au total (25 + 11 conversations)", len(aya_seed.SEED_QR) == 36,
      len(aya_seed.SEED_QR))

print("── 2. Variantes « | » : une entrée, plusieurs questions ──")
entry = knowledge_store._make_entry("q1 | q2 | q3", "réponse")
check("split en 3 questions distinctes",
      entry["questions"] == ["q1", "q2", "q3"], entry["questions"])
entry2 = knowledge_store._make_entry("question simple", "réponse")
check("question sans « | » reste intacte",
      entry2["questions"] == ["question simple"], entry2["questions"])
entry3 = knowledge_store._make_entry("  a |  | b  ", "r")
check("variantes vides ignorées", entry3["questions"] == ["a", "b"], entry3["questions"])

print("── 3. Les messages clients réels trouvent la bonne réponse ──")
knowledge_store._CUSTOM_ROWS = []
aya_seed.ensure_seed("fr")
check("36 entrées servies en runtime", len(knowledge_store._CUSTOM_ROWS) == 36,
      len(knowledge_store._CUSTOM_ROWS))

CASES = [
    # (message client, mot-clé attendu dans la réponse)
    ("Je veux en savoir plus sur vos services d'agents IA", "agents IA personnalisés"),
    ("Comment ça marche un chatbot pour mon site web ?", "spécifications"),
    ("Quels sont vos tarifs pour un chatbot ?", "devis personnalisé"),
    ("Parlez-moi de la modération des réseaux sociaux", "Facebook et Instagram"),
    ("J'ai trop de messages et de commentaires, je n'arrive pas à gérer", "modération prend"),
    ("Comment vous procédez pour la modération ?", "lignes directrices"),
    ("J'ai des questions sur le devis du chatbot", "questions fréquentes"),
    ("Est-ce possible d'ajouter une collecte d'emails au bot ?", "collecte"),
    ("Je suis d'accord pour le devis, on fait quoi maintenant ?", "lien pour finaliser"),
    ("Je valide le devis !", "finaliser"),
    ("Combien de temps pour mettre en place le chatbot ?", "2 à 4 semaines"),
    ("Est-ce que vous offrez un support après la mise en place ?", "support continu"),
]
for msg, kw in CASES:
    r = rag_bot.trouver_meilleure_reponse_multilingue(msg, "fr") or ""
    check(f"« {msg[:48]} » → bonne réponse", kw in r, r[:80])

print("── 4. Idempotence du seed (les variantes ne dupliquent pas) ──")
n = len(knowledge_store._CUSTOM_ROWS)
aya_seed.ensure_seed("fr")
check("re-seed n'ajoute rien", len(knowledge_store._CUSTOM_ROWS) == n,
      len(knowledge_store._CUSTOM_ROWS))

print(f"\nTOTAL: {OK} OK / {KO} KO")
