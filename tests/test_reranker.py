# ──────────────────────────────────────────────────────────────────────────
# test_reranker — CrossEncoder ms-marco + seuil de pertinence (Boss 09/10)
#
# Objectif : le moteur lexical peut classer une fiche hors-sujet devant la
# bonne (égalité de mots-clés). Le reranker RELIT (message, réponse) et :
#   1. re-classe les top-K candidats par pertinence réelle ;
#   2. rejette TOUT sous le seuil -> jamais de réponse devinée ;
#   3. ne tire (anti-répétition) qu'entre paraphrases.
#   4. si le modèle n'est pas installé : comportement lexical inchangé.
#
# Le vrai modèle n'est PAS requis ici : on simule CrossEncoder pour tester
# la logique. La calibration réelle du seuil est faite à part.
# ──────────────────────────────────────────────────────────────────────────
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")

import reranker

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

print("── 1. Dégradation douce (modèle absent) ──")
saved_load, saved_attempted = reranker._load, reranker._load_attempted
reranker._load_attempted = True
reranker._reranker = None
check("sans modèle -> rerank None (voie lexicale)", reranker.rerank("q", [("1", "a", "q")]) is None)
check("sans modèle -> _load None", reranker._load() is None)
reranker._load, reranker._load_attempted = saved_load, saved_attempted

print("── 2. Rerank : re-classement par pertinence ──")
class FakeCE:
    def __init__(self): self.calls = []
    def predict(self, pairs):
        self.calls.append(pairs)
        # la « bonne » QUESTION de fiche parle de bot quand le client en parle
        return [3.0 if ("bot" in q.lower()) else -3.0 for _c, q in pairs]
reranker._reranker = FakeCE()
reranker._load_attempted = True
cands = [(0.9, "Logo pro moderne à partir de 50€", "prix logo"),
         (1.0, "Notre portfolio de réalisations", "portfolio"),
         (0.5, "Un bot coûte entre 50€ et 150€ selon la formule", "prix bot")]
out = reranker.rerank("Combien coûte un bot ?", cands)
check("retourne 3 résultats", out is not None and len(out) == 3, out)
check("le candidat « prix » passe en 1er", out[0][2] == 3.0, out[0])
check("le candidat non pertinent est dernier", out[-1][2] == -3.0, out[-1])
check("paires = (question client, QUESTION de la fiche)",
      reranker._reranker.calls[0][0][0] == "Combien coûte un bot ?"
      and reranker._reranker.calls[0][0][1] in {"prix logo", "portfolio", "prix bot"},
      reranker._reranker.calls[0][0])
reranker._reranker = None

print("── 3. Seuil : jamais de réponse devinée ──")
os.environ["RERANK_THRESHOLD"] = "0.15"
check("seuil env lu", reranker.threshold() == 0.15, reranker.threshold())
check("score 0.3 -> pertinent", reranker.is_relevant(0.3))
check("score 0.1 -> NON pertinent", not reranker.is_relevant(0.1))
os.environ["RERANK_THRESHOLD"] = "1.5"
check("seuil modifiable à chaud", reranker.threshold() == 1.5)
check("score 1.0 -> rejeté au-dessus du nouveau seuil", not reranker.is_relevant(1.0))
del os.environ["RERANK_THRESHOLD"]

print("── 4. top_k configurable ──")
os.environ["RERANK_TOP_K"] = "3"
check("top_k=3", reranker.top_k() == 3)
del os.environ["RERANK_TOP_K"]

print("── 5. Intégration trouver_meilleure_reponse ──")
# Score lexical égal parfait pour 2 fiches différentes -> le reranker doit
# départager, et sous le seuil il doit retourner None.
import local_search as LS

kb = [
    {"questions": ["prix bot", "combien coûte un bot"], "answer": "Un bot coûte entre 50€ et 150€ selon la formule.",
     "keywords": ["prix", "bot", "coût"]},
    {"questions": ["tu vends quoi", "que vends tu"], "answer": "Je vends pour toi même quand tu dors 😴",
     "keywords": ["vends", "quoi"]},
]
faq, dial = [], []

# 5a. sans reranker actif : voie lexicale inchangée
reranker._reranker = None; reranker._load_attempted = True
r0 = LS.trouver_meilleure_reponse("combien coûte un bot ?", kb, faq, dial)
check("voie lexicale inchangée (réponse prix)", r0 is not None and "50€" in r0, str(r0)[:60])

# 5b. reranker qui VALIDE la fiche prix
class ApproveCE:
    def predict(self, pairs): return [5.0 for _ in pairs]
reranker._reranker = ApproveCE()
r1 = LS.trouver_meilleure_reponse("combien coûte un bot ?", kb, faq, dial)
check("rerank OK -> réponse prix", r1 is not None and "50€" in r1, str(r1)[:60])

# 5c. reranker qui REJETTE (scores faibles) -> None, pas de réponse devinée
class RejectCE:
    def predict(self, pairs): return [-5.0 for _ in pairs]
reranker._reranker = RejectCE()
r2 = LS.trouver_meilleure_reponse("combien coûte un bot ?", kb, faq, dial)
check("sous le seuil -> None (jamais deviné)", r2 is None, str(r2)[:60])

# 5d. hors-sujet complet avec validation : le reranker ne fait que
# reclasser ce que le moteur lexical a RETROUVÉ ; s'il valide tout,
# la réponse reste celle du meilleur lexical (pas d'invention)
reranker._reranker = ApproveCE()
r3 = LS.trouver_meilleure_reponse("kangourou violet antarctique", kb, faq, dial)
check("aucune fiche retrouvée -> None", r3 is None, str(r3)[:60])

reranker._reranker = None
print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
