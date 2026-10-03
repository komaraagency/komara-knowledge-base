# -*- coding: utf-8 -*-
"""Lot 33 — Boss 03/10 : fidélité au prompt + ethnicité + fiabilité réseau.

Root cause (confirmée via l'API Pollinations) :
  1. `enhance=true` faisait réécrire le prompt du client par une IA
     tierce avant génération -> diluait/ignorait les instructions
     explicites (NO plastic, ethnicité précisée). RETIRÉ.
  2. Aucune ethnicité par défaut cohérente (persona marque) quand le
     client ne précise rien sur un portrait -> ajout du défaut KOMARA
     (West African), jamais imposé si le client précise autre chose.
  3. Le endpoint anonyme Pollinations gate désormais ~50% des requêtes
     (402 Payment Required, constaté en direct le 03/10) -> retry ajouté.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(label, cond, extra=""):
    global OK, KO
    if cond:
        OK += 1
        print("OK ", label)
    else:
        KO += 1
        print("KO ", label, "->", extra)

import img_gen

print("── FIDÉLITÉ PROMPT : enhance=true retiré ──")
check("enhance absent de l'URL Pollinations", "enhance" not in img_gen.POLLINATIONS, img_gen.POLLINATIONS)
check("model=flux conservé (meilleure cohérence)", "model=flux" in img_gen.POLLINATIONS, img_gen.POLLINATIONS)
check("nologo conservé", "nologo=true" in img_gen.POLLINATIONS, img_gen.POLLINATIONS)

print("── ETHNICITÉ : respect strict si précisée, défaut marque sinon ──")
raw = ('photorealistic, real skin texture, natural light, professional '
       'photography, 8k sharp, NO cartoon, NO drawing, NO anime, NO plastic '
       'doll, NO smooth skin, NO fake')
out = img_gen._with_8k_protocol(raw)
check("le prompt client original reste intact en tête", out.startswith(raw), out[:80])
check("négatifs du client toujours présents", "NO plastic doll" in out, out)

p_default = img_gen._with_8k_protocol("portrait d'une femme élégante en studio")
check("portrait sans ethnicité → défaut West African", "West African" in p_default, p_default)

p_eu = img_gen._with_8k_protocol("portrait d'une femme européenne en studio")
check("ethnicité européenne explicite respectée", "européenne" in p_eu, p_eu)
check("pas d'override africain si européen précisé", "West African" not in p_eu, p_eu)

p_as = img_gen._with_8k_protocol("photo d'un homme asiatique en costume")
check("ethnicité asiatique explicite respectée", "asiatique" in p_as, p_as)
check("pas d'override africain si asiatique précisé", "West African" not in p_as, p_as)

p_af = img_gen._with_8k_protocol("portrait d'une femme africaine en studio")
check("ethnicité africaine explicite : pas de doublon de défaut",
      p_af.count("African") + p_af.count("africaine") <= 2, p_af)

p_obj = img_gen._with_8k_protocol("une voiture de sport rouge sur une route")
check("objet (pas une personne) → aucun ajout d'ethnicité", "West African" not in p_obj, p_obj)

print("── NÉGATIFS POSITIONNÉS TÔT (poids fort, pas tronqués en fin) ──")
p_pos = img_gen._with_8k_protocol("un homme guinéen souriant")
check("NO cartoon arrive avant le tag marque/style (pas en toute fin)",
      p_pos.find("NO cartoon") < p_pos.find(img_gen.BRAND_TAG), p_pos)

print("── NON-RÉGRESSION : logo et cartoon explicite inchangés ──")
p_logo = img_gen._with_8k_protocol("un logo doré pour mon business")
check("logo : toujours no person", "no person" in p_logo, p_logo)
p_cartoon = img_gen._with_8k_protocol("un lion cartoon rigolo")
check("cartoon explicite toujours respecté", "cartoon" in p_cartoon.lower(), p_cartoon)
check("cartoon reste sous marque KOMARA", img_gen.BRAND_TAG in p_cartoon, p_cartoon)

print("── FIABILITÉ RÉSEAU : retry sur 402 Payment Required ──")
class FakeResp:
    def __init__(self, code, content=b""):
        self.status_code = code
        self.content = content

_real_get = img_gen.requests.get
_real_sleep = img_gen.time.sleep
img_gen.time.sleep = lambda s: None

calls = {"n": 0}
def flaky_then_ok(url, timeout=None):
    calls["n"] += 1
    return FakeResp(402) if calls["n"] < 3 else FakeResp(200, b"\xff\xd8FAKE")
img_gen.requests.get = flaky_then_ok
content = img_gen._fetch_image("un portrait")
check("retry : succès après 2 échecs 402", content == b"\xff\xd8FAKE", content)
check("retry : 3 tentatives exactement", calls["n"] == 3, calls["n"])

calls["n"] = 0
img_gen.requests.get = lambda url, timeout=None: (calls.__setitem__("n", calls["n"] + 1), FakeResp(402))[-1]
content2 = img_gen._fetch_image("un portrait")
check("échec persistant → abandon propre (None, pas de crash)", content2 is None, content2)
check("échec persistant → s'arrête à MAX_GEN_RETRIES", calls["n"] == img_gen.MAX_GEN_RETRIES, calls["n"])

img_gen.requests.get = _real_get
img_gen.time.sleep = _real_sleep

print(f"\nTOTAL: {OK} OK / {KO} KO")
