# ---------------------------------------------------------------------------
# portfolio_drive.py — Portfolio du bot sur Google Drive.
#
# RÈGLE BOSS (02/10) : le disque Railway est éphémère — le portfolio vit
# dans le dossier Drive dédié « Komara Bot - Portfolio ».
#   1. Toute photo envoyée par l'ADMIN est uploadée là (handler rag_bot).
#   2. Le bouton 📂 Portfolio liste les images de CE dossier.
# Nécessite le lien Google (/google, scope drive.file).
# ---------------------------------------------------------------------------
from __future__ import annotations

import io
import logging

import requests

from google_link import get_access_token

logger = logging.getLogger("komara.portfolio_drive")

UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
FOLDER_NAME = "Komara Bot - Portfolio"
IMAGE_MIMES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
_folder_id = ""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def get_portfolio_folder_id() -> str:
    """Dossier Drive dédié : trouvé ou créé. '' = Google non lié."""
    global _folder_id
    if _folder_id:
        return _folder_id
    token = get_access_token()
    if not token:
        return ""
    try:
        resp = requests.get(
            FILES_URL, headers=_headers(token),
            params={"q": ("mimeType = 'application/vnd.google-apps.folder' "
                          f"and name = '{FOLDER_NAME}' and trashed = false"),
                    "fields": "files(id,name)"},
            timeout=30,
        )
        files = resp.json().get("files", []) if resp.status_code == 200 else []
        if files:
            _folder_id = files[0]["id"]
        else:
            resp = requests.post(
                FILES_URL, headers=_headers(token),
                json={"name": FOLDER_NAME,
                      "mimeType": "application/vnd.google-apps.folder"},
                timeout=30,
            )
            if resp.status_code in (200, 201):
                _folder_id = resp.json().get("id", "")
    except requests.RequestException:
        logger.warning("Résolution du dossier portfolio impossible", exc_info=True)
    return _folder_id


def upload_image(filename: str, data: bytes) -> str:
    """Upload d'une image de l'admin → dossier portfolio. Renvoie l'id."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = IMAGE_MIMES.get(ext, "image/jpeg")
    token = get_access_token()
    folder = get_portfolio_folder_id()
    if not token:
        return ""
    boundary = "komara_por4719"
    metadata = (
        f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
        f'{{"name": "{filename}", "mimeType": "{mime}"'
        + (f', "parents": ["{folder}"]' if folder else "")
        + '}\r\n'
        f'--{boundary}\r\nContent-Type: {mime}\r\n\r\n'
    ).encode("utf-8")
    body = metadata + data + f"\r\n--{boundary}--".encode("utf-8")
    try:
        resp = requests.post(
            UPLOAD_URL,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={boundary}"},
            data=body, timeout=120,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id", "")
        logger.error("Upload portfolio Drive refusé : %s", resp.text[:200])
    except requests.RequestException:
        logger.warning("Upload portfolio Drive impossible", exc_info=True)
    return ""


def list_images() -> list[dict]:
    """Images du portfolio, de la plus RÉCENTE à la plus ancienne.
    [{id, name, createdTime}, ...] — [] si Google non lié."""
    token = get_access_token()
    folder = get_portfolio_folder_id()
    if not token or not folder:
        return []
    try:
        resp = requests.get(
            FILES_URL, headers=_headers(token),
            params={"q": (f"'{folder}' in parents and trashed = false "
                          "and mimeType contains 'image/'"),
                    "fields": "files(id,name,createdTime)",
                    "orderBy": "createdTime desc", "pageSize": 50},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("files", [])
    except requests.RequestException:
        logger.warning("Liste portfolio Drive impossible", exc_info=True)
    return []


def download_image(file_id: str, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Contenu binaire d'une image du portfolio (pour envoi Telegram).

    PERF (Boss 10/10) : streaming par chunks + PLAFOND mémoire. Avant,
    resp.content chargeait l'image entière d'un coup — 10 clients
    simultanés sur une grosse image = OOM sur Railway (512 Mo). Ici on
    télécharge par morceaux de 64 Ko et on ABANDONNE au-delà du plafond
    (les visuels portfolio sont des images légères ; un fichier plus
    gros est une anomalie, pas un cas normal)."""
    token = get_access_token()
    if not token or not file_id:
        return b""
    try:
        resp = requests.get(f"{FILES_URL}/{file_id}",
                            headers=_headers(token),
                            params={"alt": "media"}, timeout=60,
                            stream=True)
        if resp.status_code == 200:
            buf = io.BytesIO()
            for chunk in resp.iter_content(65536):
                if not chunk:
                    continue
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    logger.warning("Image portfolio %s > %d Ko : abandon",
                                   file_id, max_bytes // 1024)
                    return b""
            return buf.getvalue()
    except requests.RequestException:
        logger.warning("Téléchargement portfolio Drive impossible", exc_info=True)
    return b""
