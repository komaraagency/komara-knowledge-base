# ---------------------------------------------------------------------------
# memory_sheets — Stockage externe de TOUT ce que le bot collecte.
#
# RÈGLE BOSS (02/10) : AUCUNE information, note, donnée ou dialogue collecté
# par le bot ne doit fuiter ou se sauvegarder sur le dépôt GitHub ni sur le
# disque Railway. Tout est APPEND dans un Google Sheet DÉDIÉ :
#   « Komara Bot - Mémoire » (créé automatiquement au premier lien Google).
#
# Onglets :
#   Dialogues               : Q/R apprises par l'admin via /apprends
#   Conversations           : chaque message client + chaque réponse du bot
#   Questions sans réponse  : questions que la base ne couvrait pas
#
# Si le compte Google de l'admin n'est PAS lié (/google) : aucune écriture
# nulle part — les données restent en RAM éphémère et meurent avec le
# conteneur. Jamais de fichier local de persistance.
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging
import os
import threading
import urllib.parse
from datetime import datetime, timezone

import requests

logger = logging.getLogger("komara.memory_sheets")

SHEET_NAME = os.getenv("MEMORY_SHEET_NAME", "Komara Bot - Mémoire")
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
SHEETS_BASE_URL = "https://sheets.googleapis.com/v4/spreadsheets"

TAB_HEADERS: dict[str, list[str]] = {
    "Dialogues": ["Date", "Langue", "Question", "Réponse"],
    "Conversations": ["Date", "Chat ID", "Nom", "Rôle", "Contenu", "Langue"],
    "Questions sans réponse": ["Date", "Question", "Langue", "Chat", "Occurrences"],
    "Logique": ["Départ", "Suite", "Date", "Admin"],
}

_sheet_id = ""
_sheet_lock = threading.Lock()


def _token() -> str:
    from google_link import get_access_token  # import tardif (évite cycle)
    return get_access_token()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Résolution du Google Sheet dédié (env > recherche Drive > création)
# ---------------------------------------------------------------------------
def get_memory_sheet_id() -> str:
    """ID du tableur dédié. Priorité : MEMORY_SHEET_ID (env) > recherche
    Drive par nom > création d'un nouveau tableur. '' = non résoluble."""
    global _sheet_id
    if _sheet_id:
        return _sheet_id
    with _sheet_lock:
        if _sheet_id:
            return _sheet_id
        env_id = os.getenv("MEMORY_SHEET_ID", "").strip()
        if env_id:
            _sheet_id = env_id
            return _sheet_id
        token = _token()
        if not token:
            return ""
        try:
            # 1. Le tableur existe-t-il déjà ?
            resp = requests.get(
                DRIVE_FILES_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": f"name = '{SHEET_NAME}' and mimeType = "
                             f"'application/vnd.google-apps.spreadsheet' and trashed = false",
                        "fields": "files(id,name)"},
                timeout=30,
            )
            files = resp.json().get("files", []) if resp.status_code == 200 else []
            if files:
                _sheet_id = files[0]["id"]
            else:
                # 2. Sinon on le crée, avec ses onglets.
                sheets = [{"properties": {"title": tab}} for tab in TAB_HEADERS]
                resp = requests.post(
                    SHEETS_BASE_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"properties": {"title": SHEET_NAME}, "sheets": sheets},
                    timeout=30,
                )
                if resp.status_code not in (200, 201):
                    logger.error("Création du sheet mémoire impossible : %s",
                                 resp.text[:200])
                    return ""
                _sheet_id = resp.json().get("spreadsheetId", "")
                if _sheet_id:
                    write_headers(_sheet_id)
        except requests.RequestException:
            logger.exception("Résolution du sheet mémoire impossible")
            return ""
        return _sheet_id


def write_headers(sheet_id: str) -> None:
    """Écrit la ligne d'en-tête de chaque onglet (idempotent)."""
    token = _token()
    if not token or not sheet_id:
        return
    for tab, headers in TAB_HEADERS.items():
        try:
            rng = urllib.parse.quote(f"{tab}!A1")
            url = (f"{SHEETS_BASE_URL}/{sheet_id}/values/{rng}"
                   f"?valueInputOption=RAW")
            requests.put(url, headers={"Authorization": f"Bearer {token}"},
                         json={"values": [headers]}, timeout=30)
        except requests.RequestException:
            logger.exception("En-tête %s impossible", tab)


def ensure_tab(sheet_id: str, tab: str) -> bool:
    """Crée l'onglet s'il manque (tableur créé manuellement par l'admin)."""
    token = _token()
    if not token or not sheet_id:
        return False
    try:
        resp = requests.get(f"{SHEETS_BASE_URL}/{sheet_id}",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"fields": "properties.title,sheets.properties.title"},
                            timeout=30)
        titles = [s["properties"]["title"] for s in resp.json().get("sheets", [])] \
            if resp.status_code == 200 else []
        if tab in titles:
            return True
        requests.post(f"{SHEETS_BASE_URL}/{sheet_id}:batchUpdate",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
                      timeout=30)
        rng = urllib.parse.quote(f"{tab}!A1")
        requests.put(f"{SHEETS_BASE_URL}/{sheet_id}/values/{rng}"
                     f"?valueInputOption=RAW",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"values": [TAB_HEADERS[tab]]}, timeout=30)
        return True
    except requests.RequestException:
        logger.exception("ensure_tab %s impossible", tab)
        return False


# ---------------------------------------------------------------------------
# Primitives read / append
# ---------------------------------------------------------------------------
def append_rows(tab: str, rows: list[list]) -> bool:
    """Append brut. Retourne False silencieusement si Google non lié."""
    if not rows:
        return False
    sheet_id = get_memory_sheet_id()
    token = _token()
    if not sheet_id or not token:
        return False
    if not ensure_tab(sheet_id, tab):
        return False
    rng = urllib.parse.quote(f"{tab}!A1")
    url = (f"{SHEETS_BASE_URL}/{sheet_id}/values/{rng}:append"
           f"?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                             json={"values": rows}, timeout=30)
        return resp.status_code == 200
    except requests.RequestException:
        logger.exception("append_rows(%s) impossible", tab)
        return False


class SheetUnavailableError(RuntimeError):
    """Google lié mais lecture impossible (réseau, quota, HTTP != 200).
    Distinction cruciale (Boss 10/10) : « échec de lecture » ≠ « base
    vide » — confondre les deux faisait réinsérer tout le seed en
    doublon dans le Sheet de production."""


def is_configured() -> bool:
    """Google lié ? (Sheet mémoire + token présents)"""
    return bool(get_memory_sheet_id() and _token())


def read_rows(tab: str, limit: int = 0, strict: bool = False) -> list[list[str]]:
    """Lit tout l'onglet (limit > 0 : derniers N lignes utiles). '' si indispo."""
    sheet_id = get_memory_sheet_id()
    token = _token()
    if not sheet_id or not token:
        return []
    strict = strict and True  # lisibilité : mode « échec = exception »
    rng = urllib.parse.quote(f"{tab}!A:Z")
    try:
        resp = requests.get(f"{SHEETS_BASE_URL}/{sheet_id}/values/{rng}",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=60)
        if resp.status_code != 200:
            if strict:
                raise SheetUnavailableError(
                    f"HTTP {resp.status_code} sur l'onglet {tab}")
            return []
        rows = resp.json().get("values", [])
        # saute l'en-tête
        rows = [[("" if cell is None else str(cell)) for cell in row] for row in rows[1:]]
        if limit and len(rows) > limit:
            rows = rows[-limit:]
        return rows
    except requests.RequestException:
        if strict:
            raise
        logger.exception("read_rows(%s) impossible", tab)
        return []


def _append_async(tab: str, row: list) -> None:
    """Append en thread daemon : ne bloque JAMAIS la réponse au client."""
    threading.Thread(target=append_rows, args=(tab, [row]),
                     name=f"sheets-{tab[:12]}", daemon=True).start()


# ---------------------------------------------------------------------------
# API métier
# ---------------------------------------------------------------------------
def log_conversation(chat_id, name: str, role: str, content: str, lang: str = "") -> None:
    """Miroir d'un tour de conversation (client ou bot) vers le Sheet."""
    try:
        content = (content or "").strip()
        if not content:
            return
        _append_async("Conversations",
                      [_now(), str(chat_id), (name or "")[:100], role,
                       content[:2000], lang])
    except Exception:
        logger.exception("log_conversation impossible")


def log_lead(when: str, chat_id, name: str, phone: str,
             sector: str, need: str, budget: str) -> None:
    """Miroir d'un lead vers l'onglet « Leads » du Sheet mémoire (Boss
    06/10) : le lead est DÉFINITIF — il survit aux redéploiements Railway
    et au cache SQLite éphémère. Append async, ne bloque jamais le bot."""
    try:
        _append_async("Leads", [[when or _now(), str(chat_id)[:20],
                                 (name or "")[:100], (phone or "")[:50],
                                 (sector or "")[:100], (need or "")[:500],
                                 (budget or "")[:100]]])
    except Exception:
        logger.exception("log_lead impossible")


def log_unanswered(question: str, lang: str, chat_id, count: int) -> None:
    """Question que la base ne couvrait pas → onglet dédié."""
    try:
        _append_async("Questions sans réponse",
                      [_now(), (question or "")[:300], lang, str(chat_id), str(count)])
    except Exception:
        logger.exception("log_unanswered impossible")


def save_learned(question: str, answer: str, lang: str) -> bool:
    """Append-only : chaque /apprends ajoute une ligne. Au chargement, la
    DERNIÈRE version d'une question gagne (l'historique sert de piste
    d'audit pour l'admin). Échoue proprement si Google non lié."""
    sheet_id = get_memory_sheet_id()
    token = _token()
    if not sheet_id or not token:
        raise RuntimeError(
            "Google non lié : lance /google pour autoriser la sauvegarde des dialogues")
    # PATCH BOSS 06/10 : réponses longues coupées à 1500 caractères →
    # 5000 (question 500). + hygiène AVANT écriture : ce qui est stocké
    # dans le Sheet est déjà propre (ci_dessous → ci-dessous).
    question = fix_sheet_typos(question)[:500]
    answer = fix_sheet_typos(answer)[:5000]
    if not append_rows("Dialogues", [[_now(), lang, question, answer]]):
        raise RuntimeError("Écriture du dialogue dans Google Sheets impossible")
    return True


# HYGIÈNE DE LECTURE (Boss 06/10, screenshot « ci_dessous ») : le boss
# stocke TOUTE la mémoire dans ce Google Sheet — le bot doit donc
# appliquer la même méthode de compréhension sur les tokens LUS qu'à
# l'écriture. Certains /apprends arrivent avec des underscores de
# copier-coller à la place des apostrophes/liions (« l_équipe »,
# « ci_dessous »). On répare au CHARGEMENT (lecture) et à l'ÉCRITURE
# (save_learned) — la fiche dans le Sheet garde sa trace d'audit, mais
# ce que le bot lit/affiche est toujours propre.
import re as _re
_TYPO_APOSTROPHE = _re.compile(r"(\b[ldjncsmqt])_(?=[a-zA-Zàâäéèêëîïôöùûüç])")
_TYPO_CI = _re.compile(r"\bci_(dessous|dessus)\b", _re.IGNORECASE)


def fix_sheet_typos(text: str) -> str:
    """Répare les underscores parasites d'un texte lu depuis le Sheet :
    « l_équipe » → « l'équipe », « ci_dessous » → « ci-dessous ».
    Ne touche à RIEN d'autre (les mots composés légitimes avec
    tiret bas, style « mon_site », restent inchangés si la 1re partie
    fait plus d'une lettre)."""
    t = str(text or "")
    t = _TYPO_APOSTROPHE.sub(r"\1'", t)
    t = _TYPO_CI.sub(r"ci-\1", t)
    return t


def load_learned(strict: bool = False) -> list[dict]:
    """Relit les dialogues appris. Dédoublonne : la version la plus
    récente d'une question gagne. [] si Google non lié (base vide).
    RÈGLE BOSS (06/10) : les tokens lus du Sheet passent par la MÊME
    méthode de compréhension que le seed — y compris l'hygiène des
    typos (fix_sheet_typos) et les variantes « | » (knowledge_store)."""
    rows = read_rows("Dialogues", strict=strict)
    learned: dict[str, dict] = {}
    for row in rows:
        if len(row) < 4:
            continue
        _date, lang, question, answer = row[0], row[1], row[2], row[3]
        question = fix_sheet_typos(question)
        answer = fix_sheet_typos(answer)
        if not question.strip() or not answer.strip():
            continue
        key = question.strip().casefold()
        learned[key] = {"question": question, "answer": answer,
                        "lang": (lang or "fr").strip() or "fr", "date": _date}
    return list(learned.values())
