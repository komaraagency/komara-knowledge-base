# ---------------------------------------------------------------------------
# backup_drive.py — Sauvegarde automatique de la base SQLite vers Google Drive
# Nécessite le lien Google (/google) avec le scope drive.file.
# /backup (admin) : sauvegarde immédiate. Planificateur : 02h00 chaque nuit.
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
FOLDER_NAME = "Komara Backups"
_folder_id = ""    # cache en mémoire du dossier dédié


def get_folder_id(token: str) -> str:
    """Dossier Drive dédié « Komara Backups » : trouvé ou créé. '' = racine."""
    global _folder_id
    if _folder_id:
        return _folder_id
    try:
        resp = requests.get(
            FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q": ("mimeType = 'application/vnd.google-apps-folder' "
                      f"and name = '{FOLDER_NAME}' and trashed = false"),
                "fields": "files(id,name)",
            },
            timeout=30,
        )
        files = resp.json().get("files", []) if resp.status_code == 200 else []
        if files:
            _folder_id = files[0]["id"]
        else:
            resp = requests.post(
                FILES_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"name": FOLDER_NAME,
                      "mimeType": "application/vnd.google-apps.folder"},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                _folder_id = resp.json().get("id", "")
    except requests.RequestException:
        pass
    return _folder_id


def move_root_backups(token: str) -> int:
    """Range les anciens backups de la racine vers le dossier dédié."""
    fid = get_folder_id(token)
    if not fid:
        return 0
    try:
        resp = requests.get(
            FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": (f"name contains '{PREFIX}' and 'root' in parents "
                          "and trashed = false"),
                    "fields": "files(id,name)", "pageSize": 50},
            timeout=30,
        )
        files = resp.json().get("files", []) if resp.status_code == 200 else []
    except requests.RequestException:
        return 0
    moved = 0
    for f in files:
        try:
            r = requests.patch(
                f"{FILES_URL}/{f['id']}",
                headers={"Authorization": f"Bearer {token}"},
                params={"addParents": fid},
                timeout=30,
            )
            moved += 1 if r.status_code in (200, 204) else 0
        except requests.RequestException:
            pass
    return moved


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
    fid = get_folder_id(token)
    boundary = "komara42"
    meta = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + '{"name": "' + name + '", "mimeType": "application/gzip"'
        + (f', "parents": ["{fid}"]' if fid else "")
        + "}\r\n"
        + f"--{boundary}\r\nContent-Type: application/gzip\r\n\r\n"
    ).encode()
    body = meta + content + f"\r\n--{boundary}--".encode()
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
    move_root_backups(token)
    _prune_old(token)
    return name


def _prune_old(token: str) -> None:
    """Ne garde que les KEEP dernières sauvegardes (dans le dossier dédié)."""
    fid = get_folder_id(token)
    try:
        resp = requests.get(
            FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": ("name contains '" + PREFIX + "' "
                          + (f"and '{fid}' in parents " if fid else "")
                          + "and trashed = false"),
                    "orderBy": "name desc",
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
        fid = _folder_id or ""
        link = ("\n\n📁 Dossier : https://drive.google.com/drive/folders/" + fid) if fid else ""
        bot.send_message(chat_id,
                         f"✅ Sauvegarde envoyée dans « {FOLDER_NAME} » : {name}{link}")
    except PermissionError as e:
        bot.send_message(chat_id, f"⚠️ {e}")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Sauvegarde échouée : {e}")
        notify_admin(bot, f"⚠️ Backup Drive échoué : {e}")


def _next_2am() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds() + 1


def scheduler_loop() -> None:
    """Thread démon : sauvegarde chaque nuit à 02h00 UTC (heure de Guinée)."""
    import logging
    log = logging.getLogger("komara.backup")
    while True:
        time.sleep(_next_2am())
        try:
            name = backup_now()
            log.info("Backup Drive OK : %s", name)
        except Exception as e:
            log.warning("Backup Drive échoué : %s", e)
