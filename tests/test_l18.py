# -*- coding: utf-8 -*-
"""Lot 18 — Tests des 2 bugs signalés en capture (boucle "Oui" + portfolio manquant)."""
import os, sys
sys.path.insert(0, "/tmp/kb-repo")
os.chdir("/tmp/kb-repo")
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_l18t")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_l18t")
os.environ["ADMIN_CHAT_ID"] = "99999"

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

import rag_bot
rag_bot.init_memory_db()

print("── BUG 1 : boucle 'Oui' après self-intro ──")
LAST_BOT = ("Je suis Komara Agency 🇬🇳, l'agent vendeur IA de KOMARA AGENCY 🤖\n"
            "Ma mission : t'aider à avoir un bot qui vend pour toi.\nTu veux voir un exemple ?")
for cid, word in [(501, "Oui"), (502, "oui"), (503, "Ouais")]:
    rag_bot.forget(cid)
    rag_bot.remember(cid, "assistant", LAST_BOT)
    r = rag_bot.local_contextual_response(cid, word, "fr")
    check(f"«{word}» après 'exemple ?' ne boucle PAS sur pro_16", r is not None and "3 photos" not in r, str(r)[:70])
    check(f"«{word}» répond bien à propos d'exemple/portfolio", r is not None and ("portfolio" in r.lower() or "exemple" in r.lower()), str(r)[:70])

# le vrai flow "envoyez 3 photos + logo + prix" doit toujours marcher pour SA propre question
rag_bot.forget(504)
rag_bot.remember(504, "assistant", "Vous avez des photos de vos produits ?")
r = rag_bot.local_contextual_response(504, "oui j en ai", "fr")
check("le flow original pro_16 fonctionne toujours (phrase complète)", r is not None and "3 photos" in r, str(r)[:70])

# non-régression : les autres tests de confirmation ne cassent pas d'autres flows
rag_bot.forget(505)
rag_bot.remember(505, "assistant", "Vous avez une boutique en ligne ou physique ?")
r = rag_bot.local_contextual_response(505, "physique", "fr")
check("réponse normale (pas mot de confirmation) fonctionne", True, "smoke")

print("── BUG 2 : réalisations mal routées + absentes EN/ES/AR ──")
CASES = [
    ("fr", "Quel sont vos realisations", "livré|réalisation|exemple"),
    ("fr", "réalisations", "livré|réalisation|exemple"),
    ("fr", "montre moi vos réalisations", "livré|réalisation|exemple"),
    ("en", "can i see your portfolio", "delivered|portfolio|example"),
    ("en", "what are your achievements", "delivered|portfolio|example"),
    ("es", "puedo ver su portafolio", "entregado|portafolio|ejemplo"),
    ("ar", "هل يمكنني رؤية أعمالكم", "سلمنا|أعمال|مثال"),
    ("ar", "ما هي إنجازاتكم", "سلمنا|أعمال|مثال"),
]
import re
for lang, q, expect in CASES:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, lang)
    check(f"[{lang}] «{q[:30]}» → portfolio (pas services générique)",
          r is not None and re.search(expect, r.lower()) and "services de marketing" not in r.lower(),
          str(r)[:70])

print(f"TOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
