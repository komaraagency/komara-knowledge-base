"""Lot 39 (Boss 14/10) : identité agence/fondateur distinctes, fiches
affiche/automatisation (signalées par /evaluation), prix TOUJOURS en euros."""
import os, sys
from pathlib import Path
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ADMIN_CHAT_ID"] = "99999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print("OK ", name)
    else: KO += 1; print("KO ", name, "->", extra)

import rag_bot, aya_seed, knowledge_store, commercial_pack, osm_maps
knowledge_store._CUSTOM_ROWS = []
aya_seed.ensure_seed("fr")

print("── 1. Identité : agence ≠ fondateur ≠ services ──")
CASES = [
    # (message client, doit contenir, ne doit PAS contenir)
    ("Qui est komara Agency 🇬🇳",        "agence digitale guinéenne", "Je vends pour toi"),
    ("C'est quoi komara Agency 🇬🇳",     "agence digitale guinéenne", "Je vends pour toi"),
    ("Qui est Ndine komara",              "fondateur",                 "Je vends pour toi"),
    ("qui est le fondateur",              "fondateur",                 "Je vends pour toi"),
    ("tu fais quoi",                      "Bots WhatsApp",             "fondateur"),
    ("que fait Komara Agency",            "Bots WhatsApp",             "fondateur"),
]
for msg, must, never in CASES:
    r = rag_bot.trouver_meilleure_reponse_multilingue(msg, "fr") or ""
    check(f"« {msg} »", must in r and never not in r, r[:90])

print("── 2. Fiches signalées par /evaluation ──")
for msg, must in [("quel est le prix d'une affiche", "30 €"),
                  ("c'est quoi une automatisation", "se fait toute seule")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(msg, "fr") or ""
    check(f"« {msg} »", must in r, r[:90])
    check(f"  finit par (Oui / Non) -> logique RDV", "(Oui / Non)" in r, r[-40:])

print("── 3. JAMAIS de monnaie locale dans les réponses ──")
import re
bad = re.compile(r"\bGNF\b|\bFG\b|\bXOF\b|\bFCFA\b")
leaks = [(q[0], a[:50]) for q, a in aya_seed.SEED_QR if bad.search(a)]
check("aucune fiche seed en GNF/FG/XOF", not leaks, leaks)
for msg in ("c'est combien le logo", "prix", "combien ça coûte"):
    r = rag_bot.trouver_meilleure_reponse_multilingue(msg, "fr") or ""
    check(f"« {msg} » -> en €, sans GNF", "€" in r and not bad.search(r), r[:80])
for z in (3, 30, 200):
    zone = osm_maps._zone(z)
    check(f"livraison {z} km en €", "GNF" not in zone[1] and ("€" in zone[1] or "devis" in zone[1]), zone)

print("── 4. Qualification budget : seuils en euros ──")
check("seuils EUR définis", commercial_pack.TIER_LOW_EUR < commercial_pack.TIER_HIGH_EUR)
check("affichage = euros", commercial_pack._fmt_gnf_in(5.0, "GN") == "5 €",
      commercial_pack._fmt_gnf_in(5.0, "GN"))
check("conversion € -> €", abs(commercial_pack._to_eur(20.0, "FR") - 20.0) < 0.5,
      commercial_pack._to_eur(20.0, "FR"))

print(f"\n=== lot39 : {OK} OK / {KO} KO ===")
sys.exit(1 if KO else 0)
