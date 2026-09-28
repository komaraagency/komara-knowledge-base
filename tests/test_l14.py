import sys, re, json
sys.path.insert(0, '.')
import rag_bot, actions, catalogue
rag_bot.init_memory_db(); actions.init_db()
passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"OK  {name}")
    else: failed += 1; print(f"KO  {name} -> {extra[:110]}")

sent = []
class FakeBot:
    def send_chat_action(self, *a, **k): return True
    def send_message(self, cid, text, **k): sent.append((cid, text)); return True
    def send_photo(self, *a, **k): return True
rag_bot.bot = FakeBot()
def last(cid): return [t for c, t in sent if c == cid][-1] if any(c == cid for c, t in sent) else ""

print("── ES : vouvoiement premium ──")
TUTEO = re.compile(r"\btienes\b|\bquieres\b|\bpuedes\b|\bestás\b|\bvendes\b|\bhaces\b|\bnecesitas\b|\bcontigo\b", re.I)
es_tests = [
    ("quiero un sitio web para mi restaurante", "su escenario"),
    ("vendo productos y quiero vender en linea", "Le montamos"),
    ("cuanto cuesta un logo", None),
    ("quiero un bot para mi negocio", None),
    ("hola", None),
]
for q, expect in es_tests:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "es")
    no_tuteo = r is None or not TUTEO.search(r)
    ok = no_tuteo and (expect is None or (r and expect in r))
    check(f"ES «{q[:32]}» sans tuteo", ok, str(r)[:70])

# Lot 22 (F4) : nouveau client → qualification budget AVANT la grille
# devis (ordre strict spec). Le bot ES vouvoie (usted) dans la question.
sent.clear()
actions.handle(rag_bot.bot, 555, "devis", "es")
dev = last(555)
check("devis ES → qualification usted",
      "presupuesto" in dev.lower() and not TUTEO.search(dev), dev[:60])
sent.clear()
# routeur réel : _process_text essaie actions.handle PUIS commercial_pack
if not actions.handle(rag_bot.bot, 555, "B", "es"):
    import commercial_pack
    commercial_pack.handle(rag_bot.bot, 555, "B", "es")
dev2 = last(555)
check("réponse B → grille devis ES",
      "¿Qué servicio" in dev2 or "servicio" in dev2.lower(), dev2[:50])

# questions client gardent le tuteo (matching préservé)
kb = json.load(open("lang/es/kb.json", encoding="utf-8"))
items = kb if isinstance(kb, list) else kb.get("knowledge", [])
qs = {q.lower() for it in items for q in it.get("questions", [])}
check("question client «explicame» conservée", "explicame" in qs)

print("── FR : Aya et son «tu» intacts ──")
r = rag_bot.trouver_meilleure_reponse_multilingue("qui es tu", "fr")
check("qui es tu → Aya", r is not None and ("aya" in r.lower() or "komara" in r.lower()))
for q, expect in [("bsr", "onsoir|ienvenue|alut"), ("c est trop cher pourquoi", "excellence"),
                  ("comment se passe une commande", "50%"), ("c est quoi ton style", "luxury")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"FR «{q[:30]}» intacte", r is not None and re.search(expect, r, re.I), str(r)[:60])
# le pack Aya2 FR garde le tutoiement
d = json.load(open("docs/aya2/dialogues.json", encoding="utf-8"))
items = d if isinstance(d, list) else d.get("dialogues", [])
blob = json.dumps(items, ensure_ascii=False)
check("Aya2 FR : tutoiement conservé", "tu " in blob or "Ton " in blob)

print("── EN/AR inchangés et pro ──")
r = rag_bot.trouver_meilleure_reponse_multilingue("i want a website for my restaurant", "en")
check("EN site restaurant", r is not None and ("restaurant" in r.lower() or "site" in r.lower()))
r = rag_bot.trouver_meilleure_reponse_multilingue("اريد موقع الكتروني لمطعمي", "ar")
check("AR site restaurant", r is not None)

print("── régression générale ──")
sent.clear()
actions.handle(rag_bot.bot, 99999, "/promo VIVA14 14", "fr")
actions.handle(rag_bot.bot, 777, "/promo VIVA14", "fr")
check("/promo client+admin OK", "VALIDE" in last(777) and "-14%" in last(777), last(777)[:60])
sent.clear()
actions.handle(rag_bot.bot, 888, "me han estafado, quiero una denuncia", "es")
check("réclamation ES → admin notifié", any("RÉCLAMATION" in t for c, t in sent if c == 99999), last(888)[:60])
check("accusé ES vouvoyé", "su mensaje" in last(888) or "Gracias" in last(888))
sent.clear()
actions.handle(rag_bot.bot, 889, "hablar con un humano", "es")
check("humain ES vouvoyé", "le contacta" in last(889).lower() or "Déjeme" in last(889), last(889)[:60])
r = rag_bot.trouver_meilleure_reponse_multilingue("c est combien un site", "fr")
check("devis routing FR intact", r is not None and "devis" in r.lower())
print(f"TOTAL: {passed} OK / {failed} KO")
sys.exit(1 if failed else 0)
