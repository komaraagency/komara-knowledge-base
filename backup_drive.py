# ---------------------------------------------------------------------------
# backup_drive.py — Sauvegarde automatique de la base SQLite vers Google Drive
# Nécessite le lien Google (/google) avec le scope drive.file.
# /backup (admin) : sauvegarde immédiate. Planificateur : 03h00 chaque nuit.
# ---------------------------------------------------------------------------

import gzip
import io
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from google_link import get_access_token

KEEP = 7           # nombre de sauvegardes conservées sur Drive
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
PREFIX = "komara-backup-"


def backup_now() -> str:
    """Gzippe la base et l'envoie sur Drive. Retourne le nom du fichier, '' si échec."""
    import actions  # ACTIONS_DB défini après init
    token = get_access_token()
    if not token:
        raise PermissionError("Compte Google non lié — tape /google")
    db_path = actions.ACTIONS_DB
    if not db_path.is_file():
        raise FileNotFoundError("Base SQLite introuvable")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"{PREFIX}{stamp}.db.gz"
    buf = io.BytesIO()
    with db_path.open("rb") as f_in, gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f_out:
        f_out.write(f_in.read())
    content = buf.getvalue()
    boundary = "komara42"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + f'{{"name": "{name}", "mimeType": "application/gzip"}}\r\n'
        + f"--{boundary}\r\nContent-Type: application/gzip\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--".encode()
    resp = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Drive a refusé l'envoi ({resp.status_code})")
    _prune_old(token)
    return name


def _prune_old(token: str) -> None:
    """Ne garde que les KEEP dernières sauvegardes."""
    try:
        resp = requests.get(
            FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": f"name contains '{PREFIX}'", "orderBy": "name desc",
                    "pageSize": 50, "fields": "files(id,name)"},
            timeout=30,
        )
        files = resp.json().get("files", []) if resp.status_code == 200 else []
        for f in files[KEEP:]:
            requests.delete(f"{FILES_URL}/{f['id']}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except requests.RequestException:
        pass


def cmd_backup(bot, chat_id: int, lang: str = "fr") -> None:
    from actions import notify_admin  # noqa: PLC0415
    try:
        name = backup_now()
        bot.send_message(chat_id, f"✅ Sauvegarde envoyée sur Google Drive : {name}")
    except PermissionError as e:
        bot.send_message(chat_id, f"⚠️ {e}")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Sauvegarde échouée : {e}")
        notify_admin(bot, f"⚠️ Backup Drive échoué : {e}")


def _next_3am() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds() + 1


def scheduler_loop() -> None:
    """Thread démon : sauvegarde chaque nuit à 03h00 UTC (heure de Guinée)."""
    import logging
    log = logging.getLogger("komara.backup")
    while True:
        time.sleep(_next_3am())
        try:
            name = backup_now()
            log.info("Backup Drive OK : %s", name)
        except Exception as e:
            log.warning("Backup Drive échoué : %s", e)
