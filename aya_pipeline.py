# -*- coding: utf-8 -*-
"""aya_pipeline — Pipeline mémoire Aya en 7 étapes (Boss 05/10).

ADAPTATION DU SCHÉMA ML DU BOSS :
    collect_data → preprocess_data → convert_to_numerical → build_model
    → train_model → evaluate_and_adjust → deploy_model

...à l'architecture RÉELLE du bot (pivot zéro-donnée 02/10) :

    1. COLLECTE      : les Q/R vivent dans aya_seed (la marque du bot)
                      + knowledge_store (RAM apprise via /apprends) ;
                      la persistance est le Sheet « Komara Bot - Mémoire ».
    2. PRÉTRAITEMENT : normalize_text + tokenisation + stemming léger
                      (local_search._tokenize) — équivalent du
                      clean()/tokenize() du schéma Boss.
    3. CONVERSION    : vocabulaire token → index + pondération IDF
       NUMÉRIQUE      (les mots rares portent plus de sens que « bot »).
                      C'est notre « map_token_to_number ».
    4. MODÈLE        : le moteur de scoring bidirectionnel IDF + fuzzy
                      (local_search). Pas de TensorFlow : Railway tourne
                      sur CPU, le pivot zéro-donnée interdit un corpus
                      d'entraînement sur disque, et un modèle appris
                      casserait les 17 suites de régression.
    5. ENTRAÎNEMENT  : construction de l'index IDF — déterministe,
                      instantané, reconstruit à froid à chaque
                      redémarrage (aucune époque à sauvegarder).
    6. ÉVALUATION    : jeu de validation de formulations CLIENTES
       + AJUSTEMENT    réelles (fautes, français approximatif). Sous le
                      seuil → chaque échec devient une suggestion
                      concrète « /apprends ... » pour le Boss (c'est
                      l'ajustement de paramètres, version humain-dans-
                      la-boucle).
    7. DÉPLOIEMENT   : refresh des ressources + rapport. L'intégration
                      applicative existe déjà (handle() sert le moteur).

Usage admin : /evaluation → exécute tout le pipeline et rapporte la
précision + les échecs à enseigner.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any

logger = logging.getLogger("komara.aya_pipeline")

# Seuil de compréhension attendu sur le jeu de validation (Boss 05/10).
# 85% : assez haut pour garantir la qualité, assez bas pour tolérer les
# formulations exotiques d'un vrai client.
DEFAULT_THRESHOLD = 0.85


# ── Étape 1 : Collecte de données ─────────────────────────────────────────

def collect_data(source: str = "seed+ram") -> list[tuple[str, str]]:
    """Collecte les Q/R : seed Aya (la marque) + entrées apprises en RAM
    (/apprends). Le Google Sheet reste la persistance, pas la source du
    jour : au démarrage, ensure_seed() réhydrate déjà la RAM depuis
    le Sheet quand il est lié."""
    import aya_seed
    import knowledge_store

    rows: dict[str, str] = {}
    if "seed" in source or "ram" in source:
        for q, a in aya_seed.SEED_QR:
            rows.setdefault(q.strip().casefold(), a)
    if "ram" in source:
        for r in knowledge_store._CUSTOM_ROWS:
            q = str(r.get("question", "")).strip()
            if q:
                rows.setdefault(q.casefold(), str(r.get("answer", "")))
    return [(q, a) for q, a in rows.items() if a]


# ── Étape 2 : Prétraitement des données ───────────────────────────────────

def preprocess_data(raw_data: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Nettoyage + normalisation + tokenisation (variantes « | » incluses :
    chaque variante est prétraitée séparément — le moteur score chacune
    et prend la meilleure)."""
    from local_search import _tokenize

    processed = []
    for q, a in raw_data:
        variants = [v.strip() for v in q.split("|") if v.strip()] or [q]
        processed.append({
            "variants": variants,
            "tokens_per_variant": [_tokenize(v) for v in variants],
            "answer": a,
        })
    return processed


# ── Étape 3 : Conversion en nombres ───────────────────────────────────────

def convert_to_numerical(preprocessed: list[dict[str, Any]]) -> dict[str, Any]:
    """Vocabulaire token → index + pondération IDF. Équivalent local du
    « map_token_to_number » : chaque question devient un vecteur de
    tokens pondérés (un mot rare comme « modération » pèse plus qu'un
    mot générique comme « bot »)."""
    df: Counter = Counter()
    for entry in preprocessed:
        seen: set[str] = set()
        for toks in entry["tokens_per_variant"]:
            seen.update(toks)
        df.update(seen)

    vocab = {tok: idx for idx, tok in enumerate(sorted(df))}
    n_docs = max(len(preprocessed), 1)
    idf = {tok: math.log((n_docs + 1) / (count + 1)) + 1.0
           for tok, count in df.items()}
    return {"vocab": vocab, "idf": idf, "doc_count": n_docs}


# ── Étape 4 : Construction du modèle ──────────────────────────────────────

def build_model() -> dict[str, Any]:
    """Le « modèle » d'Aya : le moteur de scoring bidirectionnel IDF +
    fuzzy matching (local_search), servi par rag_bot. Pas de
    TensorFlow (voir docstring du module)."""
    import rag_bot

    return {"engine": "local_search:bidirectional+idf+fuzzy",
            "scorer": rag_bot.trouver_meilleure_reponse_multilingue,
            "trained": False}


# ── Étape 5 : Entraînement du modèle ──────────────────────────────────────

def train_model(model: dict[str, Any],
                numerical: dict[str, Any]) -> dict[str, Any]:
    """« Entraînement » : l'index IDF est construit (étape 3) et le moteur
    est armé sur les ressources fraîches. Déterministe : zéro époque, la
    base est reconstruite à froid à chaque démarrage — c'est ça, notre
    entraînement continu : /apprends ajoute une ligne, l'index se
    reconstruit, la compréhension s'améliore immédiatement."""
    import aya_seed
    import knowledge_store

    knowledge_store.refresh_resources("fr")
    model["trained"] = True
    return {
        "epoch": 1,
        "loss": 0.0,  # modèle déterministe : pas de fonction de perte
        "vocab_size": len(numerical["vocab"]),
        "docs": numerical["doc_count"],
        "seed_loaded": len(aya_seed.SEED_QR),
    }


# ── Étape 6 : Évaluation et ajustement ────────────────────────────────────

# Jeu de validation : formulations CLIENTES réelles (avec fautes et
# français approximatif) → mot-clé OBLIGATOIRE dans la réponse servie.
# Chacune vérifie une entrée de la connaissance (seed + conversations
# commerciales Boss 04/10).
VALIDATION_SET: list[tuple[str, str]] = [
    ("salut", "Aya"),
    ("qui es-tu ?", "Aya"),
    ("c'est combien le logo", "300"),
    ("quel est le prix d'une affiche", "300"),
    ("c'est quoi un chatbot", "logiciel"),
    ("c'est quoi une automatisation", "machine"),
    ("je veux en savoir plus sur vos agents IA", "agents IA personnalisés"),
    ("comment ça marche un chatbot pour mon site web ?", "spécifications"),
    ("quels sont vos tarifs pour un chatbot ?", "devis personnalisé"),
    ("parlez-moi de la modération des réseaux sociaux", "Facebook et Instagram"),
    ("j'ai trop de messages sur ma page je n'arrive pas à gérer", "modération prend"),
    ("comment vous procédez pour la modération ?", "lignes directrices"),
    ("j'ai des questions sur le devis du chatbot", "questions fréquentes"),
    ("est-ce possible d'ajouter une collecte d'emails au bot ?", "collecte"),
    ("combien de temps pour mettre en place le chatbot ?", "2 à 4 semaines"),
    ("est-ce que vous offrez un support après la mise en place ?", "support continu"),
    ("je valide le devis !", "finaliser"),
    ("vous livrez à Kindia ?", "Guinée"),
    ("c'est quoi le délai pour un logo", "2-3 jours"),
    ("merci beaucoup", "plaisir"),
]


def evaluate_and_adjust(model: dict[str, Any],
                        validation_data: list[tuple[str, str]] | None = None,
                        threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Évalue la compréhension sur les formulations réelles ; sous le
    seuil → AJUSTEMENT version Boss-dans-la-boucle : chaque échec
    devient une suggestion « /apprends <question> || <réponse> »."""
    validation = validation_data if validation_data is not None else VALIDATION_SET
    scorer = model["scorer"]

    hits = 0
    misses: list[dict[str, str]] = []
    for probe, expected in validation:
        answer = scorer(probe, "fr") or ""
        if expected.lower() in answer.lower():
            hits += 1
        else:
            misses.append({"question": probe,
                           "attendu": expected,
                           "recu": answer[:120] or "(aucune réponse)"})

    total = max(len(validation), 1)
    accuracy = hits / total
    below = accuracy < threshold

    # Ajustement : suggestions concrètes pour l'admin.
    suggestions = [f"/apprends {m['question']} || ta réponse"
                   for m in misses]

    return {
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "threshold": threshold,
        "below_threshold": below,
        "misses": misses,
        "suggestions": suggestions,
    }


# ── Étape 7 : Intégration et déploiement ───────────────────────────────────

def deploy_model(model: dict[str, Any], eval_report: dict[str, Any]) -> dict[str, Any]:
    """Le moteur est déjà intégré dans handle() — le « déploiement »
    consiste à garantir que les ressources sont fraîches et à rendre
    compte. Pas de rechargement à chaud : Railway redéploie le code,
    le Sheet réhydrate la mémoire."""
    return {
        "integrated": True,
        "engine": model["engine"],
        "accuracy": eval_report["accuracy"],
        "deploy_note": "moteur servi par handle() — ressources rafraîchies",
    }


# ── Pipeline complet (appelé par /evaluation) ─────────────────────────────

def run_evaluation(lang: str = "fr",
                   threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Exécute les 7 étapes bout en bout. Idempotent : le seed est
    assuré (RAM + Sheet si lié) avant l'évaluation."""
    import aya_seed
    import knowledge_store

    aya_seed.ensure_seed(lang)          # 1. collecte (assure le seed)
    raw = collect_data()                # 1. collecte
    pre = preprocess_data(raw)          # 2. prétraitement
    numerical = convert_to_numerical(pre)  # 3. conversion numérique
    model = build_model()               # 4. modèle
    train_report = train_model(model, numerical)  # 5. entraînement
    eval_report = evaluate_and_adjust(model, threshold=threshold)  # 6.
    deploy_report = deploy_model(model, eval_report)  # 7.

    logger.info("Pipeline Aya : précision %.0f%% (%d/%d) — vocab %d",
                eval_report["accuracy"] * 100, eval_report["hits"],
                eval_report["total"], train_report["vocab_size"])
    return {"train": train_report, "eval": eval_report,
            "deploy": deploy_report}
