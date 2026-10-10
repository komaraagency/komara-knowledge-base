# -*- coding: utf-8 -*-
"""test_seed_v2 — Boss 10/10 : refonte du seed (liste de questions,
sécurité de chargement, lock atomique, perf du filtre exact).

Historique : le séparateur « | » dans les données cassait la
déduplication (le pipe-string entier n'était jamais reconnu connu ->
re-seed complet et doublons massifs dans le Sheet à chaque
redémarrage), et un échec de lecture Google était confondu avec une
base vide."""
import os
import sys
import threading
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ["ADMIN_CHAT_ID"] = "99999"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print("OK ", name)
    else: KO += 1; print("KO ", name, "->", extra)

import rag_bot  # initialise le store (Google non lié en test)
import aya_seed
import knowledge_store as ks
import memory_sheets

def _reset_store():
    ks._CUSTOM_ROWS = []
    ks.rebuild_known_questions()

print("── 1. STRUCTURE : listes de questions, zéro séparateur ──")
check("SEED_QR = (liste, réponse) partout",
      all(isinstance(qs, list) and isinstance(a, str)
          for qs, a in aya_seed.SEED_QR))
check("aucune question ne contient « | »",
      not any("|" in q for qs, a in aya_seed.SEED_QR for q in qs))
check("SeedResult importable et typrable",
      set(aya_seed.SeedResult.__annotations__) >= {"loaded", "persisted", "missing"})
check("faute corrigée : « qui es-tu » en 1re variante",
      "qui es-tu" in aya_seed.SEED_QR[5][0][0 if False else 0] or
      any(qs and qs[0] == "qui es-tu" for qs, a in aya_seed.SEED_QR))
_flat = {q: a for qs, a in aya_seed.SEED_QR for q in qs}
check("« c'est quoi un agent IA génératif » présent",
      "c'est quoi un agent IA génératif" in _flat)
check("réponse : « connaît » (plus jamais « connait »)",
      "connaît" in _flat["c'est quoi un agent ia générative"] and
      "connait" not in _flat["c'est quoi un agent ia générative"])
check("anglicisme corrigé : « conclut la vente »",
      "conclut la vente" in _flat["c'est quoi un chatbot"] and
      "et close" not in _flat["c'est quoi un chatbot"])
check("contact WhatsApp harmonisé (FR)",
      any("+212 701 986 219" in a for qs, a in aya_seed.SEED_QR for q in [qs[0]] for a in [a]) or
      any("+212 701 986 219" in a for qs, a in aya_seed.SEED_QR))
ar_flat = {q: a for qs, a in aya_seed.SEED_QR_ML["ar"] for q in qs}
check("contact WhatsApp harmonisé (AR)",
      any("+212 701 986 219" in a for a in ar_flat.values()))
check("prix injectés depuis PRICES (défaut 50 €)",
      "50 €" in _flat["prix"] and "300 000 GNF" in _flat["prix"])
check("PRICES pilotable par variable d'env",
      aya_seed.PRICES["LOGO"] == os.environ.get("PRICE_LOGO", "50 €"))

print("── 2. DÉDUPLICATION PAR VARIANTE (le bug des doublons) ──")
_reset_store()
# le Sheet contient déjà « qui es-tu | qui es tu » (variante pipe réelle)
ks._CUSTOM_ROWS = [{"question": "qui es-tu | qui es tu",
                    "answer": "Réponse du boss", "lang": "fr"}]
ks.rebuild_known_questions()
r = aya_seed.ensure_seed("fr")
all_qs = [str(row.get("question", "")) for row in ks.custom_rows_snapshot()]
check("l'entrée identité est IGNORÉE (déjà connue par variante)",
      not any("tu es qui" in q for q in all_qs), all_qs[:5])
check("les 39 autres fiches sont publiées",
      len(all_qs) == 1 + 39, len(all_qs))
check("loaded = 40 (base complète)", r["loaded"] == 40, r)
check("persisted = 0 (Google non lié en test)", r.get("persisted", 0) == 0, r)

_reset_store()
ks._CUSTOM_ROWS = [{"question": "salut", "answer": "Salut boss", "lang": "fr"}]
ks.rebuild_known_questions()
aya_seed.ensure_seed("fr")
check("variante exacte simple reconnue (pas de doublon de « salut »)",
      sum(1 for row in ks.custom_rows_snapshot()
          if "salut" in str(row.get("question", "")).casefold()) == 1)

print("── 3. SHEET LIÉ MAIS ILLISIBLE -> SEED ANNULÉ (échec silencieux) ──")
_reset_store()
_calls = {"persist": 0}
real_is_configured = memory_sheets.is_configured
real_load = memory_sheets.load_learned
memory_sheets.is_configured = lambda: True
def _boom(strict=False):
    raise memory_sheets.SheetUnavailableError("HTTP 503 quota")
memory_sheets.load_learned = _boom
real_batch = ks.learn_entries_batch
def _no_batch(*a, **k):
    _calls["persist"] += 1
    return real_batch(*a, **k)
ks.learn_entries_batch = _no_batch
r = aya_seed.ensure_seed("fr")
check("seed ANNULÉ avec erreur explicite", bool(r.get("error")), r)
check("rien publié en runtime", len(ks.custom_rows_snapshot()) == 0,
      len(ks.custom_rows_snapshot()))
check("AUCUNE tentative de persistance (zéro doublon Sheet)",
      _calls["persist"] == 0, _calls)
memory_sheets.is_configured = real_is_configured
memory_sheets.load_learned = real_load
ks.learn_entries_batch = real_batch

print("── 4. IDEMPOTENCE + FILTRE EXACT (perf) ──")
_reset_store()
r1 = aya_seed.ensure_seed("fr")
n1 = len(ks.custom_rows_snapshot())
r2 = aya_seed.ensure_seed("fr")
check("1er appel : 40 fiches runtime_only",
      n1 == 40 and r1.get("runtime_only") is True, (n1, r1.get("runtime_only")))
check("2e appel : rien ajouté (idempotent)",
      len(ks.custom_rows_snapshot()) == n1, len(ks.custom_rows_snapshot()))
check("is_question_known : exact O(1)",
      ks.is_question_known("Salut") and ks.is_question_known("qui es-tu") and
      not ks.is_question_known("question inconnue du tout xyz"))
check("known_questions : variantes splitées, pas de pipe-string",
      "qui es-tu" in ks.known_questions() and
      not any("|" in q for q in ks.known_questions()))

print("── 5. CONCURRENCE : pas de double seed (lock atomique) ──")
_reset_store()
results = []
def _worker():
    results.append(aya_seed.ensure_seed("fr"))
threads = [threading.Thread(target=_worker) for _ in range(3)]
for t in threads: t.start()
for t in threads: t.join()
total = len(ks.custom_rows_snapshot())
check("3 threads en parallèle -> exactement 40 fiches (pas 120)",
      total == 40, total)

print("── 6. SEED ML : refresh UNIQUEMENT des langues modifiées ──")
_reset_store()
_refreshed = []
real_refresh = ks.refresh_resources
ks.refresh_resources = lambda lang: _refreshed.append(lang) or real_refresh(lang)
ks._CUSTOM_ROWS = [{"question": aya_seed.SEED_QR_ML["en"][0][0][0],
                    "answer": "already known", "lang": "en"}]
ks.rebuild_known_questions()
rml = aya_seed.ensure_seed_ml()
check("fiche EN déjà connue -> non republiée", rml["published"] == 4,
      rml)  # 5 fiches ML - 1 connue = 4 (en restant: 1, es: 2, ar: 1)
check("« en » rafraîchi UNE seule fois", _refreshed.count("en") == 1, _refreshed)
check("« fr » jamais rafraîchi par le seed ML", "fr" not in _refreshed, _refreshed)
rml2 = aya_seed.ensure_seed_ml()
check("2e appel ML : 0 publication", rml2["published"] == 0, rml2)
ks.refresh_resources = real_refresh

print("── 7. API PUBLIQUE : plus d'accès privé depuis l'extérieur ──")
snap = ks.custom_rows_snapshot()
snap.append({"question": "pirate", "answer": "x", "lang": "fr"})
check("snapshot défensif (mutation locale sans effet)",
      not any(str(r.get("question")) == "pirate"
              for r in ks.custom_rows_snapshot()))
check("store_lock utilisable (context manager)",
      ks.store_lock().__enter__ is not None)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
