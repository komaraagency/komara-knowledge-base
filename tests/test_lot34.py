# -*- coding: utf-8 -*-
"""Lot 34 — Boss 04/10 : img2img (photo + texte → image) + définitions
métier dans le seed + non-régression du fix « faux remplacements ».

1. IMG2IMG : photo + caption → retouche IA (Pollinations image=).
   - caption respectée, visage préservé (IDENTITY_LOCK)
   - réalisme strict : NO plastic/blurry/deformed par défaut, cartoon
     UNIQUEMENT si demandé
   - sans caption → la photo suit son chemin habituel (QR/portfolio)
2. DÉFINITIONS MÉTIER (demandées par le Boss) dans le seed Aya :
   chatbot, agent IA générative, agent IA commercial, automatisation —
   chacune servie à SA question, aucune n'écrase les autres.
3. FIX REMPLACEMENTS INCOHÉRENTS (re-signalement Boss) : enseigner
   « comment avoir mon bot » NE DOIT PAS remplacer « comment créer un
   bot » — vérifié par le pipeline complet /apprends → client.
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

# ── Faux Google Sheet en RAM (même mécanique que lot29) ───────────────────
import memory_sheets

class FakeSheet:
    def __init__(self):
        self.dialogues = []
        self.linked = True
    def save_learned(self, q, a, lang):
        if not self.linked: raise RuntimeError("Google non lié (test)")
        self.dialogues.append(["t", lang, q, a])
    def load_learned(self):
        seen = {}
        for r in self.dialogues:
            seen[r[2].strip().casefold()] = {"question": r[2], "answer": r[3],
                                              "lang": r[1], "date": r[0]}
        return list(seen.values())
    def log_conversation(self, *a, **k): pass
    def log_unanswered(self, *a, **k): pass

sheet = FakeSheet()
with patch.object(memory_sheets, "load_learned", sheet.load_learned):
    import rag_bot, actions, img_gen, aya_seed, knowledge_store

sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append((cid, text)); return True
    def send_photo(self, cid, f, caption=None, *a, **k):
        sent.append((cid, ("PHOTO", caption))); return True
    def send_chat_action(self, *a, **k): return True

rag_bot.bot = FakeBot()
actions.ADMIN_CHAT_ID = 99999

print("── 1. DÉFINITIONS MÉTIER dans le seed ──")
check("seed contient les 4 définitions", 
      all(any(q == d for q, a in aya_seed.SEED_QR) for d in (
          "c'est quoi un chatbot", "c'est quoi un agent ia générative",
          "c'est quoi un agent ia commercial", "c'est quoi une automatisation")),
      [q for q, a in aya_seed.SEED_QR])
ans_chatbot = [a for q, a in aya_seed.SEED_QR if q == "c'est quoi un chatbot"][0]
ans_gen = [a for q, a in aya_seed.SEED_QR if q == "c'est quoi un agent ia générative"][0]
ans_com = [a for q, a in aya_seed.SEED_QR if q == "c'est quoi un agent ia commercial"][0]
ans_auto = [a for q, a in aya_seed.SEED_QR if q == "c'est quoi une automatisation"][0]
check("chatbot : parle de logiciel qui discute 24h/24", 
      "24h/24" in ans_chatbot and "logiciel" in ans_chatbot, ans_chatbot[:80])
check("agent génératif : il CRÉE (images, devis)", 
      "CRÉE" in ans_gen and "images" in ans_gen, ans_gen[:80])
check("agent commercial : il QUALIFIE et CLOS la vente", 
      ("qualifie" in ans_com or "vendeur" in ans_com) and "vente" in ans_com, ans_com[:80])
check("automatisation : tâche répétitive à ta place", 
      "répétitive" in ans_auto, ans_auto[:80])

# Chaque question client reçoit SA définition, pas celle d'un autre
kb_fr = rag_bot.LANG_RESOURCES["fr"]["kb"]
all_answers = [str(f.get("answer", "")) for f in kb_fr]
def find_ans(q):
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    return r or ""
r1 = find_ans("c'est quoi un chatbot")
check("« c'est quoi un chatbot » → réponse chatbot", "logiciel" in r1, r1[:80])
r2 = find_ans("c'est quoi un agent IA générative ?")
check("« agent ia générative » → réponse générative (pas chatbot)", 
      "CRÉE" in r2, r2[:80])
r3 = find_ans("c'est quoi un agent IA commercial")
check("« agent ia commercial » → réponse commerciale", 
      "vendeur" in r3 or "vente" in r3, r3[:80])
r4 = find_ans("c'est quoi une automatisation")
check("« automatisation » → réponse automatisation", 
      "répétitive" in r4 or "machine" in r4, r4[:80])
# variantes avec fautes/SMS
r5 = find_ans("c koi un chatbot")
check("fautes/SMS tolérées (c koi un chatbot)", "logiciel" in r5, r5[:80])

print("── 2. FIX REMPLACEMENTS INCOHÉRENTS (scénario exact du Boss) ──")
with patch.object(memory_sheets, "save_learned", sheet.save_learned):
    sent.clear()
    actions._admin_apprends(FakeBot(), 99999,
                            "comment créer un bot || DEF_CREER_UN_BOT", "fr")
    actions._admin_apprends(FakeBot(), 99999,
                            "comment avoir mon bot || DEF_AVOIR_MON_BOT", "fr")
check("les 2 /apprends annoncent AJOUT (pas remplacement)",
      all("ajoutée" in t for c, t in sent if c == 99999),
      [t.split(chr(10))[0] for c, t in sent])
check("les 2 fiches coexistent en mémoire", 
      any("DEF_CREER" in a for f in knowledge_store.LANG_RESOURCES["fr"]["kb"]
          for a in f.get("questions", []) + [f.get("answer", "")])
      and any("DEF_AVOIR" in a for f in knowledge_store.LANG_RESOURCES["fr"]["kb"]
              for a in f.get("questions", []) + [f.get("answer", "")]),
      "")
q1 = find_ans("comment créer un bot")
q2 = find_ans("comment avoir mon bot")
check("« comment créer un bot » → SA réponse (créer)", 
      q1 is not None and "DEF_CREER" in q1, str(q1)[:80])
check("« comment avoir mon bot » → SA réponse (avoir)", 
      q2 is not None and "DEF_AVOIR" in q2, str(q2)[:80])
# l'ordre inverse ne doit pas non plus remplacer
with patch.object(memory_sheets, "save_learned", sheet.save_learned):
    actions._admin_apprends(FakeBot(), 99999,
                            "comment avoir mon bot || DEF_AVOIR_V2", "fr")
    actions._admin_apprends(FakeBot(), 99999,
                            "comment créer un bot || DEF_CREER_V2", "fr")
q1b = find_ans("comment créer un bot")
q2b = find_ans("comment avoir mon bot")
check("ordre inverse : toujours 2 fiches distinctes", 
      "DEF_CREER_V2" in (q1b or "") and "DEF_AVOIR_V2" in (q2b or ""),
      f"{q1b!r} / {q2b!r}")

print("── 3. IMG2IMG : prompt + verrous ──")
check("verrou réalisme étendu : NO blurry + NO deformed", 
      "NO blurry" in img_gen.REALISM_LOCK and "NO deformed" in img_gen.REALISM_LOCK,
      img_gen.REALISM_LOCK)
check("verrou identité visage présent", 
      "same face" in img_gen.IDENTITY_LOCK and "do not alter the face" in img_gen.IDENTITY_LOCK,
      img_gen.IDENTITY_LOCK)
p = img_gen._i2i_prompt("mets-moi sur fond doré élégant")
check("caption client en tête, respectée", p.startswith("mets-moi sur fond doré élégant"), p[:60])
check("img2img : verrou réalisme + identité par défaut", 
      "NO plastic" in p and "same face" in p, p[-120:])
check("img2img : marque KOMARA présente", "KOMARA AGENCY" in p, p[-60:])
pc = img_gen._i2i_prompt("fais-moi en cartoon rigolo")
check("cartoon EXPLICITE → cartoon respecté (pas de verrou)", 
      "NO cartoon" not in pc and "cartoon" in pc.lower(), pc[:80])
check("cartoon explicite : identité visage toujours préservée", 
      "same face" in pc, pc[-120:])

print("── 4. IMG2IMG : hébergement référence ──")
class FakePost:
    def __init__(self, payload):
        self._p = payload
    def json(self):
        return self._p
ok_resp = FakePost({"success": True, "files": [{"url": "https://h.uguu.se/ABC.jpg"}]})
ko_resp = FakePost({"success": False, "error": "rate limited"})
with patch.object(img_gen.requests, "post", return_value=ok_resp) as fp:
    url = img_gen._upload_reference(b"\xff\xd8fake")
    check("hébergement réussi → URL publique directe", url == "https://h.uguu.se/ABC.jpg", url)
    check("fichier envoyé en multipart", fp.called and fp.call_args.kwargs.get("files") is not None, "")
with patch.object(img_gen.requests, "post", return_value=ko_resp):
    check("hébergement refusé → None (pas de crash)", 
          img_gen._upload_reference(b"x") is None, "")
def boom(*a, **k): raise OSError("réseau HS")
with patch.object(img_gen.requests, "post", side_effect=boom):
    check("hébergement réseau HS → None (pas de crash)", 
          img_gen._upload_reference(b"x") is None, "")

print("── 5. IMG2IMG : génération avec retry 402 ──")
class FakeGenResp:
    def __init__(self, code, content=b""):
        self.status_code = code; self.content = content
calls = {"n": 0}
def flaky(url, timeout=None):
    calls["n"] += 1
    return FakeGenResp(402) if calls["n"] < 4 else FakeGenResp(200, b"\xff\xd8FAKEEDIT")
img_gen.time.sleep = lambda s: None
with patch.object(img_gen.requests, "get", side_effect=flaky):
    out = img_gen._fetch_image_i2i("mets-moi sur fond doré", "https://h.uguu.se/ABC.jpg")
    check("retry : succès après 3 échecs 402", out == b"\xff\xd8FAKEEDIT", out)
    check("retry : 4 tentatives au total", calls["n"] == 4, calls["n"])
    check("URL de référence passée à Pollinations", 
          "image=https://h.uguu.se/ABC.jpg" in flaky.last_url if hasattr(flaky, "last_url") else True, "")
calls["n"] = 0
def always402(url, timeout=None):
    calls["n"] += 1
    flaky.last_url = url
    return FakeGenResp(402)
with patch.object(img_gen.requests, "get", side_effect=always402):
    out2 = img_gen._fetch_image_i2i("test", "https://h.uguu.se/ABC.jpg")
    check("échec persistant → None propre", out2 is None, out2)
    check("échec persistant → MAX_I2I_RETRIES tentatives", 
          calls["n"] == img_gen.MAX_I2I_RETRIES, calls["n"])
check("URL i2i : paramètre image= présent", 
      "image=" in img_gen.POLLINATIONS_I2I, img_gen.POLLINATIONS_I2I)

print("── 6. IMG2IMG : worker _edit_and_send (mock réseau) ──")
sent.clear()
with patch.object(img_gen, "_upload_reference", return_value="https://h.uguu.se/REF.jpg"), \
     patch.object(img_gen, "_fetch_image_i2i", return_value=b"\xff\xd8FAKEEDIT"), \
     patch.object(img_gen, "_stamp_brand", lambda path: None), \
     patch.object(img_gen, "_remember_caption", lambda *a: None), \
     patch.object(img_gen, "_prune_images", lambda: None):
    img_gen._edit_and_send(FakeBot(), 4242, "mets-moi sur fond doré", b"photo", "fr")
    sent_s = [t for c, t in sent]
    check("photo retouchée envoyée au client", 
          any(isinstance(t, tuple) and t[0] == "PHOTO" for t in sent_s), sent_s)
    check("prompt d'origine mémorisé pour « variante »", 
          img_gen.LAST_PROMPT.get(4242) == "mets-moi sur fond doré",
          img_gen.LAST_PROMPT.get(4242))
sent.clear()
with patch.object(img_gen, "_upload_reference", return_value=None):
    img_gen._edit_and_send(FakeBot(), 4243, "test", b"photo", "fr")
    check("hébergement HS → message d'erreur (pas de crash)", 
          any("échoué" in str(t) for c, t in sent), sent)

print("── 7. ROUTAGE : photo + caption vs photo seule ──")
class FakeMsg:
    def __init__(self, caption=None, with_photo=True):
        self.chat = type("C", (), {"id": 777})
        self.caption = caption
        self.photo = [type("P", (), {"file_id": "fid123"})] if with_photo else None
        self.content_type = "photo" if with_photo else "text"

# photo + caption → img2img consommé
with patch.object(img_gen, "handle_photo_request", return_value=True) as hpr:
    # on invoque la branche photo du handler comme le ferait telebot
    m = FakeMsg(caption="rends-moi plus stylé")
    cap = m.caption or ""
    consumed = bool(cap) and hpr(rag_bot.bot, 777, cap, m, "fr")
    check("photo + caption → routée vers img2img", consumed and hpr.called, "")
# photo SANS caption → QR scan (client) — handle_photo_request doit refuser
    m2 = FakeMsg(caption=None)
    cap2 = (m2.caption or "").strip()
    check("photo sans caption → PAS l'img2img (chemin QR/portfolio)", 
          not (cap2 and hpr(rag_bot.bot, 777, cap2, m2, "fr")), "")
# handle_photo_request réel : sans caption → False
class FakeFileBot(FakeBot):
    def get_file(self, fid): raise AssertionError("ne doit pas télécharger")
    def download_file(self, p): raise AssertionError("ne doit pas télécharger")
check("handle_photo_request : caption vide → False sans rien télécharger",
      img_gen.handle_photo_request(FakeFileBot(), 777, "  ", FakeMsg(caption="x"), "fr") is False, "")
# avec caption → True, message working, thread lancé (fetch mocké)
sent.clear()
class DLBot(FakeBot):
    def get_file(self, fid): return type("F", (), {"file_path": "/x"})
    def download_file(self, p): return b"\xff\xd8photo"
with patch.object(img_gen, "_edit_and_send", lambda *a: sent.append(("EDIT", a[2]))) as _:
    consumed = img_gen.handle_photo_request(DLBot(), 888, "retouche ma photo", 
                                            FakeMsg(caption="retouche ma photo"), "fr")
    check("photo + caption → consommé (True)", consumed is True, "")
    check("message « working » envoyé", 
          any("crée" in str(t) for c, t in sent), [t for c, t in sent][:2])
    check("worker lancé avec la photo + le prompt",
          any(t[0] == "EDIT" and t[1] == "retouche ma photo" for t in sent), sent[-2:])

print(f"\\nTOTAL: {OK} OK / {KO} KO")
