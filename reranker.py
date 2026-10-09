"""reranker — CrossEncoder ms-marco + seuil de pertinence (Boss 09/10).

Problème (captures 07-09/10) : le moteur lexical peut classer une fiche
hors-sujet au-dessus de la bonne (égalité de mots-clés à 1.0). Le
reranker RELIT la paire (question client / réponse candidate) avec un
modèle entraîné pour ça, et le SEUIL rejette les réponses trop faibles :
règle d'or du bot — JAMAIS de réponse devinée.

Modèle : cross-encoder/ms-marco-MiniLM-L-6-v2 (~90 Mo, 100 % local,
aucun appel API externe, cohérent avec l'architecture offline).

Dégradation douce : si sentence-transformers n'est pas installé
(Railway sans les dépendances lourdes) ou si le modèle ne charge pas,
rerank() retourne None et le moteur lexical garde son comportement
inchangé — le bot ne tombe JAMAIS en panne à cause du reranker.

Env :
    RERANK_ENABLED     auto (défaut) | 1 | 0
    RERANK_THRESHOLD   score mini pour répondre, en logits (défaut
                        -3.0, calibré sur les fiches réelles + captures du 07-09/10)
    RERANK_TOP_K       nb de candidats reclassés (défaut 8)
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("komara.rerank")

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_reranker = None          # CrossEncoder chargé (ou None)
_load_attempted = False   # on ne tente le chargement qu'UNE fois


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def is_enabled() -> bool:
    flag = _env_flag("RERANK_ENABLED")
    if flag in {"0", "false", "off", "no", "non"}:
        return False
    if flag in {"1", "true", "on", "yes", "oui"}:
        return True
    return True  # auto : le reranker s'active si le modèle est dispo


def _load():
    """Chargement paresseux, UNE seule tentative par process."""
    global _reranker, _load_attempted
    if _load_attempted:
        return _reranker
    _load_attempted = True
    if not is_enabled():
        logger.info("[RERANK] désactivé par RERANK_ENABLED")
        return None
    t0 = time.time()
    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(_MODEL_NAME)
        logger.info("[RERANK] modèle chargé en %.1fs", time.time() - t0)
    except Exception as exc:  # pas installé / pas de réseau au boot
        logger.warning("[RERANK] indisponible (%s) — moteur lexical seul", exc)
        _reranker = None
    return _reranker


def threshold() -> float:
    """Seuil de pertinence : en dessous, on NE répond PAS par une fiche."""
    try:
        return float(_env_flag("RERANK_THRESHOLD", "-3.0"))
    except ValueError:
        return -3.0


def top_k() -> int:
    try:
        return int(_env_flag("RERANK_TOP_K", "8"))
    except ValueError:
        return 8


def rerank(question: str, candidates: list) -> list | None:
    """Relit et re-classe les candidats par pertinence réelle.

    candidates : [(score_lexical, answer, best_q), ...]
    Compare la question du client à la QUESTION ORIGINALE de chaque
    fiche (Boss 09/10 : pas la réponse — une réponse commerciale longue
    détourne le CrossEncoder ; la question candidate dit ce que la fiche
    couvre VRAIMENT).
    Retourne [(answer, best_q, rerank_score), ...] trié du meilleur au
    pire, ou None si le reranker n'est pas disponible (moteur lexical
    inchangé). Les scores ms-marco sont des logits (peuvent être < 0) :
    le seuil par défaut est calibré sur les vrais dialogues du bot.
    """
    model = _load()
    if model is None or not candidates:
        return None
    try:
        # q_original si disponible, sinon la réponse (repli)
        pairs = [[question, (c[2] if c[2] else c[1])[:512]] for c in candidates]
        # show_progress_bar=False : les barres sentence-transformers vont
        # sur stderr -> Railway les colore en rouge et pollue les logs
        scores = model.predict(pairs, show_progress_bar=False)
        ranked = sorted(zip(candidates, scores, strict=False),
                        key=lambda x: x[1], reverse=True)
        return [(c[1], c[2], s) for c, s in ranked]
    except Exception as exc:
        logger.warning("[RERANK] prédiction échouée (%s) — fallback lexical", exc)
        return None


def is_relevant(rerank_score) -> bool:
    """Au-dessus du seuil : on répond. En dessous : PAS de réponse devinée."""
    return rerank_score >= threshold()
