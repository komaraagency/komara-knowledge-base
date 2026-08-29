#!/usr/bin/env python3
"""Sauvegarde sûre et rotative de la base locale kb.json.

Usage :
    python scripts/backup_kb.py
    python scripts/backup_kb.py --source kb.json --backup-dir backups/kb --keep 30

Le script valide le JSON avant de créer la copie et écrit d'abord un fichier
.temp avant de le renommer, afin d'éviter une sauvegarde partielle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger("komara.backup")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Sauvegarder kb.json avec rotation.")
    parser.add_argument("--source", type=Path, default=root / "kb.json")
    parser.add_argument("--backup-dir", type=Path, default=root / "backups" / "kb")
    parser.add_argument("--keep", type=int, default=int(os.getenv("BACKUP_KEEP", "30")))
    return parser.parse_args()


def validate_json(source: Path) -> bytes:
    raw = source.read_bytes()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("kb.json doit contenir un objet JSON.")
    if not isinstance(parsed.get("knowledge"), list):
        raise ValueError("kb.json doit contenir une liste 'knowledge'.")
    if len(parsed["knowledge"]) == 0:
        raise ValueError("La base 'knowledge' ne peut pas être vide.")
    return raw


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_backup(source: Path, backup_dir: Path, raw: bytes) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"kb-{timestamp}.json"
    # Évite une collision si deux exécutions ont lieu dans la même seconde.
    if destination.exists():
        destination = backup_dir / f"kb-{timestamp}-{os.getpid()}.json"
    fd, temporary_name = tempfile.mkstemp(prefix=".kb-", suffix=".tmp", dir=backup_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def rotate_backups(backup_dir: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise ValueError("--keep doit être supérieur ou égal à 1.")
    backups = sorted(backup_dir.glob("kb-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = backups[keep:]
    for path in removed:
        path.unlink()
    return removed


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(levelname)s %(message)s")
    args = parse_args()
    source = args.source.expanduser().resolve()
    backup_dir = args.backup_dir.expanduser().resolve()
    if not source.is_file():
        LOGGER.error("Fichier source absent : %s", source)
        return 2
    try:
        raw = validate_json(source)
        destination = create_backup(source, backup_dir, raw)
        removed = rotate_backups(backup_dir, args.keep)
        LOGGER.info("Sauvegarde créée : %s", destination)
        LOGGER.info("Taille : %s octets | SHA-256 : %s", len(raw), sha256(raw))
        if removed:
            LOGGER.info("Anciennes sauvegardes supprimées : %s", len(removed))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Sauvegarde annulée : %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
