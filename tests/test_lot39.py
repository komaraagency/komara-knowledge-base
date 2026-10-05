# -*- coding: utf-8 -*-
"""Lot 39 (v2) — Boss 05/10 : pipeline mémoire Aya en 6 étapes, restructuré
sur le schéma « Le trajet complet » (L'information IA / ChatGPT) :
DONNÉES → TOKENS → NOMBRES → TRANSFORMEUR → PRÉ-ENTRAÎNEMENT →
POST-ENTRAÎNEMENT, + commande admin /evaluation.

Adaptation RÉELLE (pas de réseau de neurones) : Railway tourne sur CPU,
le pivot zéro-donnée interdit un corpus d'entraînement sur disque, et
le moteur déterministe bidirectionnel IDF + fuzzy est déjà servi par
handle(). Le « transformeur » du schéma = ce moteur ; le
« pré-entraînement » = index IDF construit sur toute la base ; le
« post-entraînement » = évaluation sur feedback humain réel (RLHF
version Boss-dans-la-boucle) + déploiement.
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

print("── 1. Les 6 étapes existent (schéma DONNÉES→POST-ENTRAÎNEMENT) ──")
steps = [aya_pipeline.step1_donnees, aya_pipeline.step2_tokens,
         aya_pipeline.step3_nombres, aya_pipeline.step4_transformeur,
         aya_pipeline.step5_pre_entrainement, aya_pipeline.step6_post_entrainement]
check("6 fonctions étapes définies", all(callable(s) for s in steps), "")

raw = aya_pipeline.step1_donnees()                      # 01 DONNÉES
check("01 DONNÉES : les 36 Q/R du seed ramassées", len(raw) >= 36, len(raw))
tok = aya_pipeline.step2_tokens(raw)                     # 02 TOKENS
check("02 TOKENS : tokens + variantes « | » découpées",
      all("tokens_per_variant" in e for e in tok)
      and any(len(e["variants"]) > 1 for e in tok), "")
num = aya_pipeline.step3_nombres(tok)                    # 03 NOMBRES
check("03 NOMBRES : vocab + IDF (poids par token)",
      len(num["vocab"]) > 50 and all(w >= 1.0 for w in num["idf"].values()), len(num["vocab"]))
model = aya_pipeline.step4_transformeur()                # 04 TRANSFORMEUR
check("04 TRANSFORMEUR = moteur local bidirectionnel IDF (pas de réseau de neurones)",
      model["engine"].startswith("local_search"), model["engine"])
pretrain = aya_pipeline.step5_pre_entrainement(model, num)  # 05 PRÉ-ENTR.
check("05 PRÉ-ENTRAÎNEMENT : index prêt, déterministe (1 passe, loss 0)",
      model["trained"] and pretrain["loss"] == 0.0
      and pretrain["vocab_size"] == len(num["vocab"]), pretrain)

print("── 2. 06 POST-ENTRAÎNEMENT : évaluation + ajustement + déploiement ──")
post = aya_pipeline.step6_post_entrainement(model)       # 06 POST-ENTR.
check("jeu de validation : 20 formulations clientes", post["total"] == 20, post["total"])
check(f"précision ≥ 85% (reçu {post['accuracy']:.0%})",
      post["accuracy"] >= 0.85, post["misses"])
check("pas de faux succès : chaque écart est un manque",
      all("question" in m and "attendu" in m for m in post["misses"]), post["misses"])
check("déploiement intégré au rapport post-entraînement",
      post["integrated"] and post["accuracy"] == post["accuracy"], post)

print("── 3. Ajustement : un manque détecté devient une suggestion ──")
fake_validation = [("salut", "Aya"), ("kwejkwel zzz inconnu", "impossible")]
post2 = aya_pipeline.step6_post_entrainement(model, validation_data=fake_validation)
check("le manque est compté (1/2)", post2["hits"] == 1, post2)
check("suggestion /apprends générée pour l'ajustement",
      post2["suggestions"] and all(s.startswith("/apprends ") for s in post2["suggestions"]),
      post2["suggestions"])
check("sous le seuil signalé (1/2 < 85%)", post2["below_threshold"] is True, post2["below_threshold"])

print("── 4. Pipeline complet (run_evaluation) ──")
report = aya_pipeline.run_evaluation("fr")
check("run_evaluation retourne pretrain + posttrain",
      all(k in report for k in ("pretrain", "posttrain")), list(report))
check("seed assuré avant l'évaluation (idempotent)",
      len(knowledge_store._CUSTOM_ROWS) >= 36, len(knowledge_store._CUSTOM_ROWS))
check("précision pipeline complet ≥ 85%",
      report["posttrain"]["accuracy"] >= 0.85, report["posttrain"]["misses"])

print("── 5. Compatibilité : anciens noms (v1) toujours utilisables ──")
check("collect_data == step1_donnees", aya_pipeline.collect_data is aya_pipeline.step1_donnees, "")
check("preprocess_data == step2_tokens", aya_pipeline.preprocess_data is aya_pipeline.step2_tokens, "")
check("convert_to_numerical == step3_nombres", aya_pipeline.convert_to_numerical is aya_pipeline.step3_nombres, "")
check("build_model == step4_transformeur", aya_pipeline.build_model is aya_pipeline.step4_transformeur, "")
old_eval = aya_pipeline.evaluate_and_adjust(model)
check("evaluate_and_adjust (alias v1) fonctionne toujours",
      "accuracy" in old_eval and old_eval["total"] == 20, old_eval.get("accuracy"))
old_deploy = aya_pipeline.deploy_model(model, old_eval)
check("deploy_model (alias v1) fonctionne toujours",
      old_deploy["integrated"] is True, old_deploy)

print("── 6. Commande admin /evaluation ──")
check("/evaluation enregistrée dans ADMIN_COMMANDS",
      "/evaluation" in actions.ADMIN_COMMANDS, "")
sent = []
class FakeBot:
    def send_message(self, cid, text=None, *a, **k):
        sent.append(text); return True
unanswered = []
with patch("aya_pipeline.run_evaluation", return_value=report), \
     patch("memory_sheets.log_unanswered", side_effect=lambda q, l, c, n: unanswered.append(q)):
    r = actions._admin_evaluation(FakeBot(), 99999, "", "fr")
check("/evaluation exécuté sans crash", r is True, r)
check("rapport de précision envoyé à l'admin",
      sent and "Précision" in sent[0], sent[:1])
check("manques tracés dans Questions sans réponse (miroir)",
      len(unanswered) == len(report["posttrain"]["misses"]), unanswered)

# Anti-crash : pipeline qui lève → l'admin est prévenu, pas de crash
sent.clear()
with patch("aya_pipeline.run_evaluation", side_effect=RuntimeError("boom")):
    r = actions._admin_evaluation(FakeBot(), 99999, "", "fr")
check("pipeline HS → message d'erreur à l'admin, pas de crash",
      r is True and any("échoué" in str(t) for t in sent), sent)

print(f"\nTOTAL: {OK} OK / {KO} KO")
