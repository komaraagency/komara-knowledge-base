# -*- coding: utf-8 -*-
"""Lot 32 — Boss 03/10 : 100% KOMARA AGENCY, jamais une autre marque.
  • Prompt : marque KOMARA injectée + verrou photoréaliste par défaut
    (interdit cartoon/plastique/IA factice), sauf demande explicite client.
  • Post-traitement : tout clip-art/watermark tiers (ex: @pollinations.ai)
    recouvert par notre tampon doré KOMARA AGENCY — fiable, indépendant
    du modèle/prompt.
"""
import os
import sys
import tempfile
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

print("── PROMPT : MARQUE + VERROU PHOTORÉALISTE ──")
p_default = img_gen._with_8k_protocol("une femme avec des bijoux dorés")
check("marque KOMARA AGENCY injectée par défaut", "KOMARA AGENCY" in p_default, p_default)
check("verrou : NO cartoon", "NO cartoon" in p_default, p_default)
check("verrou : NO plastic", "NO plastic" in p_default, p_default)
check("verrou : NO fake AI look", "NO fake AI look" in p_default, p_default)
check("verrou : no other brand logo", "no other brand logo" in p_default, p_default)

p_cartoon = img_gen._with_8k_protocol("un lion cartoon rigolo pour enfants")
check("cartoon demandé explicitement → respecté", "cartoon" in p_cartoon.lower(), p_cartoon)
check("cartoon demandé → reste sous marque KOMARA", "KOMARA AGENCY" in p_cartoon, p_cartoon)
check("cartoon demandé → pas de contradiction NO cartoon", "NO cartoon" not in p_cartoon, p_cartoon)

p_anime = img_gen._with_8k_protocol("dessine un personnage manga")
check("manga/anime explicite → cartoon autorisé", "KOMARA AGENCY" in p_anime, p_anime)

p_logo = img_gen._with_8k_protocol("un logo doré")
check("logo : toujours no person (inchangé)", "no person" in p_logo, p_logo)
check("logo : aussi no other brand logo", "no other brand logo" in p_logo, p_logo)

check("_is_cartoon_allowed détecte cartoon", img_gen._is_cartoon_allowed("style cartoon svp"), "")
check("_is_cartoon_allowed détecte dessin animé", img_gen._is_cartoon_allowed("dessin animé"), "")
check("_is_cartoon_allowed False par défaut (photo normale)",
      not img_gen._is_cartoon_allowed("une femme élégante"), "")

print("── POST-TRAITEMENT : TAMPON KOMARA RECOUVRE TOUT CLIP-ART TIERS ──")
from PIL import Image, ImageDraw

TD = Path(tempfile.mkdtemp(prefix="lot32_"))
img = Image.new("RGB", (1280, 1280), (40, 40, 40))
d = ImageDraw.Draw(img)
d.text((1180, 1250), "@pollinations.ai", fill=(255, 255, 255))
test_path = TD / "fake.jpg"
img.save(test_path, "JPEG")
before_bytes = test_path.read_bytes()

img_gen._stamp_brand(test_path)

after = Image.open(test_path)
check("image reste ouvrable après tampon", after.size == (1280, 1280), after.size)
check("le fichier a bien été modifié (tampon appliqué)",
      test_path.read_bytes() != before_bytes, "")
# Le bandeau de tampon doit couvrir le coin bas-droite (zone watermark)
corner = after.crop((after.width - 50, after.height - 20, after.width, after.height))
px = corner.getpixel((25, 10))
check("coin bas-droite recouvert par le bandeau sombre KOMARA",
      sum(px[:3]) < 300, px)  # bandeau (10,10,10) très sombre, pas (255,255,255)

# Best-effort : police manquante ne doit JAMAIS bloquer l'envoi
_orig = img_gen.STAMP_FONT_PATH
img_gen.STAMP_FONT_PATH = Path("/chemin/inexistant.ttf")
img2 = Image.new("RGB", (800, 800), (0, 0, 0))
test_path2 = TD / "fake2.jpg"
img2.save(test_path2, "JPEG")
try:
    img_gen._stamp_brand(test_path2)
    check("police manquante → fallback sans crash", True, "")
except Exception as e:
    check("police manquante → fallback sans crash", False, e)
finally:
    img_gen.STAMP_FONT_PATH = _orig

print(f"\nTOTAL: {OK} OK / {KO} KO")
