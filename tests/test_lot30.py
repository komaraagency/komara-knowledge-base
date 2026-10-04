# -*- coding: utf-8 -*-
"""Lot 30 — Nouveautés du 02/10 (soir) :
  • Seed Aya (21 Q/R ton africain) — chargée au démarrage, idempotente.
  • Portfolio sur Google Drive (upload admin + listing + download).
  • Lien Google robuste : fallback env GOOGLE_REFRESH_TOKEN.
  • Événement Google Calendar créé à chaque RDV.
  • Photo admin → portfolio Drive ; photo client → QR (inchangé).
  • /kb_modele : 3 modèles d'apprentissage (FAQ, CSV, JSON) envoyés.
  • /kb_import : parseurs FAQ + CSV toujours valides.
"""
import os, sys, io, tempfile, json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TELEGRAM_TOKEN"] = "123:TEST"
os.environ["ADMIN_CHAT_ID"] = "99999"
TD = Path(tempfile.mkdtemp(prefix="lot30_"))
os.environ["ACTIONS_DIR"] = str(TD)
os.environ["MEMORY_DIR"] = str(TD)

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

# ── 1. Seed Aya : 20 Q/R, idempotente, servies ─────────────────────────────
import rag_bot
import aya_seed, knowledge_store

print("── SEED AYA ──")
check("25 Q/R définies (20 + logo + 4 définitions Boss 04/10)", len(aya_seed.SEED_QR) == 25, len(aya_seed.SEED_QR))
# Vider puisSeeder
knowledge_store._CUSTOM_ROWS = []
r1 = aya_seed.ensure_seed("fr")
check("seed charge 25 en runtime", len(knowledge_store._CUSTOM_ROWS) == 25,
      len(knowledge_store._CUSTOM_ROWS))
check("seed persistée = 0 (Google non lié en test)", r1["persisted"] == 0, r1)
# Idempotent : re-seed n'ajoute rien
n_before = len(knowledge_store._CUSTOM_ROWS)
r2 = aya_seed.ensure_seed("fr")
check("re-seed idempotent", len(knowledge_store._CUSTOM_ROWS) == n_before, n_before)
# Réponse servie pour une question seed
resp = rag_bot.trouver_meilleure_reponse_multilingue("qui es tu", "fr")
check("seed 'qui es tu' répond Aya", resp and "Aya" in resp, str(resp)[:80])
resp2 = rag_bot.trouver_meilleure_reponse_multilingue("prix", "fr")
check("seed 'prix' répond avec GNF", resp2 and "GNF" in resp2, str(resp2)[:80])

# ── 2. Portfolio Drive (mocké) ────────────────────────────────────────────
import portfolio_drive

print("── PORTFOLIO DRIVE ──")
class FakeResp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code = status; self._d = data or {}; self.content = content
        self.text = json.dumps(data or {})
    def json(self): return self._d

class FakeReq:
    def __init__(self):
        self.posts = []; self.gets = []; self.created = []
    def get(self, url, headers=None, params=None, timeout=30):
        self.gets.append(url)
        q = (params or {}).get("q", "")
        if "files" in url and "'FOLDER1' in parents" in q:
            return FakeResp(200, {"files": [
                {"id": "img1", "name": "logo1.jpg", "createdTime": "2026-10-02T10:00:00Z"},
                {"id": "img2", "name": "affiche2.png", "createdTime": "2026-10-02T09:00:00Z"},
            ]})
        if "files" in url and "vnd.google-apps.folder" in q:
            return FakeResp(200, {"files": [{"id": "FOLDER1", "name": "Komara Bot - Portfolio"}]})
        if "alt" in str(params or {}):
            return FakeResp(200, content=b"IMGDATA")
        return FakeResp(200, {"files": []})
    def post(self, url, headers=None, json=None, data=None, timeout=30):
        self.posts.append((url, json or data))
        if "files" in url:
            self.created.append(json or {})
            return FakeResp(201, {"id": "NEW1"})
        return FakeResp(201, {"id": "EVT1"})

fr_req = FakeReq()
portfolio_drive._folder_id = ""  # reset cache
with patch.object(portfolio_drive, "requests", fr_req), \
     patch.object(portfolio_drive, "get_access_token", lambda: "FAKE"):
    fid = portfolio_drive.get_portfolio_folder_id()
    check("dossier portfolio trouvé/créé", fid == "FOLDER1", fid)
    uid = portfolio_drive.upload_image("test.jpg", b"BYTES")
    check("upload image renvoie id", uid == "NEW1", uid)
    imgs = portfolio_drive.list_images()
    check("liste 2 images (récentes d'abord)", len(imgs) == 2 and imgs[0]["id"] == "img1", imgs)
    data = portfolio_drive.download_image("img1")
    check("download renvoie bytes", data == b"IMGDATA", data[:20])

# ── 3. Fallback env GOOGLE_REFRESH_TOKEN ─────────────────────────────────
print("── LIEN GOOGLE : FALLBACK ENV ──")
import google_link
google_link.DB_CONN = None  # simule disque éphémère vide
google_link._access_token = ""; google_link._token_expiry = 0
os.environ["GOOGLE_REFRESH_TOKEN"] = "ENV_REFRESH_123"
class TokResp(FakeResp):
    def __init__(self): super().__init__(200, {"access_token": "ATOK", "expires_in": 3600})
with patch.object(google_link, "requests") as mk:
    mk.post.return_value = TokResp()
    tok = google_link.get_access_token()
check("token obtenu depuis env (DB vide)", tok == "ATOK", tok)
del os.environ["GOOGLE_REFRESH_TOKEN"]

# ── 4. Événement Calendar au RDV ─────────────────────────────────────────
print("── CALENDAR RDV ──")
with patch.object(google_link, "get_access_token", lambda: "FAKE"), \
     patch.object(google_link, "requests") as mk:
    mk.post.return_value = FakeResp(201, {"id": "EVT1"})
    eid = google_link.create_calendar_event("RDV Test", __import__("datetime").datetime(2026,10,3,10,0), 30)
check("événement Calendar créé", eid == "EVT1", eid)
# Non lié → ""
with patch.object(google_link, "get_access_token", lambda: ""):
    check("non lié → pas d'événement", google_link.create_calendar_event("x", __import__("datetime").datetime.now()) == "")

# ── 5. Photo admin → portfolio ; photo client → QR ───────────────────────
print("── PHOTO ADMIN vs CLIENT ──")
class FakeFile: file_path = "x"
class FakeBot:
    def __init__(self): self.sent = []
    def get_file(self, fid): return FakeFile()
    def download_file(self, path): return b"IMG"
    def send_message(self, cid, text, **k): self.sent.append(("msg", text)); return True
    def send_chat_action(self, *a, **k): return True
fb = FakeBot()
rag_bot.bot = fb  # _handle_message utilise le bot module-global
class FakeMsg:
    def __init__(self, cid, has_photo=True):
        self.chat = type("C",(),{"id":cid})()
        self.photo = [type("P",(),{"file_id":"f1"})()] if has_photo else None
        self.location = None; self.document = None; self.voice = None
        self.audio = None; self.text = None; self.caption = None
        self.message_id = 1
up_calls = []
with patch.object(portfolio_drive, "upload_image", lambda n,d: up_calls.append(n) or "NEW1"):
    try: rag_bot._handle_message(FakeMsg(99999))
    except Exception: pass
    check("admin photo → portfolio upload appelé", len(up_calls) == 1, up_calls)
# client photo ne doit PAS appeler upload_image (mais QR scan peut échouer)
up_calls2 = []
with patch.object(portfolio_drive, "upload_image", lambda n,d: up_calls2.append(n) or "NEW1"):
    try: rag_bot._handle_message(FakeMsg(11111))
    except Exception: pass
    check("client photo → portfolio NON appelé", len(up_calls2) == 0, up_calls2)

# ── 6. /kb_modele envoie 3 documents ─────────────────────────────────────
print("── /kb_modele ──")
fb2 = FakeBot()
fb2.sent_docs = []
fb2.send_document = lambda cid, data, visible_file_name=None, caption=None: fb2.sent_docs.append(visible_file_name)
import actions
actions._send_kb_templates(fb2, 99999)
check("3 modèles envoyés (FAQ, CSV, JSON)", len(fb2.sent_docs) == 3, fb2.sent_docs)
check("noms corrects", any(".txt" in n for n in fb2.sent_docs) and any(".csv" in n for n in fb2.sent_docs) and any(".json" in n for n in fb2.sent_docs), fb2.sent_docs)

# ── 7. Parseurs /kb_import (FAQ + CSV) ───────────────────────────────────
print("── PARSEURS KB_IMPORT ──")
import kb_import
faq = b"Q: test question ?\nA: test reponse\n\nQ: autre ?\nA: autre reponse"
parsed = kb_import.parse_txt(faq.decode())
check("parse FAQ Q:/A: → 2 entrées", len(parsed) == 2 and parsed[0][0].startswith("test"), parsed)
csv_data = b"question;rponse\nvous livrez;oui\nlogo prix;300k"
parsed_c = kb_import.parse_csv(csv_data.decode())
check("parse CSV → 2 entrées (en-tête ignoré)", len(parsed_c) == 2, parsed_c)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
