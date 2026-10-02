"""Shared knowledge state and durable learning, independent of Telegram startup."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("komara.knowledge")
BASE_DIR = Path(__file__).resolve().parent
LANG_RESOURCES: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
_INITIALIZED = False
_REFRESH_LOADER: Callable | None = None
_CUSTOM_ROWS: list[dict[str, Any]] = []
# Questions officielles (kb.json/faq/dialogues) neutralisées parce que le
# boss a réappris une réponse différente pour ce libellé EXACT via
# /apprends. Clé = langue, valeur = set de questions normalisées.
_PURGED: dict[str, set[str]] = {}


def _custom_path(directory=None) -> Path:
    return Path(directory if directory is not None else os.getenv("ACTIONS_DIR", str(BASE_DIR / "data"))) / "kb_custom.json"


def _purged_path(directory=None) -> Path:
    return Path(directory if directory is not None else os.getenv("ACTIONS_DIR", str(BASE_DIR / "data"))) / "kb_purged.json"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("The learned knowledge file must contain a list")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("question"), str)
                or not isinstance(row.get("answer"), str)
                or not row["question"].strip() or not row["answer"].strip()
                or ("lang" in row and not isinstance(row["lang"], str))):
            raise ValueError("Invalid learned knowledge entry; original file preserved")
    return rows


def _read_purged(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("The purged questions file must contain an object")
        return {str(lang): set(str(q) for q in qs) for lang, qs in data.items()}
    except Exception:
        logger.exception("Purged questions file unreadable; treated as empty")
        return {}


def _make_entry(question: str, answer: str) -> dict[str, Any]:
    return {"id": "custom_" + uuid.uuid4().hex, "category": "custom",
            "questions": [question], "answer": answer, "tags": ["custom", "admin"]}


def _apply_purge(resources: dict, lang: str) -> None:
    """Retire des ressources natives (kb/faq/dialogues) toute question que
    le boss a explicitement remplacée via /apprends (voir learn_entry).
    Ne touche jamais les fichiers sur disque : agit uniquement sur la
    publication en mémoire, rejouée à chaque (re)chargement."""
    purged = _PURGED.get(lang)
    if not purged:
        return
    from normalize_text import normalize_text as _norm
    for item in resources.get("kb") or []:
        qs = item.get("questions")
        if isinstance(qs, list):
            item["questions"] = [q for q in qs if _norm(str(q)).strip() not in purged]
        q = item.get("question")
        if isinstance(q, str) and _norm(q).strip() in purged:
            item["question"] = ""
    for src in ("faq", "dialogues"):
        resources[src] = [item for item in (resources.get(src) or [])
                          if _norm(str(item.get("question", ""))).strip() not in purged]


def initialize_resources(resources: dict, loader: Callable) -> dict:
    """Publish the initial state once, even if the entrypoint is imported twice."""
    global _INITIALIZED, _REFRESH_LOADER, _CUSTOM_ROWS, _PURGED
    with _LOCK:
        if _INITIALIZED:
            return LANG_RESOURCES
        LANG_RESOURCES.update(resources)
        _REFRESH_LOADER = loader
        try:
            _PURGED = _read_purged(_purged_path())
        except Exception:
            logger.exception("Purged questions could not be loaded; treated as empty")
            _PURGED = {}
        for lang, target in LANG_RESOURCES.items():
            _apply_purge(target, lang)
        try:
            _CUSTOM_ROWS = _read_rows(_custom_path())
        except Exception:
            logger.exception("Learned knowledge could not be loaded; original file preserved")
            _CUSTOM_ROWS = []
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
        _apply_purge(resources, lang_code)
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
                    c_content = _content_tokens(c_norm)
                    if not q_content or not c_content:
                        continue
                    inter = len(q_content & c_content)
                    # la NOUVELLE question doit être couverte à >= 80% par
                    # les mots porteurs de l'existante : une fiche courte
                    # (« logo ») ne doit pas absorber une question plus
                    # riche (« vous faites des logos pro »).
                    if inter and inter / len(q_content) >= 0.8:
                        return (str(cand), _lang)
    return None


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


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".kb_write_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def learn_entry(question: str, answer: str, lang: str = "fr", directory=None) -> dict:
    """Serialize duplicate check, durable atomic write and runtime publication.

    RÈGLE BOSS (02/10 — fini le blocage) : si une question similaire existe
    déjà (apprise par le boss OU dans la base officielle kb.json/faq/
    dialogues), /apprends REMPLACE sa réponse au lieu de refuser :
    - similaire apprise par le boss -> sa réponse est mise à jour en place ;
    - similaire officielle (kb.json/faq/dialogues, jamais modifiée sur
      disque) -> cette question précise est neutralisée en mémoire
      (kb_purged.json) et la nouvelle réponse du boss devient la seule
      servie pour ce libellé exact.

    Failed or corrupt storage raises an error and leaves runtime knowledge
    unchanged. Callers must never announce success when this method raises.
    """
    global _CUSTOM_ROWS, _PURGED
    question = str(question or "").strip()[:200]
    answer = str(answer or "").strip()[:1500]
    if not question or not answer:
        raise ValueError("Question and answer are required")
    with _LOCK:
        target = LANG_RESOURCES.get(lang) or LANG_RESOURCES.get("fr")
        if not target or not isinstance(target.get("kb"), list):
            raise RuntimeError("Knowledge base is not loaded")
        found = _similar_question_exists_unlocked(question, lang)
        path = _custom_path(directory)
        rows = _read_rows(path)  # Never silently overwrite corrupt/unreadable data.

        from normalize_text import normalize_text as _norm
        updated = False
        matched_text = None
        matched_lang = lang
        if found:
            matched_text, matched_lang = found
            matched_norm = _norm(matched_text).strip()
            new_rows = []
            for row in rows:
                if _norm(row.get("question", "")).strip() == matched_norm:
                    new_rows.append({**row, "question": question, "answer": answer,
                                     "date": datetime.now(timezone.utc).isoformat()})
                    updated = True
                else:
                    new_rows.append(row)
            if not updated:
                # La question matchée vient de la base officielle (jamais
                # modifiée sur disque) : on la neutralise en mémoire et on
                # ajoute la version du boss comme nouvelle entrée custom.
                purged_path = _purged_path(directory)
                purged_data = _read_purged(purged_path)
                purged_data.setdefault(matched_lang, set()).add(matched_norm)
                _atomic_write(purged_path, {lg: sorted(qs) for lg, qs in purged_data.items()})
                _PURGED = purged_data
                new_rows = rows + [{"question": question, "answer": answer, "lang": lang,
                                    "date": datetime.now(timezone.utc).isoformat()}]
        else:
            new_rows = rows + [{"question": question, "answer": answer, "lang": lang,
                               "date": datetime.now(timezone.utc).isoformat()}]

        _atomic_write(path, new_rows)
        _CUSTOM_ROWS = new_rows
        # Republie entièrement les langues concernées : relecture des
        # fichiers natifs + purge + réapplication de TOUT le store custom
        # (garantit qu'il n'existe jamais deux réponses pour la même
        # question, ni un doublon en mémoire après une mise à jour).
        for lg in {lang, matched_lang}:
            if not refresh_resources(lg):
                # Pas encore de loader (tests unitaires isolés) : on publie
                # au moins la nouvelle entrée pour ne pas perdre l'ajout.
                tgt = LANG_RESOURCES.get(lg) or LANG_RESOURCES.get("fr")
                if tgt and isinstance(tgt.get("kb"), list):
                    tgt["kb"].append(_make_entry(question, answer))
        return {"added": True, "updated": updated, "question": question,
                "answer": answer, "replaced": matched_text if found else None}
