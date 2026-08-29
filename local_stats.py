"""Statistiques locales minimales pour les questions sans réponse."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from local_search import normalize_text

DEFAULT_STATS_FILE = Path(__file__).resolve().parent / "data" / "unrecognized_questions.json"
STATS_FILE = Path(os.getenv("UNRECOGNIZED_STATS_FILE", str(DEFAULT_STATS_FILE)))
MAX_ITEMS = max(10, int(os.getenv("STATS_MAX_ITEMS", "1000")))
_LOCK = threading.Lock()


def _path() -> Path:
    return STATS_FILE.expanduser().resolve()


def _read() -> dict:
    path = _path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("unrecognized"), dict):
            return data
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {"version": 1, "unrecognized": {}}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".unrecognized-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def record_unrecognized(question: str, source: str = "local") -> None:
    """Agrège une question normalisée; aucun chat_id n’est conservé."""
    normalized = normalize_text(question)
    if not normalized:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        data = _read()
        entries = data.setdefault("unrecognized", {})
        entry = entries.setdefault(normalized, {"count": 0, "first_seen": now, "last_seen": now, "sources": {}})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_seen"] = now
        sources = entry.setdefault("sources", {})
        sources[source] = int(sources.get(source, 0)) + 1
        if len(entries) > MAX_ITEMS:
            oldest = sorted(entries.items(), key=lambda item: (item[1].get("last_seen", ""), item[1].get("count", 0)))
            for key, _value in oldest[: len(entries) - MAX_ITEMS]:
                entries.pop(key, None)
        data["updated_at"] = now
        _write(data)


def get_unrecognized_stats(limit: int = 50) -> dict:
    """Retourne les questions agrégées les plus fréquentes, sans données de chat."""
    limit = min(max(1, int(limit)), 200)
    with _LOCK:
        data = _read()
    items = [
        {"question": question, **(value if isinstance(value, dict) else {})}
        for question, value in data.get("unrecognized", {}).items()
    ]
    items.sort(key=lambda item: (-int(item.get("count", 0)), item.get("question", "")))
    return {"count": len(items), "updated_at": data.get("updated_at"), "items": items[:limit]}
