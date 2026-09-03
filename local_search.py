"""Moteur de recherche locale avec compréhension sémantique (100% local, zéro API externe).

Améliorations vs ancienne version:
1. Score BIDIRECTIONNEL: couverture du keyword ET couverture du message
   → "je souhaite créer un bot" (5 mots) ne matche plus "bot" (1 mot) à 100%
   → le score baisse car seulement 1/5 du message correspond au keyword
2. Pondération IDF: "devis" (rare, porteur de sens) > "bot" (fréquent, générique)
   → "quel est le devis pour un bot whatsapp" matche les entrées "prix/devis"
     plutôt que l'entrée générique "bot"
3. Détection d'intention: prix, création, info, objection, setup, comparaison...
   → booste les entrées de même intention, pénalise les autres
4. Stop words filtrés: "je", "le", "la", "pour", "que"... ne participent pas au score
"""

from typing import Any, List, Tuple
import re
import math
from collections import Counter
from normalize_text import normalize_text


def _stem(word: str) -> str:
    """Stemming léger pour le français : retire les pluriels courants."""
    if len(word) <= 3:
        return word
    for suffix in ('aux', 'eaux'):
        if word.endswith(suffix):
            return word[:-len(suffix)] + 'al'
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def _tokenize(text: str) -> set[str]:
    """Extrait et normalise les mots d'un texte (accents + pluriels)."""
    text = normalize_text(text)
    raw = set(re.findall(r'[a-z0-9]+', text.lower()))
    return {_stem(w) for w in raw if len(w) >= 2}


# Stop words: très fréquents, ne portent pas de sens — exclus du scoring
STOP_WORDS = {
    # Français
    'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'et', 'est',
    'sont', 'je', 'tu', 'il', 'nous', 'vous', 'ils', 'mon', 'ton',
    'son', 'notre', 'votre', 'leur', 'ce', 'cette', 'ces', 'que',
    'qui', 'quoi', 'dont', 'pour', 'par', 'mais', 'avec', 'sans',
    'sur', 'sous', 'dans', 'au', 'aux', 'en', 'ne', 'pas', 'plus',
    'ou', 'donc', 'car', 'si', 'comme', 'aussi', 'tres', 'tout',
    'tous', 'toute', 'toutes', 'mes', 'tes', 'ses', 'etre',
    # Anglais
    'the', 'a', 'an', 'is', 'are', 'am', 'be', 'been', 'was', 'were',
    'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'his',
    'her', 'our', 'their', 'this', 'that', 'these', 'those',
    'do', 'does', 'did', 'can', 'could', 'would', 'should',
    'to', 'of', 'in', 'on', 'at', 'by', 'for', 'from',
    'and', 'or', 'but', 'not', 'no', 'yes', 'so', 'if', 'as',
    # Espagnol
    'el', 'los', 'las', 'una', 'unas', 'y', 'es', 'son', 'mi', 'tu',
    'su', 'que', 'con', 'sin', 'para', 'por', 'pero',
}

# Patterns d'intention: (nom, mots-clés qui signalent cette intention)
# Sera stemmé au chargement du module
_INTENT_PATTERNS_RAW: dict[str, set[str]] = {
    "pricing": {
        "devis", "prix", "tarif", "cout", "combien", "couter", "payer",
        "abonnement", "mensuel", "fcfa", "dollar", "euro", "cher",
        "cost", "price", "pricing", "much", "plan", "month",
        "precio", "costo", "cuanto",
    },
    "creation": {
        "creer", "creez", "developper", "construire", "souhaite", "voudrais",
        "aimerais", "besoin", "nouveau", "faire", "avoir",
        "create", "build", "make", "want", "need", "new",
        "crear", "construir", "hacer",
    },
    "info": {
        "presentation", "agence", "agency", "presentation",
        "about", "what", "qu", "c",
    },
    "objection": {
        "risque", "robot", "remplacer", "securite", "mal", "peur", "erreur",
        "wrong", "replace", "risk", "safe", "error", "problem",
        "riesgo", "seguro", "error",
    },
    "setup": {
        "demarrer", "commencer", "etapes", "installer", "installation",
        "lancer", "setup", "start", "begin", "launch",
        "iniciar", "comenzar",
    },
    "comparison": {
        "difference", "comparaison", "mieux", "vs", "entre",
        "between", "better",
        "diferencia",
    },
    "availability": {
        "nuit", "weekend", "24", "disponibilite", "ferie", "jour",
        "night", "hours", "available",
        "noche",
    },
}

# Stemmer les patterns d'intention une fois au chargement
INTENT_PATTERNS: dict[str, set[str]] = {}
for _intent, _keywords in _INTENT_PATTERNS_RAW.items():
    _stemmed = set()
    for _kw in _keywords:
        _stemmed.update(_tokenize(_kw))
    INTENT_PATTERNS[_intent] = _stemmed
del _INTENT_PATTERNS_RAW


def _detect_intent(tokens: set[str]) -> str | None:
    """Détecte l'intention à partir des tokens du message."""
    best_intent = None
    best_score = 0
    for intent, keywords in INTENT_PATTERNS.items():
        matched = tokens & keywords
        score = len(matched)
        if score > best_score:
            best_score = score
            best_intent = intent
    return best_intent if best_score > 0 else None


def _get_questions(item: dict) -> List[str]:
    """Extrait les questions d'un item KB — gère 'questions' (liste) ou 'question' (string)."""
    if 'questions' in item:
        qs = item['questions']
        if isinstance(qs, list):
            return [str(q) for q in qs if q]
        return [str(qs)]
    if 'question' in item:
        return [str(item['question'])]
    return []


def _compute_idf(all_questions: list[list[str]]) -> dict[str, float]:
    """Calcule l'IDF de chaque token à partir de toutes les questions.

    IDF élevé = token rare (porteur de sens, ex: "devis", "gdpr")
    IDF faible = token fréquent (générique, ex: "bot", "komara")
    """
    doc_freq: Counter = Counter()

    for questions in all_questions:
        tokens = set()
        for q in questions:
            tokens |= _tokenize(q)
        for t in tokens:
            doc_freq[t] += 1

    total_docs = len(all_questions) or 1
    idf: dict[str, float] = {}
    for token, df in doc_freq.items():
        # IDF lissé: log((N+1)/(df+1)) + 1
        idf[token] = math.log((total_docs + 1) / (df + 1)) + 1.0

    return idf


def _score_bidirectional(
    msg_tokens: set[str],
    keyword: str,
    idf: dict[str, float],
    msg_intent: str | None,
    kw_intent: str | None,
) -> float:
    """Score sémantique bidirectionnel avec IDF + boost d'intention.

    Ancien score (unidirectionnel): intersection / len(keywords)
      → "je souhaite créer un bot" vs "bot" = 1/1 = 1.0 ❌ (faux positif)

    Nouveau score (bidirectionnel):
      1. keyword_coverage = mots du keyword trouvés dans le message (pondéré IDF)
      2. message_coverage = mots du message trouvés dans le keyword (pondéré IDF, sans stop words)
      3. score = moyenne harmonique des deux
      4. boost x1.3 si même intention, pénalité x0.7 si intention différente

      → "je souhaite créer un bot" vs "bot":
        keyword_coverage = 1.0, message_coverage ≈ 0.2
        harmonic mean ≈ 0.33, avec pénalité d'intention ≈ 0.23 ❌ (correct!)
    """
    kw_tokens = _tokenize(keyword)
    if not kw_tokens or not msg_tokens:
        return 0.0

    intersection = msg_tokens & kw_tokens
    if not intersection:
        return 0.0

    # 1. Couverture du keyword (IDF-pondérée)
    kw_total = sum(idf.get(t, 1.0) for t in kw_tokens)
    kw_matched = sum(idf.get(t, 1.0) for t in intersection)
    keyword_coverage = kw_matched / kw_total if kw_total > 0 else 0.0

    # 2. Couverture du message (IDF-pondérée, sans stop words)
    msg_meaningful = {t for t in msg_tokens if t not in STOP_WORDS}
    if not msg_meaningful:
        msg_meaningful = msg_tokens  # fallback si tout est stop word

    msg_total = sum(idf.get(t, 1.0) for t in msg_meaningful)
    msg_matched = sum(idf.get(t, 1.0) for t in intersection if t not in STOP_WORDS)
    message_coverage = msg_matched / msg_total if msg_total > 0 else 1.0

    # 3. Moyenne harmonique des deux couvertures
    if keyword_coverage <= 0 or message_coverage <= 0:
        base_score = 0.0
    else:
        base_score = 2 * keyword_coverage * message_coverage / (keyword_coverage + message_coverage)

    # 4. Boost/pénalité d'intention
    if msg_intent and kw_intent:
        if msg_intent == kw_intent:
            base_score *= 1.3  # même intention → boost
        else:
            base_score *= 0.7  # intention différente → pénalité

    return base_score


def trouver_meilleure_reponse(
    message: str,
    knowledge_base: List[dict[str, Any]],
    local_faq: List[dict[str, str]],
    local_dialogues: List[dict[str, str]] = None
) -> str | None:
    """
    Retourne la meilleure réponse avec scoring sémantique bidirectionnel.

    Améliorations vs ancienne version:
    - Score bidirectionnel (keyword coverage × message coverage)
    - Pondération IDF (mots rares > mots fréquents)
    - Détection d'intention (prix, création, info, objection...)
    - Stop words filtrés du scoring
    """
    if local_dialogues is None:
        local_dialogues = []

    if not message:
        return None

    msg_tokens = _tokenize(message)
    if not msg_tokens:
        return None

    msg_intent = _detect_intent(msg_tokens)

    # Collecter toutes les questions pour calculer l'IDF
    all_questions: list[list[str]] = []

    # Préparer les entrées KB avec leurs intentions
    kb_entries: list[tuple[list[str], str, str | None]] = []
    for item in knowledge_base:
        questions = _get_questions(item)
        all_questions.append(questions)
        combined = ' '.join(questions)
        kw_intent = _detect_intent(_tokenize(combined))
        kb_entries.append((questions, item.get("answer", ""), kw_intent))

    # FAQ
    faq_entries: list[tuple[str, str, str | None]] = []
    for item in local_faq:
        q = item.get("question", "")
        all_questions.append([q])
        kw_intent = _detect_intent(_tokenize(q))
        faq_entries.append((q, item.get("answer", ""), kw_intent))

    # Dialogues
    dialogue_entries: list[tuple[str, str, str | None]] = []
    for item in local_dialogues:
        q = item.get("question", "")
        all_questions.append([q])
        kw_intent = _detect_intent(_tokenize(q))
        dialogue_entries.append((q, item.get("answer", ""), kw_intent))

    # Calculer IDF global
    idf = _compute_idf(all_questions)

    # Scoring de tous les candidats
    candidates: List[Tuple[float, str]] = []

    for questions, answer, kw_intent in kb_entries:
        best_score = max(
            (_score_bidirectional(msg_tokens, q, idf, msg_intent, kw_intent) for q in questions),
            default=0.0
        )
        if best_score >= 0.22:
            candidates.append((best_score, answer))

    for q, answer, kw_intent in faq_entries:
        score = _score_bidirectional(msg_tokens, q, idf, msg_intent, kw_intent)
        if score >= 0.22:
            candidates.append((score, answer))

    for q, answer, kw_intent in dialogue_entries:
        score = _score_bidirectional(msg_tokens, q, idf, msg_intent, kw_intent)
        if score >= 0.22:
            candidates.append((score, answer))

    if candidates:
        return max(candidates, key=lambda x: x[0])[1]

    return None


# Compatibilité: garder score_match pour les imports existants
def score_match(user_message: str, keyword: str) -> float:
    """Score de compatibilité (ancien format unidirectionnel, pour tests)."""
    if not user_message or not keyword:
        return 0.0
    words_msg = _tokenize(user_message)
    words_kw = _tokenize(keyword)
    if not words_kw or not words_msg:
        return 0.0
    intersection = words_msg.intersection(words_kw)
    return len(intersection) / len(words_kw)
