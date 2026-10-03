"""Shared knowledge state and durable learning, independent of Telegram startup.

RÈGLE BOSS (02/10) : AUCUNE donnée ne persiste sur le disque (ni GitHub, ni
Railway). Les dialogues appris via /apprends vivent dans le Google Sheet
dédié « Komara Bot - Mémoire » (module memory_sheets) ; la RAM ne sert que
de cache de publication."""
from __future__ import annotations
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("komara.knowledge")
BASE_DIR = Path(__file__).resolve().parent
LANG_RESOURCES: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
_INITIALIZED = False
_REFRESH_LOADER: Callable | None = None
_CUSTOM_ROWS: list[dict[str, Any]] = []


def _make_entry(question: str, answer: str) -> dict[str, Any]:
    return {"id": "custom_" + uuid.uuid4().hex, "category": "custom",
            "questions": [question], "answer": answer, "tags": ["custom", "admin"]}


def _apply_purge(resources: dict, lang: str) -> None:
    """Obsolète (02/10) : la base officielle est vide — le boss enseigne TOUT
    via /apprends. No-op conservé pour compatibilité des appels existants."""
    return


def initialize_resources(resources: dict, loader: Callable) -> dict:
    """Publish the initial state once, even if the entrypoint is imported twice.

    RÈGLE BOSS (02/10) : les dialogues appris sont relus depuis le Google
    Sheet dédié (memory_sheets.load_learned). Google non lié → base vide,
    l'admin enseigne tout via /apprends. Aucun fichier local lu ni écrit."""
    global _INITIALIZED, _REFRESH_LOADER, _CUSTOM_ROWS
    with _LOCK:
        if _INITIALIZED:
            return LANG_RESOURCES
        LANG_RESOURCES.update(resources)
        _REFRESH_LOADER = loader
        try:
            from memory_sheets import load_learned
            _CUSTOM_ROWS = load_learned()
            logger.info("Dialogues appris relus depuis Google Sheets : %s", len(_CUSTOM_ROWS))
        except Exception:
            logger.exception("Dialogues appris illisibles ; base vide (Google non lié ?)")
            _CUSTOM_ROWS = []
        for lang, target in LANG_RESOURCES.items():
            _apply_purge(target, lang)
        # La dernière version enseignée d'une question similaire gagne
        # (append-only côté Sheet = audit ; runtime = version active).
        _CUSTOM_ROWS = _dedupe_similar_rows(_CUSTOM_ROWS)
        for row in _CUSTOM_ROWS:
            target = LANG_RESOURCES.get(row.get("lang", "fr")) or LANG_RESOURCES.get("fr")
            if target and isinstance(target.get("kb"), list):
                target["kb"].append(_make_entry(row["question"][:200], row["answer"][:1500]))
        _INITIALIZED = True
        return LANG_RESOURCES



def refresh_resources(lang_code: str) -> bool:
    """Reload a language without losing durable learned entries or purges."""
    with _LOCK:
        if not _INITIALIZED or _REFRESH_LOADER is None:
            return False  # import_file can run before the Telegram worker starts
        resources = _REFRESH_LOADER(lang_code)
        for row in _CUSTOM_ROWS:
            if row.get("lang", "fr") == lang_code:
                resources["kb"].append(_make_entry(row["question"][:200], row["answer"][:1500]))
        LANG_RESOURCES[lang_code] = resources
        return True

_STOP_TOKENS = {
    "est", "quoi", "qui", "quand", "comment", "pourquoi", "combien",
    "quel", "quelle", "quels", "quelles", "cest", "vous", "votre",
    "vos", "tu", "toi", "je", "il", "elle", "nous", "ils", "elles",
    "des", "une", "les", "leur", "leurs", "est-ce", "faire", "fait",
    "faites", "avez", "avezvous", "as", "peut", "peux", "pour", "avec",
    "sans", "sur", "dans", "chez", "aussi", "bien", "tres", "peu",
    "the", "what", "how", "why", "who", "where", "when", "which",
    "your", "you", "does", "are", "can", "have", "has", "and", "for",
    "que", "cual", "donde", "como", "cuando", "cuanto", "suyo", "suya",
    "usted", "ustedes", "hace", "hacen", "para", "con", "por", "que",
    "ما", "ماذا", "كيف", "متى", "أين", "من", "هل", "مع", "على",
}


def _content_tokens(text_norm: str) -> set:
    """Tokens porteurs de sens (mots-outils retirés)."""
    return {w for w in re.findall(r"\b\w{3,}\b", text_norm)
            if w not in _STOP_TOKENS}


def _similar_question_exists_unlocked(question: str, lang: str = "fr") -> tuple[str, str] | None:
    """Renvoie (question existante SIMILAIRE, langue où elle vit), ou None.

    Similaire = question identique après normalisation OU recouvrement de
    mots PORTEURS de sens >= 80% (paraphrase « livrez vous a kindia » vs
    « vous livrez a kindia »). Les mots-outils (vous, faites, quoi...) ne
    comptent pas : ils créent de faux doublons entre sujets différents.

    RÈGLE BOSS (02/10) : une question similaire ne bloque plus l'apprentissage,
    voir learn_entry — elle déclenche une MISE À JOUR de la réponse."""
    from normalize_text import normalize_text as _norm
    q_norm = _norm(question or "").strip()
    if not q_norm:
        return None
    q_content = _content_tokens(q_norm)
    for _lang in ("fr", "en", "es", "ar"):
        resources = LANG_RESOURCES.get(_lang) or {}
        for src in ("kb", "faq", "dialogues"):
            for fiche in resources.get(src) or []:
                qs = fiche.get("questions", [])
                if isinstance(qs, str):
                    qs = [qs]
                _q = fiche.get("question")
                if isinstance(_q, str):
                    qs = list(qs) + [_q]
                for cand in qs:
                    c_norm = _norm(str(cand or "")).strip()
                    if not c_norm:
                        continue
                    if c_norm == q_norm:
                        return (str(cand), _lang)
                    # FIX BOSS (03/10) : même critère STRICT que
                    # _questions_similar — sinon /apprends annonce un
                    # remplacement que la purge runtime n'effectue pas.
                    if _questions_similar(q_norm, c_norm):
                        return (str(cand), _lang)
    return None


def _questions_similar(q1: str, q2: str) -> bool:
    """Deux questions sont-elles 'similaires' (même réponse attendue) ?

    FIX BOSS (03/10, « le bot remplace une question par une autre alors
    que les mots sont très différents ») : l'ancien critère (couverture de
    la PLUS COURTE >= 80%) faisait absorber n'importe quelle question
    longue par une question courte qui partage UN mot :
      « catalogue »  ABSORBAIT  « je souhaite voir le catalogue »
      « prix »       ABSORBAIT  « je souhaite connaître le prix d'un logo »
    → à l'enseignement d'une question riche, la fiche courte disparaissait
    (message « remplace ») et à l'inverse la nouvelle question perdait sa
    propre réponse.

    NOUVEAU critère, strictement bidirectionnel — les deux questions
    doivent dire LA MÊME CHOSE :
      • égalité exacte après normalisation, OU
      • mots porteurs partagés >= 80% de la plus courte ET >= 60% de la
        plus longue (« livrez vous à Kindia » ≈ « vous livrez à Kindia »
        reste reconnu ; « catalogue » ≠ « je souhaite voir le catalogue »).
    Tolérance fautes conservée via la normalisation (accents, verbes
    conjugués, SMS : « coûte »≈« coute »)."""
    from normalize_text import normalize_text as _norm
    a, b = _norm(q1 or "").strip(), _norm(q2 or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    short, long_ = min(len(ta), len(tb)), max(len(ta), len(tb))
    return bool(inter) and inter / short >= 0.8 and inter / long_ >= 0.6


def _dedupe_similar_rows(rows: list) -> list:
    """Garde la DERNIÈRE version de chaque question similaire (la plus
    récente enseignée gagne). Append-only côté Sheet = piste d'audit ;
    en runtime on ne garde que la version active."""
    result: list = []
    for row in rows:
        replaced = False
        for i, kept in enumerate(result):
            if _questions_similar(str(kept.get("question", "")),
                                  str(row.get("question", ""))):
                result[i] = row
                replaced = True
                break
        if not replaced:
            result.append(row)
    return result


def similar_question_exists(question: str, lang: str = "fr") -> str | None:
    with _LOCK:
        found = _similar_question_exists_unlocked(question, lang)
        return found[0] if found else None


def add_custom_kb_entry(question: str, answer: str, lang: str = "fr") -> bool:
    """Compatibility helper for runtime-only additions; durable learning uses learn_entry."""
    try:
        question = str(question or "").strip()[:200]
        answer = str(answer or "").strip()[:1500]
        with _LOCK:
            target = LANG_RESOURCES.get(lang) or LANG_RESOURCES.get("fr")
            if not question or not answer or not target or not isinstance(target.get("kb"), list):
                return False
            target["kb"].append(_make_entry(question, answer))
            return True
    except Exception:
        logger.exception("Runtime knowledge addition failed")
        return False


def learn_entry(question: str, answer: str, lang: str = "fr", directory=None) -> dict:
    """Serialize duplicate check, durable write and runtime publication.

    RÈGLE BOSS (02/10) : la base de dialogues est ENTIÈREMENT enseignée par
    l'admin (/apprends). La persistance durable est APPEND-ONLY dans le
    Google Sheet dédié « Komara Bot - Mémoire » (memory_sheets.save_learned)
    — jamais de fichier local, jamais sur le dépôt GitHub ni le disque
    Railway. Au rechargement, la version la plus récente d'une question
    gagne (l'historique complet sert de piste d'audit à l'admin).

    Si une question SIMILAIRE existe déjà (apprise par le boss), sa réponse
    est REMPLACÉE (nouvelle version appendée). Échec de sauvegarde Google →
    exception, la publication runtime n'a PAS lieu et l'admin est prévenu."""
    global _CUSTOM_ROWS
    question = str(question or "").strip()[:200]
    answer = str(answer or "").strip()[:1500]
    if not question or not answer:
        raise ValueError("Question and answer are required")
    with _LOCK:
        target = LANG_RESOURCES.get(lang) or LANG_RESOURCES.get("fr")
        if not target or not isinstance(target.get("kb"), list):
            raise RuntimeError("Knowledge base is not loaded")
        found = _similar_question_exists_unlocked(question, lang)
        matched_text = found[0] if found else None
        matched_lang = found[1] if found else lang

        # Sauvegarde durable EXTERNE (Google Sheets). Lève en cas d'échec →
        # l'appelant ne doit JAMAIS annoncer un succès non garanti.
        from memory_sheets import save_learned
        save_learned(question, answer, lang)

        # Publication runtime : retire TOUTE question similaire (pas juste
        # l'exacte) puis ajoute la nouvelle — la réponse la plus récente
        # enseignée par le boss gagne toujours.
        rows = [r for r in _CUSTOM_ROWS
                if not _questions_similar(str(r.get("question", "")), question)]
        replaced = len(rows) < len(_CUSTOM_ROWS)  # une question similaire a été retirée
        rows.append({"question": question, "answer": answer, "lang": lang})
        _CUSTOM_ROWS = rows

        for lg in {lang, matched_lang}:
            if not refresh_resources(lg):
                # Pas encore de loader (tests unitaires isolés) : on publie
                # au moins la nouvelle entrée pour ne pas perdre l'ajout.
                tgt = LANG_RESOURCES.get(lg) or LANG_RESOURCES.get("fr")
                if tgt and isinstance(tgt.get("kb"), list):
                    tgt["kb"].append(_make_entry(question, answer))
        return {"added": True, "updated": bool(replaced or found), "question": question,
                "answer": answer, "replaced": matched_text}


def learn_entries_batch(entries, lang: str = "fr") -> dict:
    """Version LOT de learn_entry : un seul append Sheets pour N fiches
    (utilisé par /kb_import). Mêmes garanties : échec Google → exception,
    aucune publication runtime d'un ajout non persisté."""
    global _CUSTOM_ROWS
    cleaned = []
    seen = set()
    for q, a in entries:
        q = str(q or "").strip()[:200]
        a = str(a or "").strip()[:1500]
        if not q or not a:
            continue
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((q, a))
    if not cleaned:
        raise ValueError("No valid entries to learn")
    with _LOCK:
        target = LANG_RESOURCES.get(lang) or LANG_RESOURCES.get("fr")
        if not target or not isinstance(target.get("kb"), list):
            raise RuntimeError("Knowledge base is not loaded")
        from memory_sheets import get_memory_sheet_id, append_rows, _now
        sheet_id = get_memory_sheet_id()
        if not sheet_id:
            raise RuntimeError("Google non lié : lance /google avant d'importer")
        rows = [[_now(), lang, q[:200], a[:1500]] for q, a in cleaned]
        if not append_rows("Dialogues", rows):
            raise RuntimeError("Écriture des dialogues dans Google Sheets impossible")
        # publication runtime : retire les questions similaires aux nouvelles
        # (réponse la plus récente gagne), puis ajoute les nouvelles.
        new_qs = {q.casefold() for q, _ in cleaned}
        rows = [r for r in _CUSTOM_ROWS
                if not any(_questions_similar(str(r.get("question", "")), q)
                           for q, _ in cleaned)]
        for q, a in cleaned:
            rows.append({"question": q, "answer": a, "lang": lang})
        _CUSTOM_ROWS = rows
        refresh_resources(lang)
        return {"added": len(cleaned), "lang": lang}
