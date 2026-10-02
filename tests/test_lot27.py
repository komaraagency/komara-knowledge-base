#!/usr/bin/env python3
"""Lot 27 — LETTRE MASTER FINALE : suite de tests.

Conforme à la lettre : fix admin (détection langue FR, format devis
express), fix client (1 seul accueil, devis en 2 échanges max, prix
toujours visible), multilingue 4 langues complet (lang/*.json),
changement de langue persistant.
"""

import json
import logging
from datetime import datetime
import os
import sys
import types
from pathlib import Path

logging.disable(logging.INFO)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_TOKEN"] = "123:TEST"
os.environ["ADMIN_CHAT_ID"] = "99999"
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_lot27")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_lot27")
# run isolé : on repart d'une base vierge à chaque exécution
import shutil as _sh
_sh.rmtree("/tmp/kb-repo/data_lot27", ignore_errors=True)

import rag_bot as rb
import actions

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("✅" if ok else "❌"), name, ("— " + detail if detail and not ok else ""))


# ── Setup mocks ─────────────────────────────────────────────────────────
SENT: list = []
ADMIN: list = []
rb.bot.send_message = lambda c, t, **k: SENT.append(t)
rb.bot.send_photo = lambda *a, **k: None
actions.notify_admin = lambda bot, m: ADMIN.append(m)
rb.notify_admin = lambda bot, m: ADMIN.append(m)


def say(chat, mid, text, name="Mamadou"):
    SENT.clear()
    m = types.SimpleNamespace(
        chat=types.SimpleNamespace(id=chat), message_id=mid, text=text,
        voice=None, audio=None, document=None, location=None, photo=None,
        from_user=types.SimpleNamespace(first_name=name))
    rb.handle_message(m)
    return list(SENT)


rb.init_memory_db()

# ── 1. PARTIE A admin : détection langue ────────────────────────────────
check("A1. « J'ai plusieurs articles a vendre » -> fr (bug screen)",
      rb.detect_language("J'ai plusieurs articles a vendre") == "fr")
check("A2. « j'ai plusieurs articles à vendre » -> fr",
      rb.detect_language("j'ai plusieurs articles à vendre") == "fr")
check("A3. FR standard inchangé (« Bonjour »)",
      rb.detect_language("Bonjour") == "fr")
check("A4. EN/ES/AR standards inchangés",
      rb.detect_language("Hello, how much does a website cost?") == "en"
      and rb.detect_language("Hola, ¿cuánto cuesta un sitio web?") == "es"
      and rb.detect_language("مرحبا، كم تكلفة موقع ويب؟") == "ar")

# ── 2. PARTIE B client : 1 seul accueil ─────────────────────────────────
SENT.clear()
r = say(90001, 1, "Bonjour")
check("B1. 'Bonjour' (nouveau client) = 1 SEUL message", len(r) == 1)
check("B2. nouveau client PAS « Re-bonjour »",
      len(r) == 1 and "Re-bonjour" not in r[0])

# revenant (2+ évènements) → salutation perso
say(90002, 10, "Bonjour")
chat_devis, mid = 90002, 11
say(chat_devis, mid, "devis"); mid += 1
say(chat_devis, mid, "1"); mid += 1
say(chat_devis, mid, "boutique"); mid += 1
r = say(90002, mid, "Bonjour")
check("B3. client REVENANT -> salutation perso",
      len(r) == 1 and "Re-bonjour" in r[0])

# ── 3. PARTIE B : devis 2 échanges MAX (anti-fuite) ─────────────────────
chat, mid = 90003, 20
r = say(chat, mid, "je souhaite un devis")
mid += 1
check("C1. 'je souhaite un devis' démarre le flux (grille services)",
      r and "Quel service" in r[0])
r = say(chat, mid, "1")
mid += 1
check("C2. après service : UNE SEULE question (activité)",
      r and "activité" in r[0].lower() and "délai" not in r[0].lower()
      and "promo" not in r[0].lower())
r = say(chat, mid, "boutique")
mid += 1
check("C3. devis DIRECT après activité (questions délai/promo supprimées)",
      r and ("FIXE" in r[0] or "Total" in r[0]))
check("C4. devis en 1 message", len(r) == 1)
check("C5. conversion locale indicative dans le devis",
      r and ("Chez toi" in r[0] or "FG" in r[0]))
check("C6. hint code promo non bloquant (/code)",
      r and "/code" in r[0])
check("C7. prix € FIXE (pas 'estimé')", r and "FIXE" in r[0])

# ── 4. PARTIE B : admin devis express toujours FR + format lettre ───────
adm = [a for a in ADMIN if "DEVIS EXPRESS" in a]
check("D1. admin notifié du devis", bool(adm))
if adm:
    a = adm[-1]
    check("D2. format : Service :", "🛒 Service :" in a)
    check("D3. format : Prix FIXE", "💰 Prix FIXE :" in a)
    check("D4. format : conversion locale + indicatif",
          "💱" in a and "indicatif" in a)
    check("D5. format : Langue du client", "- Langue: fr" in a)
    check("D6. format : Demande d'origine + chat_id",
          "💬 Demande" in a and "🆔 chat_id:" in a)

# ── 5. PARTIE A : prix TOUJOURS visible dans la commande ────────────────
chat2, mid2 = 90004, 40
r = say(chat2, mid2, "commander")
mid2 += 1
if r and "Quel service" in r[0]:
    r = say(chat2, mid2, "1")
    check("E1. commande : prix affiché dès le choix du service",
          r and "Prix" in r[0] and "€" in r[0])
else:
    check("E1. flux commande démarre", False, repr(r[:1]))

# ── 6. Moteur langue : changement + persistance ──────────────────────────
r = say(90005, 60, "español")
check("F1. 'español' -> confirmation en ES", r and "español" in r[0])
r = say(90005, 61, "ok")
check("F2. langue PERSISTE après message sans signal ('ok' -> reste ES)",
      rb._LAST_LANG.get(90005) == "es")
r = say(90006, 62, "العربية")
check("F3. 'العربية' -> confirmation en AR",
      r and "العربية" in r[0])

# ── 7. Moteur darija (lettre : salam/khoya -> ar) ───────────────────────
check("G1. « Salam » seul -> ar",
      rb.detect_language_session("Salam", None) == "ar")
check("G2. « Salam, je veux un site web » reste fr (client guinéen)",
      rb.detect_language_session("Salam, je veux un site web", None) == "fr")
check("G3. « khoya bghit bot » -> ar",
      rb.detect_language_session("khoya bghit bot", None) == "ar")
check("G4. « hola quiero un logo » -> es",
      rb.detect_language_session("hola quiero un logo", None) == "es")

# ── 8. PARTIE B multilingue : lang/*.json complets et cohérents ─────────
lang_dir = Path(__file__).resolve().parent.parent / "lang"
tabs = {}
for lg in ("fr", "en", "es", "ar"):
    tabs[lg] = json.loads((lang_dir / f"{lg}.json").read_text(encoding="utf-8"))
check("H1. 4 fichiers lang/*.json présents", len(tabs) == 4)
check("H2. mêmes clés dans les 4 langues",
      set(tabs["fr"]) == set(tabs["en"]) == set(tabs["es"]) == set(tabs["ar"]),
      f"fr={len(tabs['fr'])}")
check("H3. actions.T chargé depuis lang/*.json (69 clés x4)",
      all(len(actions.T[lg]) >= 69 for lg in ("fr", "en", "es", "ar")))
CLIENT_KEYS = ["devis_start", "devis_activity", "devis_calc", "order_activity",
               "order_done", "off_hours", "known_greeting", "bot_closed",
               "rdv_start", "lead_done", "survey_done", "tracking_none"]
check("H4. clés CLIENT traduites en AR (plus de repli FR silencieux)",
      all(k in tabs["ar"] for k in CLIENT_KEYS))

# ── 9. Promo non bloquante : /code puis devis -> appliqué auto ───────────
chat3, mid3 = 90007, 80
actions._PENDING_PROMO.clear()
with actions.DB_LOCK:
    actions.DB_CONN.execute(
        "INSERT OR REPLACE INTO promo_codes "
        "(code, discount_pct, uses, max_uses, active, created_at) "
        "VALUES ('LOT27', 20, 0, 10, 1, datetime('now'))")
    actions.DB_CONN.commit()
r = say(chat3, mid3, "/code LOT27"); mid3 += 1
check("I1. /code validé", r and "LOT27" in r[0])
r = say(chat3, mid3, "devis"); mid3 += 1
r = say(chat3, mid3, "1"); mid3 += 1
r = say(chat3, mid3, "restaurant"); mid3 += 1
joined = "\n".join(r)
check("I2. code promo appliqué SANS question bloquante",
      "-20%" in joined or "20%" in joined)

# ── Résultat ────────────────────────────────────────────────────────────
ok = sum(1 for _, o, _ in results if o)
ko = len(results) - ok
print(f"\nTOTAL: {ok} OK / {ko} KO")
sys.exit(0 if ko == 0 else 1)
