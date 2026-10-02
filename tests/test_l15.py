import sys, re, json
sys.path.insert(0, '.')
import rag_bot, actions, catalogue, skills, img_gen
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

ES_ONLY = re.compile(r"¿|Nuestro|Estimado|Sus servicios")
AR_ONLY = re.compile(r"[\u0600-\u06FF]{4,}")
print("── ANTI-MÉLANGE DE LANGUES ──")
# question EN qui n'existe PAS en EN → doit retomber sur FR (jamais ES/AR)
r = rag_bot.trouver_meilleure_reponse_multilingue("what are your delivery delays", "en")
check("question EN sans fiche EN → FR (pas ES/AR)", r is not None and not ES_ONLY.search(r) and not AR_ONLY.search(r), str(r)[:80])
r = rag_bot.trouver_meilleure_reponse_multilingue("i want the chatbot for my shop", "en")
check("EN bot → réponse EN", r is not None and not ES_ONLY.search(r) and not AR_ONLY.search(r), str(r)[:80])
r = rag_bot.trouver_meilleure_reponse_multilingue("quiero un bot para mi tienda", "es")
check("ES bot → réponse ES (usted)", r is not None and not AR_ONLY.search(r) and ("usted" in r.lower() or "su " in r.lower() or "Le " in r), str(r)[:80])
r = rag_bot.trouver_meilleure_reponse_multilingue("اريد بوت لمتجري", "ar")
check("AR bot → réponse AR", r is not None and AR_ONLY.search(r) and not ES_ONLY.search(r), str(r)[:80])
r = rag_bot.trouver_meilleure_reponse_multilingue("c est quoi un chatbot", "fr")
check("FR question → réponse FR", r is not None and not AR_ONLY.search(r) and not ES_ONLY.search(r), str(r)[:60])
# le fallback générique est dans la langue du client
from rag_bot import MESSAGES as LANG_MESSAGES
check("fallback EN existe en EN (honnête lot 19)", "knowledge" in LANG_MESSAGES.get("en", {}).get("fallback", "") or "menu" in LANG_MESSAGES.get("en", {}).get("fallback", ""))
check("fallback AR existe en AR (honnête lot 19)", "قاعد" in LANG_MESSAGES.get("ar", {}).get("fallback", "") or "مواقع" in LANG_MESSAGES.get("ar", {}).get("fallback", ""))

sent.clear()
ok = actions.handle(rag_bot.bot, 555, "تسعيرة", "ar")
check("devis se déclenche en AR", ok, last(555)[:60])
sent.clear()
ok = actions.handle(rag_bot.bot, 556, "حجز موعد", "ar")
check("rdv se déclenche en AR", ok, last(556)[:60])
import rag_bot as _rb
check("clavier AR 100% arabe", "Devis" not in str(_rb.KEYBOARDS["ar"]) and "Rendez-vous" not in str(_rb.KEYBOARDS["ar"]))
check("clavier FR intact", "📄 Devis" in str(_rb.KEYBOARDS["fr"]))

print("── PRIX UNIFIÉS SUR TOUT LE REPO ──")
PROT = re.compile(r"ROI|GNF|par jour|per day|par heure|€/hora|commission|pagamos|ندفع|Contratar|salaire|salary|salari|empleado|employee|2500€|3000€|rapporte|aporta|brings you|يجلب|يكلف|de por vida|per client|par client|psychologique|de l'heure|in the hour|التوظيف|مدى الحياة|per change|في الساعة|por vida|salary", re.I)
BOT = re.compile(r"komara|bot|assistant|pack|site|logo|visuel|agency|agence|vitrina|showcase|الوكالة|بوت|باقة", re.I)
bad = 0
for path in ["kb.json","lang/en/kb.json","lang/es/kb.json","lang/ar/kb.json"]:
    d = json.load(open(path, encoding="utf-8"))
    items = d if isinstance(d, list) else d.get("knowledge", [])
    for it in items:
        a = str(it.get("answer",""))
        for m in re.finditer(r"(\d[\d\s.,]*)\s*(€|\$)", a):
            val = re.sub(r"[\s.,]","",m.group(1))
            if not val.isdigit(): continue
            v = int(val)
            ctx = a[max(0,m.start()-70):m.end()+50]
            if v not in (15,20,25,40,50,100,120,150) and BOT.search(ctx) and not PROT.search(ctx):
                bad += 1
import glob
for path in glob.glob("dialogues/*.md") + glob.glob("lang/*/dialogues/*.md") + glob.glob("lang/*/faq.md") + ["docs/faq.md"]:
    txt = open(path, encoding="utf-8").read()
    for m in re.finditer(r"(\d[\d\s.,]*)\s*(€|\$|درهم)", txt):
        val = re.sub(r"[\s.,]","",m.group(1))
        if not val.isdigit(): continue
        v = int(val)
        ctx = txt[max(0,m.start()-60):m.end()+40]
        if v not in (15,20,25,40,50,100,120,150) and BOT.search(ctx) and not PROT.search(ctx):
            bad += 1
check("zéro prix hors catalogue (KB 4 langues + dialogues + FAQ)", bad == 0, f"{bad} restants")

# questions tarif → réponse catalogue cohérente dans CHAQUE langue
for q, lang, expect in [("c est quoi vos tarifs", "fr", "15€|100€|50€"),
                        ("what are your prices", "en", "15€|100€"),
                        ("cuanto cuestan sus servicios", "es", "15€|100€"),
                        ("كم اسعاركم", "ar", "15€|100€")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, lang)
    check(f"tarifs {lang}", r is not None and re.search(expect, r) is not None, str(r)[:70])

print("── TOUT Y EST (inventaire des lots) ──")
for q, expect in [("bsr", "onsoir|ienvenue|alut"), ("cc", "oucou|24h|ienvenue"),
                  ("calcule 250 x 3", None), ("quelle heure est il", None)]:
    if expect is None: continue
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q}»", r is not None and re.search(expect, r, re.I) is not None, str(r)[:60])
# compétences (calcul + heure) via skills.handle
sent.clear()
ok = skills.handle(rag_bot.bot, 111, "calcule 250 x 3", "fr")
check("calculatrice 250x3", ok and "750" in last(111), last(111)[:50])
sent.clear()
ok = skills.handle(rag_bot.bot, 112, "quelle heure est il", "fr")
check("heure/date", ok and "GMT" in last(112), last(112)[:50])
sent.clear()
ok = skills.handle(rag_bot.bot, 113, "800 - 15%", "fr")
check("remise 800-15%", ok and "680" in last(113), last(113)[:50])
sent.clear()
ok = skills.handle(rag_bot.bot, 114, "what time is it", "en")
check("heure EN", ok and "GMT" in last(114), last(114)[:50])
# knowledge.txt chargé
check("knowledge.txt (persona)", len(rag_bot.load_knowledge_txt()) > 500)
# protocole 8K images
pr = img_gen._with_8k_protocol("belle affiche")
check("protocole 8K images", "Sony A7R V" in pr and "#D4AF37" in pr)
# objection prix = phrase exacte
r = rag_bot.trouver_meilleure_reponse_multilingue("c est trop cher pourquoi", "fr")
check("objection prix phrase exacte", r is not None and "excellence est le seul chemin" in r, str(r)[:70])
# réclamation → admin + pas de pitch
sent.clear()
actions.handle(rag_bot.bot, 222, "c est une arnaque je veux porter plainte", "fr")
check("réclamation → admin", any("RÉCLAMATION" in t for c, t in sent if c == 99999), last(222)[:50])
check("réclamation sans pitch", "devis" not in last(222).lower() and "catalogue" not in last(222).lower())
# humain + /promo
sent.clear()
actions.handle(rag_bot.bot, 99999, "/promo AUDIT15 15", "fr")
actions.handle(rag_bot.bot, 333, "/promo AUDIT15", "fr")
check("/promo client", "-15%" in last(333), last(333)[:50])
sent.clear()
actions.handle(rag_bot.bot, 444, "talk to a human", "en")
check("humain EN", "WhatsApp" in last(444) or "5 min" in last(444))
# ton : FR tutoiement / ES usted
r = rag_bot.trouver_meilleure_reponse_multilingue("qui es tu", "fr")
check("Aya FR intacte", r is not None and ("aya" in r.lower() or "Komara" in r))
r = rag_bot.trouver_meilleure_reponse_multilingue("quien es usted", "es")
check("ES vouvoyé intact", r is not None)

print("── PURGE HORS-DOMAINE (lot 16) ──")
for q, lang in [("c est quoi la photosynthese","fr"), ("c est quoi la tabaski","fr"),
                ("comment lire une analyse medicale","fr"), ("quelle est la capitale de la france","fr"),
                ("what is an atom","en"), ("what is the capital of france","en"),
                ("que es un atomo","es"), ("cual es la capital de francia","es"),
                ("ما هو الذرة","ar"), ("ما هي عاصمة فرنسا","ar")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, lang)
    ENCYCLO = re.compile(r"atome|atom|capital|capitale|photosynth|molecule|molécule|planète|noyau|proton|adn|arn|cellule", re.I)
    check(f"hors-domaine «{q[:22]}» → pivot vente ou repli", r is None or not ENCYCLO.search(r), str(r)[:50])

print("── RÉGRESSION GÉNÉRALE ──")
for q, expect in [("qui es tu", "aya|komara"), ("c est combien un site", "devis"),
                  ("je sais pas trop ce que je veux", "vends")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q[:28]}»", r is not None and re.search(expect, r, re.I) is not None, str(r)[:60])
print(f"TOTAL: {passed} OK / {failed} KO")
sys.exit(1 if failed else 0)
