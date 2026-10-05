# -*- coding: utf-8 -*-
"""aya_pipeline — Pipeline mémoire Aya en 6 étapes (Boss 05/10, v2).

Restructuré sur le schéma « Le trajet complet » (L'information IA,
schéma ChatGPT) : DONNÉES → TOKENS → NOMBRES → TRANSFORMEUR →
PRÉ-ENTRAÎNEMENT → POST-ENTRAÎNEMENT.

ADAPTATION À L'ARCHITECTURE RÉELLE du bot (pivot zéro-donnée 02/10) :

    01 DONNÉES          : les Q/R vivent dans aya_seed (la marque du
                          bot) + knowledge_store (RAM apprise via
                          /apprends). Le Sheet « Komara Bot - Mémoire »
                          est la persistance, réhydratée au démarrage.
    02 TOKENS           : normalize_text + tokenisation + stemming
                          léger (local_search._tokenize) + variantes
                          « q1 | q2 | q3 » découpées séparément.
    03 NOMBRES          : vocabulaire token → index + pondération IDF
                          (un mot rare comme « modération » porte plus
                          de sens qu'un mot générique comme « bot »).
    04 TRANSFORMEUR     : chez ChatGPT, un réseau de neurones profond
                          qui apprend des représentations par attention.
                          Chez Aya : le moteur de scoring bidirectionnel
                          IDF + fuzzy matching (local_search), servi par
                          rag_bot. Pas de réseau de neurones : Railway
                          tourne sur CPU, le pivot zéro-donnée interdit
                          un corpus d'entraînement sur disque, et un
                          modèle appris casserait les 18 suites de
                          régression. C'est notre « transformeur
                          maison » — déterministe, explicable, instantané.
    05 PRÉ-ENTRAÎNEMENT : chez ChatGPT, apprentissage général à partir
                          d'un immense corpus. Chez Aya : construction
                          de l'index IDF sur TOUTE la base (seed + RAM
                          apprise) — la connaissance générale de la
                          marque, reconstruite à froid à chaque
                          redémarrage (zéro époque, zéro perte à
                          minimiser : c'est déterministe).
    06 POST-ENTRAÎNEMENT : chez ChatGPT, l'alignement fin sur feedback
                          humain (RLHF) après le pré-entraînement
                          général. Chez Aya : exactement ce rôle est
                          joué par l'évaluation sur des formulations
                          CLIENTES réelles + la correction humaine du
                          Boss via /apprends quand un écart est détecté
                          — puis le déploiement (le moteur est déjà
                          intégré dans handle(), Railway redéploie le
                          code).

Usage admin : /evaluation → exécute le pipeline complet et rapporte la
précision + les écarts à corriger.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any

logger = logging.getLogger("komara.aya_pipeline")

# Seuil de compréhension attendu sur le jeu de validation (post-
# entraînement). 85% : assez haut pour la qualité, assez bas pour
# tolérer les formulations exotiques d'un vrai client.
DEFAULT_THRESHOLD = 0.85


# ── 01 DONNÉES ─────────────────────────────────────────────────────────

def step1_donnees(source: str = "seed+ram") -> list[tuple[str, str]]:
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


# ── 02 TOKENS ──────────────────────────────────────────────────────────

def step2_tokens(raw_data: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Nettoyage + normalisation + tokenisation (variantes « | » incluses :
    chaque variante est tokenisée séparément — le moteur score chacune
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


# ── 03 NOMBRES ─────────────────────────────────────────────────────────

def step3_nombres(tokenized: list[dict[str, Any]]) -> dict[str, Any]:
    """Vocabulaire token → index + pondération IDF. Chaque question
    devient un vecteur de tokens pondérés (un mot rare comme
    « modération » pèse plus qu'un mot générique comme « bot »)."""
    df: Counter = Counter()
    for entry in tokenized:
        seen: set[str] = set()
        for toks in entry["tokens_per_variant"]:
            seen.update(toks)
        df.update(seen)

    vocab = {tok: idx for idx, tok in enumerate(sorted(df))}
    n_docs = max(len(tokenized), 1)
    idf = {tok: math.log((n_docs + 1) / (count + 1)) + 1.0
           for tok, count in df.items()}
    return {"vocab": vocab, "idf": idf, "doc_count": n_docs}


# ── 04 TRANSFORMEUR ────────────────────────────────────────────────────

def step4_transformeur() -> dict[str, Any]:
    """Le « transformeur » d'Aya : pas de réseau de neurones (voir
    docstring du module) mais le moteur de scoring bidirectionnel
    IDF + fuzzy matching (local_search), servi par rag_bot."""
    import rag_bot

    return {"engine": "local_search:bidirectional+idf+fuzzy",
            "scorer": rag_bot.trouver_meilleure_reponse_multilingue,
            "trained": False}


# ── 05 PRÉ-ENTRAÎNEMENT ────────────────────────────────────────────────

def step5_pre_entrainement(model: dict[str, Any],
                           numerical: dict[str, Any]) -> dict[str, Any]:
    """Apprentissage général : l'index IDF est construit sur TOUTE la
    base (étape 3) et le moteur est armé sur les ressources fraîches.
    Déterministe : zéro époque, zéro perte à minimiser — reconstruit à
    froid à chaque démarrage."""
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


# ── 06 POST-ENTRAÎNEMENT ───────────────────────────────────────────────

# Jeu de validation : formulations CLIENTES réelles (avec fautes et
# français approximatif) → mot-clé OBLIGATOIRE dans la réponse servie.
# C'est l'équivalent du feedback humain (RLHF) : chaque entrée vérifie
# un point précis de la connaissance (seed + conversations commerciales
# Boss 04/10).
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


def step6_post_entrainement(model: dict[str, Any],
                            validation_data: list[tuple[str, str]] | None = None,
                            threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Alignement fin sur feedback humain (notre RLHF) : évalue la
    compréhension sur les formulations réelles ; sous le seuil → chaque
    écart devient une suggestion « /apprends <question> || <réponse> »
    pour le Boss. Puis déploiement : le moteur est déjà intégré dans
    handle(), on rapporte juste l'état."""
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
    suggestions = [f"/apprends {m['question']} || ta réponse" for m in misses]

    return {
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "threshold": threshold,
        "below_threshold": below,
        "misses": misses,
        "suggestions": suggestions,
        "integrated": True,
        "engine": model["engine"],
        "deploy_note": "moteur servi par handle() — ressources rafraîchies",
    }


# ── Pipeline complet (appelé par /evaluation) ──────────────────────────

def run_evaluation(lang: str = "fr",
                   threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Exécute les 6 étapes bout en bout. Idempotent : le seed est
    assuré (RAM + Sheet si lié) avant l'évaluation."""
    import aya_seed

    aya_seed.ensure_seed(lang)                       # 01 (assure le seed)
    raw = step1_donnees()                            # 01 DONNÉES
    tokenized = step2_tokens(raw)                     # 02 TOKENS
    numerical = step3_nombres(tokenized)              # 03 NOMBRES
    model = step4_transformeur()                      # 04 TRANSFORMEUR
    pretrain_report = step5_pre_entrainement(model, numerical)  # 05
    post_report = step6_post_entrainement(model, threshold=threshold)  # 06

    logger.info("Pipeline Aya : précision %.0f%% (%d/%d) — vocab %d",
                post_report["accuracy"] * 100, post_report["hits"],
                post_report["total"], pretrain_report["vocab_size"])
    return {"pretrain": pretrain_report, "posttrain": post_report}


# ── Compatibilité : anciens noms (v1, Boss 05/10 matin) ────────────────
# Conservés pour ne pas casser du code externe qui les appellerait
# encore ; les tests/commandes actuels utilisent les noms step*.
collect_data = step1_donnees
preprocess_data = step2_tokens
convert_to_numerical = step3_nombres
build_model = step4_transformeur


def train_model(model, numerical):
    return step5_pre_entrainement(model, numerical)


def evaluate_and_adjust(model, validation_data=None, threshold=DEFAULT_THRESHOLD):
    return step6_post_entrainement(model, validation_data, threshold)


def deploy_model(model, eval_report):
    return {"integrated": True, "engine": model["engine"],
            "accuracy": eval_report["accuracy"],
            "deploy_note": "moteur servi par handle() — ressources rafraîchies"}
