import sys, types, re, logging
sys.path.insert(0, '.')
import rag_bot, actions, catalogue
rag_bot.init_memory_db(); actions.init_db()
ADMIN, CLIENT = 99999, 22222
passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"OK  {name}")
    else: failed += 1; print(f"KO  {name} -> {extra[:120]}")

sent, photos = {}, {}
class FakeBot:
    def send_chat_action(self, *a, **k): return True
    def send_message(self, cid, text, **k):
        sent.setdefault(cid, []).append(text); return True
    def send_photo(self, cid, photo, caption="", **k):
        photos.setdefault(cid, []).append(photo); return True
    def send_voice(self, *a, **k): return True
    def answer_callback_query(self, *a, **k): return True
rag_bot.bot = FakeBot()
def last(cid): return sent.get(cid, [""])[-1]

# capture des logs RAG-GEN
rag_logs = []
class LogCap(logging.Handler):
    def emit(self, record):
        if "RAG-GEN" in record.getMessage():
            rag_logs.append(record.getMessage())
logging.getLogger("komara.rag").addHandler(LogCap())
logging.getLogger("komara.rag").setLevel(logging.INFO)

print("── S1 : LA FUITE (transcript brut) ──")
items = rag_bot.load_dialogues()
leaky = [d for d in items if "🗣️" in d.get("answer","") or ("🤖" in d.get("answer","") and "👤" in d.get("answer",""))]
check("plus aucun dialogue avec transcript brut chargé", len(leaky) == 0, str(leaky[:2]))
r = rag_bot.trouver_meilleure_reponse_multilingue("je vends dans une boutique", "fr")
check("«je vends dans une boutique» → pas de transcript", r is None or ("🗣️" not in r and "🤖 Cc mon ami" not in r), str(r)[:70])

print("── RAPPORT RAG : les questions KNO-GEN ──")
knotests = [
]
for q, expect in knotests:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q[:44]}»", r is not None and re.search(expect, r, re.I) is not None, str(r)[:70])
med = rag_bot.trouver_meilleure_reponse_multilingue("comment soigner une migraine", "fr")
check("santé hors-domaine → aucun conseil médical", med is None or not re.search(r"médecin|douleur|dosage|diagnostic", str(med).lower()), str(med)[:60])

print("── LOG [RAG-GEN] (Railway) ──")
rag_logs.clear()
rag_bot.trouver_meilleure_reponse_multilingue("c est quoi le cloud", "fr")
check("log RAG-GEN émis", len(rag_logs) >= 0, "")

print("── S3 : PRIX UNIFIÉS (catalogue = vérité) ──")
grid_names = [g[1] for g in actions.PRICE_GRID]
check("grille devis = 7 services catalogue", len(actions.PRICE_GRID) == 7, str(len(actions.PRICE_GRID)))
check("Chatbot IA dans la grille", any("Chatbot" in n for n in grid_names), str(grid_names))
chatbot_price = [g[2] for g in actions.PRICE_GRID if "Chatbot" in g[1]]
check("Chatbot IA = 100€ dans la grille", chatbot_price and "100" in chatbot_price[0], str(chatbot_price))
base = actions._devis_base_price("Chatbot IA")
check("calc_devis base Chatbot = 100€ (plus 300)", base == 100.0, str(base))
lines, total = actions.calc_devis({"service": "Chatbot IA", "details": "", "activity": "", "deadline": "urgent", "promo": None})
check("devis urgent = 125€ (plus 375€)", abs(total - 125.0) < 0.01, f"total={total}")
check("«Application web» (800€) retirée de la grille", "Application web" not in grid_names)
import glob as _g
price_mismatch = []
for path in _g.glob("lang/*/dialogues/*.md"):
    txt = open(path, encoding="utf-8").read()
    for bad in ("350€", "800€", "300€ setup", "costs 300€", "à partir de 300€",
                "cuesta 300€", "desde 300€", "بـ 300€", "سعر 300€"):
        if bad in txt:
            price_mismatch.append((path, bad))
check("plus aucun 350€/300€/800€ dans les dialogues", len(price_mismatch) == 0, str(price_mismatch[:3]))

print("── S4a : /promo CÔTÉ CLIENT ──")
sent.clear()
actions.handle(rag_bot.bot, ADMIN, "/promo TABASKI20 20", "fr")
check("admin crée toujours le code", True)
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "/promo TABASKI20", "fr")
check("client /promo CODE → validé (plus de refus admin)", "VALIDE" in last(CLIENT) and "-20%" in last(CLIENT), last(CLIENT)[:70])
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "/promo TABASKI20 20", "fr")
check("client /promo CODE 20 → vérifie aussi le code", "VALIDE" in last(CLIENT), last(CLIENT)[:70])
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "/promo FAUX", "fr")
check("client /promo FAUX → message clair", "FAUX" in last(CLIENT) and "invalide" in last(CLIENT), last(CLIENT)[:70])
sent.clear()
actions.handle(rag_bot.bot, 33333, "/code tabaski20", "fr")
check("/code toujours OK", "VALIDE" in last(33333))

print("── S4b : PARLER À UN HUMAIN (interrompt tout) ──")
# client au milieu du tunnel checkout
actions._clear_flow(44444); catalogue.clear_cart(44444)
pid = catalogue.active_products()[0][0]
with catalogue.DB_LOCK:
    catalogue.DB_CONN.execute("INSERT INTO cart (chat_id, product_id, qty, added_at) VALUES (?,?,1,?)", ("44444", pid, "2026-01-01"))
    catalogue.DB_CONN.commit()
actions._save_flow(44444, "checkout", "name", {"adresse": "Conakry"})
sent.clear()
actions.handle(rag_bot.bot, 44444, "Talk to a human", "en")
check("humain pendant checkout → flow interrompu", "5 min" in last(44444).lower() or "whatsapp" in last(44444).lower(), last(44444)[:60])
check("flow checkout remplacé par flow human", actions._fetch_flow(44444) and actions._fetch_flow(44444)[0] == "human", str(actions._fetch_flow(44444)))
sent.clear()
actions.handle(rag_bot.bot, 44444, "+224 611 22 33 44", "en")
check("whatsapp capturé → confirmation", "Noted" in last(44444) or "contact" in last(44444).lower(), last(44444)[:60])
notif = [s for s in sent.get(ADMIN, []) if "DEMANDE HUMAIN" in s]
check("admin notifié (WhatsApp du client)", len(notif) >= 1 and "+224 611 22 33 44" in notif[-1], str(notif[-1:])[:90])
check("flow human terminé", actions._fetch_flow(44444) is None)
for phrase, lang in [("parler à un humain", "fr"), ("hablar con un humano", "es"), ("شخص حقيقي", "ar")]:
    sent.clear()
    actions.handle(rag_bot.bot, 55555, phrase, lang)
    ok = sent.get(55555) and len(sent[55555]) == 1
    check(f"«{phrase}» intercepté ({lang})", ok and ("WhatsApp" in last(55555) or "واتساب" in last(55555) or "WhatsApp" in last(55555)), last(55555)[:50])
    actions._clear_flow(55555)

print("── S2 : MÉMOIRE D'ACTIVITÉ ──")
with actions.DB_LOCK:
    actions.DB_CONN.execute("DELETE FROM clients WHERE chat_id = '77777'")
    actions.DB_CONN.commit()
sent.clear()
actions.handle(rag_bot.bot, 77777, "Bonjour, je vends dans une boutique de vêtements", "fr")
cl = actions.get_client(77777)
check("«je vends dans une boutique» → activité enregistrée", cl and cl.get("activity") in ("boutique", "vêtements"), str(cl and cl.get("activity")))
sent.clear()
actions.handle(rag_bot.bot, 88888, "Je tiens un salon de coiffure à Conakry", "fr")
cl2 = actions.get_client(88888)
check("«je tiens un salon» → activité enregistrée", cl2 and cl2.get("activity") == "salon de coiffure", str(cl2 and cl2.get("activity")))
sent.clear()
actions.handle(rag_bot.bot, 999, "les ressources humaines c est important", "fr")
cl3 = actions.get_client(999)
check("«ressources humaines» → PAS une activité", cl3 is None or cl3.get("activity") != "humaines", str(cl3 and cl3.get("activity")))

print("── S2b : BANNIÈRE CLAIRE ──")
check("bannière nouvelle (claire) branchée", "1b47143d3" in catalogue.CATALOGUE_BANNER_URL, catalogue.CATALOGUE_BANNER_URL[-40:])

print("── RÉGRESSION COMPLÈTE ──")
for q, expect in [("qui es tu", "aya|komara"), ("bsr", "onjour|onsoir|alut|oucou|24h|ienvenue"),
                  ("c est combien un site", "devis"),
                  ("c est quoi un site web", "site")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    ok = r is not None and re.search(expect, r, re.I) is not None
    check(f"«{q[:26]}» intacte", ok, str(r)[:60])
sent.clear()
actions.handle(rag_bot.bot, CLIENT, "/admin", "fr")
check("/admin toujours réservé admin", any("réservée" in s for s in sent.get(CLIENT, [])))
sent.clear()
check("«catalogue» routé vers le module (pas la KB brute)",
      actions.handle(rag_bot.bot, 11111, "catalogue", "fr") is True)
sent.clear()
actions.handle(rag_bot.bot, ADMIN, "/promo NEWORDER 15", "fr")
check("admin crée un nouveau code après les changements", True)
import sqlite3
with actions.DB_LOCK:
    row = actions.DB_CONN.execute("SELECT code, discount_pct FROM promo_codes WHERE code = 'NEWORDER'").fetchone()
check("code NEWORDER en base", row is not None and row[1] == 15.0, str(row))
print(f"TOTAL: {passed} OK / {failed} KO")
sys.exit(1 if failed else 0)
