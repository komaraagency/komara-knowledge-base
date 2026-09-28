import sys, types, re
sys.path.insert(0, '.')
import rag_bot, actions, img_gen
rag_bot.init_memory_db(); actions.init_db()
ADMIN, CLIENT = 99999, 22222
passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"OK  {name}")
    else: failed += 1; print(f"KO  {name} -> {extra[:120]}")

sent = []
class FakeBot:
    def send_chat_action(self, *a, **k): return True
    def send_message(self, cid, text, **k):
        sent.append((cid, text)); return True
    def send_photo(self, *a, **k): return True
    def send_voice(self, *a, **k): return True
rag_bot.bot = FakeBot()
def last(cid): return [t for c, t in sent if c == cid][-1] if any(c == cid for c, t in sent) else ""

print("── knowledge.txt LU au démarrage ──")
kt = rag_bot.load_knowledge_txt()
check("knowledge.txt chargé", len(kt) > 500, len(kt))
check("contient le persona KOMARA IA", "KOMARA IA" in kt and "Luxury" in kt)
check("protocole 8K présent", "Sony A7R V" in kt)

print("── PROTOCOLE 8K dans le générateur d'images ──")
pr = img_gen._with_8k_protocol("portrait d'une femme africaine élégante")
check("prompt enrichi Sony A7R V", "Sony A7R V" in pr, pr[:80])
check("Or prestige #D4AF37", "#D4AF37" in pr)
check("9:16 vertical par défaut", "9:16" in pr)
pr_logo = img_gen._with_8k_protocol("logo doré pour ma marque")
check("logo → pas de 9:16 forcé", "9:16" not in pr_logo)
check("prompt vide → inchangé", img_gen._with_8k_protocol("") == "")

print("── FICHES PERSONA + DOCS ──")
tests = [
    ("c est quoi ton style", "luxury|or et noir|africain"),
    ("c est quoi le protocole 8k", "sony|85mm|d4af37|pores"),
    ("c est trop cher pourquoi", "excellence|8k|convertit|investissement"),
    ("c est quoi komara ia", "assistant|komara"),
    ("comment se passe une commande", "50%|24h|solde|étapes"),
    ("etes vous disponibles cette semaine", "délai|jours|semaine"),
    ("les erreurs qui tuent une vente", "relance|3h|vocal|prénom"),
    ("je sais pas trop ce que je veux", "vends|orient"),
    ("tu n as pas compris ma question", "reformule|reprends"),
]
for q, expect in tests:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q[:34]}»", r is not None and re.search(expect, r, re.I) is not None, str(r)[:70])

print("── RÉCLAMATION → revue manuelle (règle docs) ──")
actions.handle(rag_bot.bot, ADMIN, "/promo TABASKI20 20", "fr")  # code créé pour ce run
sent.clear()
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "C'est une arnaque, je veux porter plainte", "fr")
notif = [t for c, t in sent if c == ADMIN and "RÉCLAMATION" in t]
check("admin notifié immédiatement", len(notif) >= 1, str(notif))
check("accusé sobre, pas de pitch", "dirigeante" in last(CLIENT) or "transmis" in last(CLIENT).lower(), last(CLIENT)[:70])
check("pas de réponse vente automatique", "devis" not in last(CLIENT).lower() and "catalogue" not in last(CLIENT).lower())
sent.clear()
actions.handle(rag_bot.bot, 33333, "I was scammed, I want to file a complaint", "en")
check("réclamation EN → accusé EN", "leadership" in last(33333), last(33333)[:60])

print("── RÉGRESSION ──")
for q, expect in [("qui es tu", "aya|komara"), ("bsr", "onjour|onsoir|alut|ienvenue"),
                  ("c est combien un site", "devis"),
                  ("c est quoi un business plan", "projet")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q[:26]}» intacte", r is not None and re.search(expect, r, re.I) is not None, str(r)[:60])
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "/promo TABASKI20", "fr")
check("/promo client toujours OK", "VALIDE" in last(CLIENT), last(CLIENT)[:60])
sent.clear()
actions.handle(rag_bot.bot, 44444, "parler à un humain", "fr")
check("humain toujours OK", "5 min" in last(44444) or "WhatsApp" in last(44444))
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "je vends dans une boutique", "fr")
check("boutique → pas de réclamation déclenchée", "RÉCLAMATION" not in last(CLIENT) and not any("RÉCLAMATION" in t for c, t in sent if c == ADMIN), last(CLIENT)[:50])
n = len(rag_bot.trouver_meilleure_reponse_multilingue.__globals__.get("LOCAL_KB", {}).get("fr", [])) if False else True
print(f"TOTAL: {passed} OK / {failed} KO")
sys.exit(1 if failed else 0)
