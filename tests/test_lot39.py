# -*- coding: utf-8 -*-
"""Lot 39 — Boss 05/10 : pipeline mémoire Aya en 7 étapes (adapté du
schéma ML fourni par le Boss : collecte → prétraitement → conversion
numérique → modèle → entraînement → évaluation/ajustement → déploiement)
+ commande admin /evaluation.

Adaptation RÉELLE (pas de TensorFlow) : Railway tourne sur CPU, le
pivot zéro-donnée interdit un corpus d'entraînement sur disque, et le
moteur déterministe bidirectionnel IDF + fuzzy est déjà servi par
handle(). Le « modèle » du schéma = ce moteur ; l'« entraînement » =
index IDF reconstruit à froid ; l'« ajustement » = suggestions
/apprends pour le Boss.
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

import knowledge_store
knowledge_store._CUSTOM_ROWS = []
import aya_pipeline
import aya_seed
import actions

print("── 1. Les 7 étapes existent et s'enchaînent ──")
steps = [aya_pipeline.collect_data, aya_pipeline.preprocess_data,
         aya_pipeline.convert_to_numerical, aya_pipeline.build_model,
         aya_pipeline.train_model, aya_pipeline.evaluate_and_adjust,
         aya_pipeline.deploy_model]
check("7 fonctions étapes définies (schéma du Boss)",
      all(callable(s) for s in steps), "")

raw = aya_pipeline.collect_data()                      # 1
check("1. collecte : les 36 Q/R du seed ramassées",
      len(raw) >= 36, len(raw))
pre = aya_pipeline.preprocess_data(raw)               # 2
check("2. prétraitement : tokens + variantes « | » découpées",
      all("tokens_per_variant" in e for e in pre)
      and any(len(e["variants"]) > 1 for e in pre), "")
num = aya_pipeline.convert_to_numerical(pre)           # 3
check("3. conversion numérique : vocab + IDF (poids par token)",
      len(num["vocab"]) > 50 and all(w >= 1.0 for w in num["idf"].values()), len(num["vocab"]))
model = aya_pipeline.build_model()                    # 4
check("4. modèle = moteur local bidirectionnel IDF (pas de TensorFlow)",
      model["engine"].startswith("local_search"), model["engine"])
train = aya_pipeline.train_model(model, num)           # 5
check("5. entraînement : index prêt, déterministe (1 passe, loss 0)",
      model["trained"] and train["loss"] == 0.0 and train["vocab_size"] == len(num["vocab"]), train)

print("── 2. Évaluation sur formulations clients réelles ──")
ev = aya_pipeline.evaluate_and_adjust(model)           # 6
check("jeu de validation : 20 formulations clientes",
      ev["total"] == 20, ev["total"])
check(f"précision ≥ 85% (reçu {ev['accuracy']:.0%})",
      ev["accuracy"] >= 0.85, ev["misses"])
check("pas de faux succès : chaque écart est un manque",
      all("question" in m and "attendu" in m for m in ev["misses"]), ev["misses"])
dep = aya_pipeline.deploy_model(model, ev)             # 7
check("7. déploiement : moteur intégré + rapport",
      dep["integrated"] and dep["accuracy"] == ev["accuracy"], dep)

print("── 3. Ajustement : un manque détecté devient une suggestion ──")
fake_validation = [("salut", "Aya"), ("kwejkwel zzz inconnu", "impossible")]
ev2 = aya_pipeline.evaluate_and_adjust(model, validation_data=fake_validation)
check("le manque est compté (1/2)", ev2["hits"] == 1, ev2)
check("suggestion /apprends générée pour l'ajustement",
      ev2["suggestions"] and all(s.startswith("/apprends ") for s in ev2["suggestions"]),
      ev2["suggestions"])
check("sous le seuil signalé (2/2 < 85%)", ev2["below_threshold"] is True, ev2["below_threshold"])

print("── 4. Pipeline complet (run_evaluation) ──")
report = aya_pipeline.run_evaluation("fr")
check("run_evaluation retourne train + eval + deploy",
      all(k in report for k in ("train", "eval", "deploy")), list(report))
check("seed assuré avant l'évaluation (idempotent)",
      len(knowledge_store._CUSTOM_ROWS) >= 36, len(knowledge_store._CUSTOM_ROWS))
check("précision pipeline complet ≥ 85%",
      report["eval"]["accuracy"] >= 0.85, report["eval"]["misses"])

print("── 5. Commande admin /evaluation ──")
check("/evaluation enregistrée dans ADMIN_COMMANDS",
      "/evaluation" in actions.ADMIN_COMMANDS, "")
sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append(text); return True
unanswered = []
with patch.object(actions, "run_evaluation", None, create=True), \
     patch("aya_pipeline.run_evaluation", return_value=report), \
     patch("memory_sheets.log_unanswered", side_effect=lambda q, l, c, n: unanswered.append(q)):
    r = actions._admin_evaluation(FakeBot(), 99999, "", "fr")
check("/evaluation exécuté sans crash", r is True, r)
check("rapport de précision envoyé à l'admin",
      sent and "Précision" in sent[0], sent[:1])
check("manques tracés dans Questions sans réponse (miroir)",
      len(unanswered) == len(report["eval"]["misses"]), unanswered)

# Anti-crash : pipeline qui lève → l'admin est prévenu, pas de crash
sent.clear()
with patch("aya_pipeline.run_evaluation", side_effect=RuntimeError("boom")):
    r = actions._admin_evaluation(FakeBot(), 99999, "", "fr")
check("pipeline HS → message d'erreur à l'admin, pas de crash",
      r is True and any("échoué" in str(t) for t in sent), sent)

print(f"\nTOTAL: {OK} OK / {KO} KO")
