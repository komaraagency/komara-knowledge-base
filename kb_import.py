# ---------------------------------------------------------------------------
# /kb_import — Import de fichiers dans la base de connaissances
# 100% local : parseurs JSON/CSV/TXT/MD, dédoublonnage, fusion, rechargement
# à chaud. Téléchargement via l'API Bot native (aucune clé IA).
# Persistance GitHub optionnelle : GITHUB_TOKEN + GITHUB_REPO (variables Railway).
# ---------------------------------------------------------------------------

import base64
import csv
import io
import json
import os
import re
from pathlib import Path

import requests
from normalize_text import normalize_text

BASE_DIR = Path(__file__).resolve().parent
LANG_DIR = BASE_DIR / "lang"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "komaraagency/komara-knowledge-base")

SUPPORTED_EXT = {".json", ".csv", ".txt", ".md", ".tsv"}

USAGE = (
    "📥 /kb_import — enrichis la base de connaissances\n\n"
    "Envoie-moi un fichier .json, .csv, .txt ou .md en pièce jointe.\n"
    "Formats acceptés :\n"
    "• JSON : {\"knowledge\": [...]} ou [{\"q\": \"...\", \"a\": \"...\"}]\n"
    "• CSV  : question;rponse  (séparateur ; , ou tabulation)\n"
    "• TXT/MD : \"Q: question\" puis \"A: réponse\", ou ### question\\nréponse\n\n"
    "Ajoute une légende pour choisir la langue : 'en', 'es', 'ar' (défaut : fr).\n"
    "Doublons détectés automatiquement — seule les nouvelles fiches sont ajoutées."
)

TEXTS = {
    "fr": {
        "imported": "📥 Import réussi !\n\n📄 Fichier : {filename}\n✔️ Nouvelles fiches : {added}\n♻️ Doublons ignorés : {dupes}\n📚 Total {lang_up} : {total} fiches\n\n{persistence}",
        "bad_file": "❌ Je ne peux lire que .json, .csv, .txt ou .md (et < 5 Mo).",
        "parse_error": "❌ Fichier illisible : {err}\n\nRelis le format attendu :\n\n{usage}",
        "admin_only": "👑 Import réservé à l'admin.",
        "pushed": "✅ Synchronisé avec GitHub — l'import survit aux redéploiements.",
        "not_pushed": "⚠️ Sans GITHUB_TOKEN sur Railway, l'import est temporaire (effacé au prochain déploiement).",
    },
    "en": {
        "imported": "📥 Import successful!\n\n📄 File: {filename}\n✔️ New entries: {added}\n♻️ Duplicates skipped: {dupes}\n📚 {lang_up} total: {total} entries\n\n{persistence}",
        "bad_file": "❌ I can only read .json, .csv, .txt or .md (and < 5 MB).",
        "parse_error": "❌ Unreadable file: {err}\n\nExpected format:\n\n{usage}",
        "admin_only": "👑 Import reserved for the admin.",
        "pushed": "✅ Synced with GitHub — the import survives redeploys.",
        "not_pushed": "⚠️ Without GITHUB_TOKEN on Railway, the import is temporary (wiped on next deploy).",
    },
    "es": {
        "imported": "📥 ¡Importación hecha!\n\n📄 Archivo: {filename}\n✔️ Nuevas fichas: {added}\n♻️ Duplicados ignorados: {dupes}\n📚 Total {lang_up}: {total} fichas\n\n{persistence}",
        "bad_file": "❌ Solo puedo leer .json, .csv, .txt o .md (y < 5 MB).",
        "parse_error": "❌ Archivo ilegible: {err}\n\nFormato esperado:\n\n{usage}",
        "admin_only": "👑 Importación reservada al admin.",
        "pushed": "✅ Sincronizado con GitHub — la importación sobrevive a los despliegues.",
        "not_pushed": "⚠️ Sin GITHUB_TOKEN en Railway, la importación es temporal (se borra al desplegar).",
    },
    "ar": {
        "imported": "📥 تم الاستيراد!\n\n📄 الملف: {filename}\n✔️ بطاقات جديدة: {added}\n♻️ مكررات متجاهلة: {dupes}\n📚 المجموع {lang_up}: {total} بطاقة\n\n{persistence}",
        "bad_file": "❌ أقرأ فقط .json أو .csv أو .txt أو .md (وأقل من 5 ميغا).",
        "parse_error": "❌ ملف غير مقروء: {err}\n\nالصيغة المطلوبة:\n\n{usage}",
        "admin_only": "👑 الاستيراد للأدمن فقط.",
        "pushed": "✅ تمت المزامنة مع GitHub — الاستيراد يبقى بعد إعادة النشر.",
        "not_pushed": "⚠️ بدون GITHUB_TOKEN على Railway الاستيراد مؤقت (يُمسح عند النشر القادم).",
    },
}


def t(lang: str, key: str, **kw) -> str:
    base = TEXTS.get(lang, TEXTS["fr"]).get(key, TEXTS["fr"].get(key, key))
    return base.format(**kw) if kw else base


# ---------------------------------------------------------------------------
# Parseurs — chacun retourne [(question, réponse), ...]
# ---------------------------------------------------------------------------

def parse_json(data: bytes) -> list:
    obj = json.loads(data.decode("utf-8"))
    entries = []
    if isinstance(obj, dict):
        items = obj.get("knowledge") or obj.get("entries") or obj.get("faq") or []
    elif isinstance(obj, list):
        items = obj
    else:
        raise ValueError("JSON inattendu (ni liste, ni objet)")
    for it in items:
        if not isinstance(it, dict):
            continue
        q = it.get("question") or it.get("q") or (
            it["questions"][0] if isinstance(it.get("questions"), list) and it["questions"] else ""
        )
        a = it.get("answer") or it.get("a") or it.get("réponse") or ""
        if q and a:
            entries.append((q, a))
    return entries


def parse_csv(text: str) -> list:
    entries = []
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        class Semicolon: delimiter = ";"
        dialect = Semicolon()
    reader = csv.reader(io.StringIO(text), delimiter=dialect.delimiter)
    for row in reader:
        if len(row) < 2:
            continue
        q, a = row[0].strip(), row[1].strip()
        low = q.lower()
        if low in {"question", "q", "pregunta", "السؤال"} or not q or not a:
            continue  # en-tête
        entries.append((q, a))
    return entries


def parse_txt(text: str) -> list:
    entries = []
    # 1. Blocs "Q: ... / A: ..." ou "Question: ... / Réponse: ..."
    qa_blocks = re.findall(
        r"(?im)^\s*(?:q(?:uestion)?|السؤال)\s*[:.]\s*(.+?)\s*\n+\s*(?:a(?:nswer|r(?:éponse)?)?|الجواب|الإجابة)\s*[:.]\s*(.+?)\s*(?=\n\s*(?:q(?:uestion)?)\s*[:.]|\Z)",
        text,
    )
    if qa_blocks:
        return [(q.strip(), a.strip()) for q, a in qa_blocks]
    # 2. Sections markdown ### question + corps réponse
    sections = re.split(r"(?m)^\s*###\s+(.*?)\s*\n", text)
    for i in range(1, len(sections) - 1, 2):
        q, a = sections[i].strip(), sections[i + 1].strip()
        if q and a:
            entries.append((q, a))
    if entries:
        return entries
    # 3. Lignes "question | réponse"
    for line in text.splitlines():
        if "|" in line:
            parts = [p.strip() for p in line.split("|", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                entries.append(tuple(parts))
    return entries


def parse_file(filename: str, data: bytes) -> list:
    ext = Path(filename).suffix.lower()
    text = None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            pass
    if ext == ".json":
        return parse_json(data)
    if ext in {".csv", ".tsv"}:
        return parse_csv(text)
    if ext in {".txt", ".md"}:
        return parse_txt(text)
    raise ValueError(f"extension non supportée : {ext}")


# ---------------------------------------------------------------------------
# Fusion + dédoublonnage + rechargement à chaud
# ---------------------------------------------------------------------------

def kb_path_for(lang: str) -> Path:
    return (LANG_DIR / lang / "kb.json") if lang != "fr" else BASE_DIR / "kb.json"


def _existing_questions(lang: str) -> set:
    """Toutes les questions connues (normalisées) pour cette langue.
    FR = racine kb.json + lang/fr/kb.json (fusion du loader)."""
    paths = [kb_path_for(lang)]
    if lang == "fr":
        extra = LANG_DIR / "fr" / "kb.json"
        if extra not in paths:
            paths.append(extra)
    known = set()
    for path in paths:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("knowledge", []):
                for q in item.get("questions", []):
                    known.add(normalize_text(q))
    return known


def import_file(filename: str, data: bytes, lang: str = "fr") -> dict:
    """Fusionne le fichier dans la KB de la langue. Retourne le rapport."""
    parsed = parse_file(filename, data)
    path = kb_path_for(lang)
    kb = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"knowledge": []}
    knowledge = kb.setdefault("knowledge", [])
    known = _existing_questions(lang)

    added = dupes = 0
    for q, a in parsed:
        nq = normalize_text(q)
        if not nq or nq in known:
            dupes += 1
            continue
        known.add(nq)
        seq = len(knowledge) + 1
        item_id = f"import_{lang}_{seq}"
        while any(k.get("id") == item_id for k in knowledge):
            seq += 1
            item_id = f"import_{lang}_{seq}"
        knowledge.append({
            "id": item_id,
            "category": "import",
            "questions": [q.strip()[:300]],
            "answer": a.strip()[:4000],
            "tags": ["import", "local"],
        })
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")

    # Rechargement à chaud (fusion fr réappliquée)
    import rag_bot
    rag_bot.refresh_resources(lang)

    # Persistance GitHub (optionnelle)
    pushed = False
    if GITHUB_TOKEN and added:
        try:
            pushed = _push_github(path, lang, added)
        except Exception:
            pushed = False

    return {
        "filename": filename, "added": added, "dupes": dupes,
        "total": len(knowledge), "pushed": pushed, "lang": lang,
    }


def _push_github(path: Path, lang: str, added: int) -> bool:
    """Pousse le kb.json mis à jour via l'API GitHub Contents (zéro dépendance git)."""
    api = "https://api.github.com"
    rel = path.relative_to(BASE_DIR).as_posix()
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(f"{api}/repos/{GITHUB_REPO}/contents/{rel}",
                     headers=headers, timeout=30)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {
        "message": f"📥 /kb_import Telegram : {added} nouvelles fiches [{lang}]",
        "content": base64.b64encode(path.read_bytes()).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{api}/repos/{GITHUB_REPO}/contents/{rel}",
                    headers=headers, json=payload, timeout=30)
    return r.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Point d'entrée Telegram
# ---------------------------------------------------------------------------

def is_admin(chat_id: int) -> bool:
    admin = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
    return bool(admin) and chat_id == admin


def handle_document(bot, message, detected_lang: str = "fr") -> None:
    """Document reçu (admin) → import dans la KB."""
    chat_id = message.chat.id
    if not is_admin(chat_id):
        bot.send_message(chat_id, t(detected_lang, "admin_only"))
        return
    doc = message.document
    filename = doc.file_name or "fichier"
    if Path(filename).suffix.lower() not in SUPPORTED_EXT or doc.file_size > 5 * 1024 * 1024:
        bot.send_message(chat_id, t(detected_lang, "bad_file"))
        return
    # Langue cible depuis la légende (fr par défaut)
    lang = detected_lang if detected_lang in {"fr", "en", "es", "ar"} else "fr"
    caption = (message.caption or "").strip().lower()
    for code in ("en", "es", "ar", "fr"):
        if re.search(rf"\b{code}\b", caption):
            lang = code
            break
    try:
        file_info = bot.get_file(doc.file_id)
        data = bot.download_file(file_info.file_path)
        report = import_file(filename, data, lang)
    except ValueError as e:
        bot.send_message(chat_id, t(lang, "parse_error", err=str(e)[:150], usage=USAGE))
        return
    except Exception as e:
        bot.send_message(chat_id, t(lang, "parse_error", err=str(e)[:150], usage=USAGE))
        return
    persistence = t(lang, "pushed") if report["pushed"] else t(lang, "not_pushed")
    bot.send_message(
        chat_id,
        t(lang, "imported", filename=filename, added=report["added"],
          dupes=report["dupes"], lang_up=lang.upper(), total=report["total"],
          persistence=persistence),
    )
