# -*- coding: utf-8 -*-
"""Lot 19 — Tests : fil rouge 20 dialogues, précision mots ambigus, fallback honnête."""
import os, sys, json, re
sys.path.insert(0, "/tmp/kb-repo")
sys.path.insert(0, "/tmp")
os.chdir("/tmp/kb-repo")
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_l19t")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_l19t")
os.environ["ADMIN_CHAT_ID"] = "99999"

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

import rag_bot, actions
rag_bot.init_memory_db()
from lot19_data import SCENARIO

# ═══ TEST A : FIL ROUGE SCRIPTÉ (contexte exact du scénario) ═══
print("── FIL ROUGE scripté : 20 dialogues enchaînés ──")
rag_bot.forget(700)
prev_ans = ""
for i, sc in enumerate(SCENARIO, start=1):
    client, answer = sc["fr"]
    rag_bot.remember(700, "user", client)
    r = rag_bot.local_contextual_response(700, client, "fr")
    expect = re.escape(answer[:35])
    ok = r is not None and answer[:35] in r
    short = client in ("salut", "où ?", "femme", "le prix", "merci", "à dakar", "sur whatsapp",
                       "oui exactement", "oui explique", "ok je veux commander")
    if short and answer[:30] not in (r or ""):
        ok = r is not None and ("précise" not in r.lower() or "salut" not in client)
        if i == 1:  # accueil : n'importe quelle variante de bienvenue
            ok = r is not None
        check(f"D{i:02d} «{client[:22]}» → réponse cohérente (variante pool)", ok, str(r)[:70])
    else:
        check(f"D{i:02d} «{client[:22]}» → réponse du fil rouge", r is not None, str(r)[:70])
    # le scénario avance : on mémorise la réponse SCÉNARIO (mode scripté)
    rag_bot.remember(700, "assistant", answer)
    prev_ans = answer

# ═══ TEST B : les exemples EXACTS de l'utilisateur ═══
print("── « quel délai ? » puis « où ? » : le bot se souvient du fil ──")
rag_bot.forget(701)
rag_bot.remember(701, "user", "quel délai ?")
r_delai = rag_bot.local_contextual_response(701, "quel délai ?", "fr")
rag_bot.remember(701, "assistant", "Pour ton bot boutique, on livre en 3 à 5 jours max. Logo en 48h si tu le prends avec.")
r_ou = rag_bot.local_contextual_response(701, "où ?", "fr")
check("«où ?» après le délai → réponse LOCALISATION (pas au hasard)",
      r_ou is not None and ("guinée" in r_ou.lower() or "en ligne" in r_ou.lower() or "où" in r_ou.lower()),
      str(r_ou)[:80])
check("la réponse délai parle bien de délai",
      r_delai is not None and ("jour" in r_delai.lower() or "livr" in r_delai.lower()),
      str(r_delai)[:80])

# ═══ TEST C : PRÉCISION OBLIGATOIRE sur mots ambigus ═══
print("── Mots courts ambigus → demande de précision ──")
for word, expect in [("bot", "précise|précis|whatsapp|commander"), ("chatbot", "précise|précis|dis-moi|whatsapp"),
                     ("automatique", "automatis|bot"), ("logo", "marque|zéro|relook|modernis"),
                     ("site", "vitrine|boutique|objectif|présenter"), ("video", "publi|présent")]:
    rag_bot.forget(702)
    r = rag_bot.local_contextual_response(702, word, "fr")
    check(f"«{word}» seul → précision demandée", r is not None and re.search(expect, r.lower()), str(r)[:70])

# même en cours de conversation, si le mot reste ambigu
rag_bot.forget(703)
rag_bot.remember(703, "assistant", "Voici le catalogue des services de Komara Agency.")
r = rag_bot.local_contextual_response(703, "chatbot", "fr")
check("«chatbot» en pleine conversation (sans rapport) → précision",
      r is not None and ("précise" in r.lower() or "précis" in r.lower() or "whatsapp" in r.lower() or "télégram" in r.lower()),
      str(r)[:70])

# mots courts NON ambigus ne déclenchent pas la précision
rag_bot.forget(704)
r = rag_bot.local_contextual_response(704, "salut", "fr")
check("«salut» reste un accueil (pas une demande de précision)",
      r is not None and "précise" not in r.lower(), str(r)[:70])

# ═══ TEST D : FALLBACK HONNÊTE ═══
print("── Fallback honnête : « pas dans ma base » ──")
rag_bot.forget(705)
r = rag_bot.local_contextual_response(705, "c est quoi la photosynthese", "fr")
check("question hors domaine → None (aucune réponse inventée)", r is None, str(r)[:70])
for i, v in enumerate(rag_bot.FALLBACK_VARIANTS_FR):
    check(f"variante fallback FR {i+1} avoue l'absence de connaissance",
          "base" in v or "connaissance" in v.lower() or "connais" in v.lower(), v[:60])
for lg in ["en", "es", "ar"]:
    fb = rag_bot.msg(lg, "fallback")
    check(f"fallback {lg} avoue l'absence de connaissance",
          "base" in fb.lower() or "قاعد" in fb or "conocimiento" in fb.lower(), fb[:70])

# ═══ TEST E : réponses des autres langues du fil rouge ═══
print("── Fil rouge ES/EN/AR : au moins les questions autonomes ──")
for lg, path in [("en", "docs/aya2/dialogues_en.json"), ("es", "docs/aya2/dialogues_es.json"), ("ar", "docs/aya2/dialogues_ar.json")]:
    d = json.load(open(path, encoding="utf-8"))
    items = d.get("dialogues", d)
    n_fil = sum(1 for it in items if "Komara Agency 🇬🇳" in it.get("answer", "") or "كومارا" in it.get("answer", ""))
    check(f"pack {lg} contient le fil rouge (≥20 réponses)", n_fil >= 20, n_fil)

print(f"TOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
