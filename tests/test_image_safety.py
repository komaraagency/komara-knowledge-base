# ──────────────────────────────────────────────────────────────────────────
# test_image_safety — blindage anti-NSFW (Boss 09/10, captures 23h51-23h53)
#
# « banane tricolore » et « lion en costume » ont livré des NUS. Trois
# couches serveur, testées ici : filtre entrant, prompt positif,
# contrôle sortant (détecteur local) qui échoue FERMÉ.
# ──────────────────────────────────────────────────────────────────────────
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")

import image_safety as S
import img_gen as G

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

print("── 1. Filtre ENTRANT : demandes à refuser ──")
BAD = [
    "image de femme nue", "génère une image de fille topless", "photo d'une femme sexy",
    "naked woman", "nude girl on beach", "image de seins", "photo de fesses",
    "femme sans vêtements", "image porno", "fille en lingerie", "femme en bikini",
    "image érotique", "une femme sensuelle en sous-vêtements", "NSFW portrait",
    "dessine un homme nu", "deshabille cette femme", "image de poitrine",
    "undress her", "sexual content", "photo de pussy", "sans habits", "photo de culotte",
]
for p in BAD:
    check(f"refusé : {p}", S.is_prompt_unsafe(p))

print("── 2. Filtre ENTRANT : demandes LÉGITIMES à laisser passer ──")
GOOD = [
    "banane tricolore", "image d'un lion en costume, style affiche pro",
    "logo pour mon resto", "affiche promo pour ma boutique de vêtements",
    "portrait d'un entrepreneur africain en costume", "femme d'affaires en tailleur",
    "un chat sur un canapé", "paysage de Conakry au coucher du soleil",
    "voiture de sport rouge", "plat de riz sauce arachide", "poster pour un concert",
    "logo pour mon salon de coiffure", "un cheval dans un champ",
    "business woman portrait", "image de maillot de foot de la Guinée",
    "bouteille de parfum luxe", "montre en or sur fond noir",
    "tenue traditionnelle bazin riche", "mariage guinéen en boubou",
]
for p in GOOD:
    check(f"autorisé : {p}", not S.is_prompt_unsafe(p), "(faux positif)")

print("── 3. Prompt FINAL : aucune négation sexuelle, aucun souffle de concept ──")
for p in ["banane tricolore", "image d'un lion en costume, style affiche pro",
          "portrait d'un entrepreneur", "logo pour mon resto", "dessine un cartoon de chat"]:
    f = G._with_8k_protocol(p).lower()
    for banned in ("nudity", "nude", "sexual", "explicit", "safe-for-work", "family-friendly"):
        check(f"« {banned} » absent du prompt « {p[:25]} »", banned not in f, f)

print("── 4. Sujet SANS personne : zéro vocabulaire portrait ──")
for p in ["banane tricolore", "image d'un lion en costume, style affiche pro"]:
    f = G._with_8k_protocol(p).lower()
    for portrait in ("skin texture", "pores", "85mm", "distorted face", "extra fingers"):
        check(f"« {portrait} » absent pour « {p[:25]} »", portrait not in f, f)
f = G._with_8k_protocol("banane tricolore").lower()
check("banane : prompt client en tête", f.startswith("banane tricolore"))
check("banane : cadrage produit/pub", "commercial" in f or "poster" in f)

print("── 5. Sujet AVEC personne : habillé en positif ──")
f = G._with_8k_protocol("portrait d'un entrepreneur").lower()
check("personne : vêtements pro demandés", "business attire" in f and "full-coverage" in f, f)

print("── 6. Contrôle SORTANT : échoue FERMÉ ──")
class FakeDet:
    def __init__(self, hits): self.hits = hits
    def detect(self, p): return self.hits
tmp = tempfile.mkdtemp(); img = os.path.join(tmp, "x.jpg"); open(img, "wb").write(b"\xff\xd8x")

S._detector = FakeDet([{"class": "FEMALE_BREAST_EXPOSED", "score": 0.9}])
check("nu exposé 0.9 -> bloqué", S.image_is_unsafe(img))
S._detector = FakeDet([{"class": "FEMALE_BREAST_EXPOSED", "score": 0.40}])
check("nu exposé 0.40 (> seuil) -> bloqué", S.image_is_unsafe(img))
S._detector = FakeDet([{"class": "FEMALE_BREAST_EXPOSED", "score": 0.20}])
check("score faible 0.20 -> passe", not S.image_is_unsafe(img))
S._detector = FakeDet([{"class": "BUTTOCKS_EXPOSED", "score": 0.8}])
check("fesses exposées -> bloqué", S.image_is_unsafe(img))
S._detector = FakeDet([{"class": "FEMALE_GENITALIA_EXPOSED", "score": 0.5}])
check("organe exposé -> bloqué", S.image_is_unsafe(img))
S._detector = FakeDet([{"class": "FACE_FEMALE", "score": 0.95},
                       {"class": "FEMALE_BREAST_COVERED", "score": 0.9},
                       {"class": "ARMPITS_EXPOSED", "score": 0.8}])
check("visage/vêtu/aisselles -> passe", not S.image_is_unsafe(img))
S._detector = FakeDet([])
check("image vide de détection -> passe", not S.image_is_unsafe(img))

class BoomDet:
    def detect(self, p): raise RuntimeError("boom")
S._detector = BoomDet()
check("détecteur plante -> FERMÉ (bloqué)", S.image_is_unsafe(img))
S._detector = None; S._detector_failed = True
check("détecteur absent -> FERMÉ (bloqué)", S.image_is_unsafe(img))
S.STRICT = False
check("STRICT=0 + absent -> passe (opt-out explicite)", not S.image_is_unsafe(img))
S.STRICT = True

print("── 7. Chaîne complète : un nu n'est JAMAIS envoyé ──")
G.IMAGES_DIR = type(G.IMAGES_DIR)(tmp)
G._stamp_brand = lambda p: None
class Bot:
    def __init__(self): self.sent = []; self.msgs = []
    def send_photo(self, cid, f, caption=None): self.sent.append(cid)
    def send_message(self, cid, t, **k): self.msgs.append(t)
    def send_chat_action(self, *a, **k): pass

# 7a. toujours un nu -> 3 tentatives, 0 photo envoyée, message de repli
S._detector = FakeDet([{"class": "FEMALE_BREAST_EXPOSED", "score": 0.95}])
calls = {"n": 0}
G._fetch_image = lambda p: (calls.__setitem__("n", calls["n"] + 1) or b"\xff\xd8fake")
b = Bot(); G._generate_and_send(b, 1, "banane tricolore", "fr")
check("nu x3 -> AUCUNE photo envoyée", b.sent == [], b.sent)
check("nu x3 -> exactement 3 tentatives", calls["n"] == 3, calls)
check("nu x3 -> message de repli poli", len(b.msgs) == 1 and "propre" in b.msgs[0], b.msgs)
check("nu x3 -> fichiers douteux supprimés", [f for f in os.listdir(tmp) if f.startswith("img_")] == [])

# 7b. nu puis propre -> la 2e passe, une seule photo
seq = iter([[{"class": "FEMALE_BREAST_EXPOSED", "score": 0.9}], []])
class SeqDet:
    def detect(self, p): return next(seq)
S._detector = SeqDet()
b = Bot(); G._generate_and_send(b, 2, "banane tricolore", "fr")
check("nu puis propre -> 1 photo envoyée", b.sent == [2], b.sent)

# 7c. propre d'emblée
S._detector = FakeDet([])
b = Bot(); G._generate_and_send(b, 3, "banane tricolore", "fr")
check("propre -> 1 photo envoyée", b.sent == [3], b.sent)

# 7d. détecteur absent -> RIEN n'est livré
S._detector = None; S._detector_failed = True
b = Bot(); G._generate_and_send(b, 4, "banane tricolore", "fr")
check("détecteur absent -> aucune photo", b.sent == [], b.sent)
S._detector_failed = False

# 7e. img2img
S._detector = FakeDet([{"class": "BUTTOCKS_EXPOSED", "score": 0.9}])
G._upload_reference = lambda d: "http://ref"
G._fetch_image_i2i = lambda c, u: b"\xff\xd8fake"
b = Bot(); G._edit_and_send(b, 5, "mets-moi en costume", b"photo", "fr")
check("img2img nu -> aucune photo envoyée", b.sent == [], b.sent)

print("── 8. Routage : demande nue refusée AVANT tout appel au générateur ──")
S._detector = FakeDet([])
spawned = {"n": 0}
G._generate_and_send = lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1)
b = Bot()
ok = G.handle_image_request(b, 6, "génère une image de femme nue", "fr")
check("demande nue : message consommé", ok is True)
check("demande nue : AUCUN thread de génération", spawned["n"] == 0, spawned)
check("demande nue : message de refus", any("ne génère pas" in m for m in b.msgs), b.msgs)
for lang, frag in {"en": "don't generate", "es": "No genero", "ar": "لا أنشئ"}.items():
    b = Bot(); G.handle_image_request(b, 7, "/image naked woman", lang)
    check(f"refus {lang}", any(frag in m for m in b.msgs), b.msgs)
b = Bot(); spawned["n"] = 0
G.handle_image_request(b, 8, "Génère moi une image de banane tricolore", "fr")
check("banane : génération lancée", spawned["n"] == 1, spawned)

# variante d'un prompt devenu douteux
G.LAST_PROMPT[9] = "femme nue"
b = Bot(); G._regenerate_variant(b, 9, "fr")
check("variante d'un prompt nu -> refus", any("ne génère pas" in m for m in b.msgs), b.msgs)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
