# -*- coding: utf-8 -*-
"""Lot 17 — Tests : scénarios plateformes + compétences, mémoire client,
règle d'or du contexte, pause humaine 2-3,5 s (< 4 s)."""
import os, sys, time, random, json, re
sys.path.insert(0, "/tmp/kb-repo")
os.chdir("/tmp/kb-repo")
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_l17")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_l17")
os.environ["ADMIN_CHAT_ID"] = "99999"

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

import actions, rag_bot
rag_bot.init_memory_db()

# ── 1. Dialogues fusionnés dans les 4 packs ──
print("── PACKS DIALOGUES (A + B) ──")
for lg, path, expected_min in [("fr","docs/aya2/dialogues.json",340),
                                ("en","docs/aya2/dialogues_en.json",225),
                                ("es","docs/aya2/dialogues_es.json",222),
                                ("ar","docs/aya2/dialogues_ar.json",222)]:
    d = json.load(open(path, encoding="utf-8"))
    items = d.get("dialogues", d) if isinstance(d, dict) else d
    check(f"pack {lg} ≥ {expected_min} dialogues", len(items) >= expected_min, len(items))

# scénarios trouvables par langue
CASES = [
    ("fr", "tu crees des bots whatsapp ?", "whatsapp"),
    ("fr", "vous faites des bots pour facebook ?", "facebook|messenger"),
    ("fr", "vous creez des bots telegram ?", "telegram"),
    ("fr", "bot pour tiktok", "tiktok"),
    ("en", "do you make bots for facebook ?", "facebook|messenger"),
    ("en", "can a telegram bot sell my products", "telegram|buttons|chat"),
    ("es", "¿hacéis bots de tiktok?", "tiktok"),
    ("ar", "هل تصنعون بوتات لتيك توك؟", "تيك توك"),
    ("fr", "je veux un logo pour ma marque", "logo|proposition"),
    ("fr", "je veux un site web pour mon entreprise", "site|vitrine"),
    ("fr", "tu fais des visuels pour reseaux sociaux", "visuel|pack|affich"),
    ("fr", "tu fais des videos pour les reseaux", "vidéo|ia|20€"),
    ("fr", "vous formez a l ia aussi", "formation|50€"),
]
for lang, q, expect in CASES:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, lang)
    check(f"[{lang}] «{q[:34]}»", r is not None and re.search(expect, r.lower()), str(r)[:60])

# ── 2. Mémoire conversationnelle (C) ──
print("── MÉMOIRE CLIENT & CONVERSATION ──")
rag_bot.forget(777)
rag_bot.remember(777, "user", "je veux un site pour ma boutique")
rag_bot.remember(777, "assistant", "Super ! C'est pour quel business exactement ?")
hist = rag_bot.context_for(777)
check("mémoire SQLite roundtrip (2 échanges)", len(hist) == 2 and hist[0]["role"] == "user", str(hist)[:70])

# règle d'or : réponse courte à la question du bot → contexte compris
rag_bot.forget(778)
rag_bot.remember(778, "user", "je veux un site pour mon business")
rag_bot.remember(778, "assistant", "Parfait 😊 C'est pour quel business ?")
r = rag_bot.local_contextual_response(778, "coiffure", "fr")
check("règle d'or : réponse courte comprise dans le contexte", r is not None and r != "", str(r)[:60])

# réponse complète aussi : le contexte de la question aide
rag_bot.forget(779)
rag_bot.remember(779, "assistant", "C'est pour quel business ? boutique, resto, services ?")
r = rag_bot.local_contextual_response(779, "salle de sport", "fr")
check("règle d'or : réponse complète contextuelle", r is not None, str(r)[:60])

# pas de boucle : message court SANS question en attente → None
rag_bot.forget(780)
rag_bot.remember(780, "assistant", "Voici le catalogue des services.")
r = rag_bot.local_contextual_response(780, "kjhsdf zqxv", "fr")
check("message court inconnu sans contexte → repli (pas de boucle)", r is None, str(r)[:60])

# ── 3. Pause humaine 2-3,5 s (typing) ──
print("── PAUSE HUMAINE (< 4 s) ──")
captured = []
real_sleep = time.sleep
time.sleep = lambda s: captured.append(s)
try:
    for _ in range(20):
        rag_bot.human_pause(42)
    check("pause humaine ∈ [2, 3,5] s", all(2.0 <= s <= 3.5 for s in captured), captured[:5])
    check("pause aléatoire (pas constante)", len(set(round(s,2) for s in captured)) > 5, "fixe ?")

    # _process_text respecte le délai ET répond
    sent = []
    class FakeBot:
        def send_message(self, cid, text, **k): sent.append((cid, text)); return True
        def send_chat_action(self, *a, **k): return True
        def send_photo(self, *a, **k): return True
    rag_bot.bot = FakeBot()
    t0 = time.time()
    rag_bot._process_text(4242, "c est combien un site web", "fr")
    check("réponse envoyée après pause humaine", len(sent) >= 1 and "devis" in sent[-1][1].lower(), str(sent[-1])[:70] if sent else "rien")
finally:
    time.sleep = real_sleep

# ── 4. Fiche client auto + salutation personnalisée ──
print("── IL SE SOUVIENT DU CLIENT ──")
class FakeUser:
    id = 8888; first_name = "Fatou"; username = "fatou"
class FakeMessage:
    chat = type("C", (), {"id": 8888})()
    text = "salut"
    from_user = FakeUser()
    document = None; voice = None; audio = None; location = None
    caption = None

sent2 = []
class FakeBot2:
    def send_message(self, cid, text, **k): sent2.append((cid, text)); return True
    def send_chat_action(self, *a, **k): return True
    def send_photo(self, *a, **k): return True
old_bot = rag_bot.bot
rag_bot.bot = FakeBot2()
actions.pause_off() if hasattr(actions, "pause_off") else None
try:
    rag_bot._handle_message(FakeMessage())
    cli = actions.get_client(8888)
    check("fiche client créée avec le nom Telegram", cli is not None and cli.get("name") == "Fatou", str(cli)[:60])
    check("salutation personnalisée au retour (prénom)", sent2 and "Fatou" in sent2[-1][1], str(sent2[-1])[:80] if sent2 else "rien")
finally:
    rag_bot.bot = old_bot

# mémoire persiste entre « redéploiements » (nouvelle connexion = même fichier)
rag_bot.forget(4321)
rag_bot.remember(4321, "user", "test persistance")
import sqlite3
conn = sqlite3.connect(str(rag_bot.MEMORY_FILE))
rows = conn.execute("SELECT COUNT(*) FROM memory WHERE chat_id='4321'").fetchone()[0]
conn.close()
check("mémoire écrite sur disque (survit au restart)", rows == 1, rows)

print(f"TOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
