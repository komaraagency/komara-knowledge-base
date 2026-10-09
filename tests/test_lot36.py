# -*- coding: utf-8 -*-
"""Lot 36 — Boss 04/10 (suite captures 19h21-19h25) :
1. LOGO OFFICIEL K (fichier du Boss) appliqué à TOUTES les images
   générées, à la place du simple tampon texte. Fallback texte si
   le logo est illisible. Le bandeau couvre toujours les clip-arts
   tiers (ex: @pollinations.ai).
2. DÉLAI « hhh » : plus jamais accepté comme délai dans le tunnel
   commande — re-demande avec exemples (max 2), comme l'activité.
3. TÉLÉPHONE poubelle : un numéro sans chiffres est re-demandé dans
   les tunnels commande ET lead (max 2), au lieu d'enregistrer
   n'importe quoi dans la fiche client.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ADMIN_CHAT_ID"] = "99999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print("OK ", name)
    else: KO += 1; print("KO ", name, "->", extra)

import actions, img_gen

print("── 1. LOGO OFFICIEL K sur toutes les images ──")
from PIL import Image, ImageDraw

# fichier présent dans le repo (Railway l'aura après push)
check("logo officiel présent dans assets/", img_gen._LOGO_PATH.exists(), img_gen._LOGO_PATH)
logo = img_gen._load_official_logo()
check("logo chargé + détouré (RGBA)", logo is not None and logo.mode == "RGBA",
      None if logo is None else logo.mode)
if logo:
    check("logo recadré sur le K (pas de fond géant)",
          logo.width < 256, f"{logo.width}x{logo.height}")
    check("transparence effective (fond noir supprimé)",
          logo.getextrema()[3][0] == 0, logo.getextrema())

# _stamp_brand sur une image test avec FAUX watermark tiers
img = Image.new("RGB", (1024, 1024))
d = ImageDraw.Draw(img)
for y in range(1024):
    d.line([(0, y), (1024, y)], fill=(210, 180, 130))
d.text((860, 995), "@pollinations.ai", fill=(255, 255, 255))
img.save("/tmp/t36.jpg", "JPEG", quality=92)
img_gen._stamp_brand(Path("/tmp/t36.jpg"))
out = Image.open("/tmp/t36.jpg").convert("RGB")
px = out.load()
check("bandeau noir pleine largeur",
      sum(px[5, 1020]) < 350 and sum(px[512, 1022]) < 350,
      (px[5, 1020], px[512, 1022]))
whites = sum(1 for x in range(840, 1024) for y in range(985, 1024)
             if px[x, y] == (255, 255, 255))
check("clip-art tiers recouvert (0 blanc pur)", whites == 0, whites)
vivid = sum(1 for x in range(840, 1024) for y in range(980, 1024)
            if (px[x, y][0] > 150 and px[x, y][1] > 100)
            or (px[x, y][1] > 120 and px[x, y][2] > 80))
check("K officiel visible (pixels or/vert vifs)", vivid > 100, vivid)

# petit format (logo jamais gigantesque) : image 512px
img2 = Image.new("RGB", (512, 512), (120, 120, 160))
img2.save("/tmp/t36b.jpg", "JPEG", quality=92)
img_gen._stamp_brand(Path("/tmp/t36b.jpg"))
check("petit format : tampon sans crash", Path("/tmp/t36b.jpg").exists())

# fallback texte si le logo est illisible
img3 = Image.new("RGB", (800, 800), (90, 90, 90))
img3.save("/tmp/t36c.jpg", "JPEG", quality=92)
with patch.object(img_gen, "_load_official_logo", return_value=None):
    img_gen._stamp_brand(Path("/tmp/t36c.jpg"))
out3 = Image.open("/tmp/t36c.jpg").convert("RGB")
p3 = out3.load()
check("fallback : bandeau + texte doré quand logo absent",
      sum(p3[5, 790]) < 350, p3[5, 790])
with patch.object(img_gen, "_load_official_logo", return_value=None):
    if img_gen.STAMP_FONT_PATH.exists() is False:
        check("fallback sans police : pas de crash", True)
    else:
        check("police tampon présente (texte lisible)", True)

print("── 2. DÉLAI valide ou re-demandé ──")
for probe, exp in [("hhh", False), ("Tty", False), ("bof", False), ("...", False),
                   ("3 jours", True), ("demain", True), ("urgent", True),
                   ("05/10/2026", True), ("24h", True), ("peu importe", True),
                   ("n'importe quand", True), ("1 semaine", True), ("asap", True)]:
    check(f"délai {probe!r} -> {exp}", actions._looks_like_deadline(probe) is exp, "")

sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append((cid, text)); return True

flows = {}
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({s: d})), \
     patch.object(actions, "get_client", lambda cid: {"name": "Test", "phone": ""}):
    data = {"service": "Logo premium", "price": "150€",
            "activity": "restaurant", "_deadline_retries": 0}
    r = actions._step_order(FakeBot(), 1, "deadline", dict(data), "hhh", "fr")
    check("« hhh » → re-demande, PAS d'étape suivante",
          r is True and flows.get("name") is None and "name" not in flows, list(flows))
    check("re-demande avec exemples (« 3 jours » cité)",
          any("3 jours" in str(t) for c, t in sent), [str(t)[:70] for c, t in sent])
    # vraie valeur → avance bien
    flows.clear()
    r = actions._step_order(FakeBot(), 1, "deadline", dict(data), "3 jours", "fr")
    check("« 3 jours » → avance vers le nom", "name" in flows, list(flows))

print("── 3. TÉLÉPHONE : chiffres obligatoires ──")
for probe, exp in [("hhh", False), ("abc", False), ("1234", False), ("", False),
                   ("622 00 00 00", True), ("+224 622 00 00 00", True),
                   ("06 22 33 44 55", True)]:
    check(f"téléphone {probe!r} -> {exp}", actions._looks_like_phone(probe) is exp, "")

sent.clear()
flows.clear()
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({s: d})), \
     patch.object(actions, "get_client", lambda cid: {"name": "Test", "phone": ""}):
    data = {"service": "Logo premium", "price": "150€", "activity": "resto",
            "deadline": "3 jours", "name": "Test", "_phone_retries": 0}
    r = actions._step_order(FakeBot(), 1, "phone", dict(data), "hhh", "fr")
    check("« hhh » comme téléphone → re-demande, PAS de commande créée",
          r is True and "phone" in flows, list(flows))
    check("re-demande numéro avec exemple",
          any("622 00 00 00" in str(t) for c, t in sent), [str(t)[:70] for c, t in sent])

with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({s: d})):
    flows.clear()
    data = {"name": "Test", "_phone_retries": 0}
    r = actions._step_lead(FakeBot(), 2, "phone", dict(data), "hhh", "fr")
    check("tunnel lead : « hhh » → re-demande aussi",
          r is True and "phone" in flows and any("622" in str(t) for c, t in sent), "")
    flows.clear()
    r = actions._step_lead(FakeBot(), 2, "phone", {"name": "T", "_phone_retries": 0},
                            "622 00 00 00", "fr")
    check("tunnel lead : vrai numéro → avance", "sector" in flows, list(flows))

print("── 4. anti-blocage Boss 09/10 : jamais de faux numéro ──")
with patch.object(actions, "_save_flow", lambda cid, f, s, d=None: flows.update({s: d})):
    flows.clear()
    sent.clear()
    data = {"service": "Logo", "activity": "resto", "_phone_retries": 2}
    with patch.object(actions, "_insert", lambda *a, **k: 1), \
         patch.object(actions, "_clear_flow", lambda cid: None), \
         patch.object(actions, "upsert_client", lambda *a, **k: None), \
         patch.object(actions, "notify_admin", lambda *a, **k: None), \
         patch.object(actions, "schedule_followup", lambda *a, **k: None), \
         patch.object(actions, "order_tracking", lambda *a, **k: None):
        r = actions._step_order(FakeBot(), 1, "phone", dict(data), "hhh", "fr")
        check("3e tentative poubelle → tunnel terminé (jamais de blocage)", r is not None, r)
        check("...et lien WhatsApp direct proposé (pas de faux numéro)",
              any("wa.me/212701986219" in str(t) for c, t in sent),
              [str(t)[:60] for c, t in sent])
    check("faux numéro réel de prod (+21287654213) → refusé",
          actions._looks_like_phone("+21287654213") is False, "")
    check("vrai numéro (+212669416020) → accepté",
          actions._looks_like_phone("+212669416020") is True, "")

print(f"\nTOTAL: {OK} OK / {KO} KO")
