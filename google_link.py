# ---------------------------------------------------------------------------
# google_link — Lie le compte Google de l'admin au bot via OAuth 2.0
# Google Sheets (commandes/leads/RDV en temps réel) + Google Contacts
# (chaque lead devient un contact). Aucune clé payante, aucune IA externe.
#
# Variables Railway :
#   GOOGLE_CLIENT_ID      — ID client de ton app d'authentification Google
#   GOOGLE_CLIENT_SECRET  — Secret client de l'app
#   GOOGLE_SHEET_ID        — ID du tableur (dans l'URL du Google Sheet)
#   OAUTH_REDIRECT_URI    — (optionnel) si autre URL que le domaine Railway
#   PORT                  — fourni par Railway (serveur de callback OAuth)
# ---------------------------------------------------------------------------

import json
import os
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
REDIRECT_URI = os.getenv(
    "OAUTH_REDIRECT_URI",
    f"https://{PUBLIC_DOMAIN}/oauth/callback" if PUBLIC_DOMAIN else "",
)

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets "
    "https://www.googleapis.com/auth/contacts"
)

API_TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS_APPEND = "https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}:append"
PEOPLE_CREATE = "https://people.googleapis.com/v1/people:createContact"

DB_CONN = None
DB_LOCK = threading.Lock()
_access_token = ""
_token_expiry = 0.0  # timestamp


def ensure_table(conn) -> None:
    global DB_CONN
    DB_CONN = conn
    with DB_LOCK:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS google_state (
                chat_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS google_token (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                refresh_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and REDIRECT_URI)


def is_linked() -> bool:
    if DB_CONN is None:
        return False
    with DB_LOCK:
        row = DB_CONN.execute("SELECT 1 FROM google_token WHERE id = 1").fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# OAuth : URL de consentement
# ---------------------------------------------------------------------------

def build_auth_url(chat_id: int) -> str:
    state = secrets.token_urlsafe(24)
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT INTO google_state (chat_id, state, created_at) VALUES (?,?,?)"
            " ON CONFLICT(chat_id) DO UPDATE SET state = excluded.state,"
            " created_at = excluded.created_at",
            (str(chat_id), state, _now()),
        )
        DB_CONN.commit()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def cmd_google(bot, chat_id: int, lang: str = "fr") -> None:
    """/google (admin) : lance le lien ou montre l'état."""
    if not is_configured():
        bot.send_message(
            chat_id,
            "⚠️ Google non configuré.\n\n"
            "Ajoute dans Railway :\n"
            "• GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET (ton app d'authentification Google)\n"
            "• OAUTH_REDIRECT_URI = https://ton-domaine-railway/oauth/callback\n"
            "   (aussi autorisée dans la console Google → URI de redirection)\n"
            "• GOOGLE_SHEET_ID (l'ID dans l'URL de ton Google Sheet)\n"
            "Puis redéploie et retape /google.",
        )
        return
    if is_linked():
        status = "✅ Ton compte Google est déjà lié (Sheets + Contacts actifs)."
        if GOOGLE_SHEET_ID:
            status += f"\n📊 Tableur : {GOOGLE_SHEET_ID[:12]}…"
        else:
            status += "\n⚠️ GOOGLE_SHEET_ID absent — la sync Sheets est inactive."
        bot.send_message(chat_id, status)
        return
    bot.send_message(
        chat_id,
        "🔗 Liaison Google\n\n1. Ouvre ce lien et autorise l'accès :\n"
        + build_auth_url(chat_id)
        + "\n\n2. Une fois validé, le bot synchronisera automatiquement "
        "commandes, leads et RDV dans ton Google Sheet, et créera un "
        "contact Google pour chaque lead.",
    )


# ---------------------------------------------------------------------------
# OAuth : échange code → refresh token
# ---------------------------------------------------------------------------

def exchange_code(code: str, state: str) -> bool:
    """Callback OAuth : vérifie le state, échange le code, stocke le token."""
    if DB_CONN is None:
        return False
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT state FROM google_state ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row or row[0] != state:
        return False
    resp = requests.post(API_TOKEN_URL, data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=30)
    if resp.status_code != 200:
        return False
    refresh = resp.json().get("refresh_token", "")
    if not refresh:
        return False
    with DB_LOCK:
        DB_CONN.execute(
            "INSERT INTO google_token (id, refresh_token, updated_at) VALUES (1,?,?)"
            " ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token,"
            " updated_at = excluded.updated_at",
            (refresh, _now()),
        )
        DB_CONN.commit()
    return True


def get_access_token() -> str:
    """Access token (rafraîchi automatiquement depuis le refresh token)."""
    global _access_token, _token_expiry
    import time
    if _access_token and time.time() < _token_expiry:
        return _access_token
    if DB_CONN is None:
        return ""
    with DB_LOCK:
        row = DB_CONN.execute("SELECT refresh_token FROM google_token WHERE id = 1").fetchone()
    if not row:
        return ""
    resp = requests.post(API_TOKEN_URL, data={
        "refresh_token": row[0],
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }, timeout=30)
    if resp.status_code != 200:
        return ""
    data = resp.json()
    _access_token = data.get("access_token", "")
    _token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
    return _access_token


# ---------------------------------------------------------------------------
# Sync Sheets + Contacts (silencieuse en cas d'absence de config)
# ---------------------------------------------------------------------------

def sync_sheets(kind: str, row: list) -> bool:
    token = get_access_token()
    if not token or not GOOGLE_SHEET_ID:
        return False
    rng = urllib.parse.quote("Bot!A1")
    url = SHEETS_APPEND.format(sid=GOOGLE_SHEET_ID, rng=rng)
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": [row]},
            timeout=30,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def sync_contact(name: str, phone: str, note: str = "") -> bool:
    token = get_access_token()
    if not token or not name.strip():
        return False
    payload = {"names": [{"givenName": name.strip()[:100]}]}
    if phone.strip():
        payload["phoneNumbers"] = [{"value": phone.strip()[:30], "type": "mobile"}]
    if note:
        payload["biographies"] = [{"value": note[:500], "contentType": "TEXT_PLAIN"}]
    try:
        resp = requests.post(
            PEOPLE_CREATE,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def hook(table: str, fields: dict) -> None:
    """Appelé après chaque INSERT orders/leads/appointments. Jamais bloquant."""
    if not is_linked():
        return
    kind = {"orders": "Commande", "leads": "Lead",
            "appointments": "RDV"}.get(table, "")
    if not kind:
        return
    name = str(fields.get("name", "") or "")
    phone = str(fields.get("phone", "") or "")
    details = str(fields.get("service", "") or fields.get("sector", "")
                  or fields.get("topic", "") or "")
    row = [_now(), kind, name, phone, details[:100]]
    try:
        sync_sheets(kind, row)
    except Exception:
        pass
    if table == "leads" and name:
        try:
            sync_contact(name, phone, f"Lead Komara Agency 🇬🇳 — {details[:100]}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Serveur HTTP : /oauth/callback (port Railway)
# ---------------------------------------------------------------------------

_PAGE_OK = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>Google lié</title></head>"
    "<body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
    "<h2>✅ Ton compte Google est lié au bot !</h2>"
    "<p>Sheets et Contacts sont maintenant actifs.<br>"
    "Tu peux fermer cette page et revenir sur Telegram.</p></body></html>"
)
_PAGE_KO = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>Erreur</title></head>"
    "<body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
    "<h2>❌ Liaison refusée ou expirée</h2>"
    "<p>Retape /google dans Telegram pour réessayer.</p></body></html>"
)


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0]
        error = (qs.get("error") or [""])[0]
        page = _PAGE_KO
        if code and state and not error and exchange_code(code, state):
            page = _PAGE_OK
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence les logs HTTP
        return


def run_oauth_server() -> None:
    """Serveur de callback OAuth (thread démon, port Railway)."""
    if not is_configured():
        return
    port = int(os.getenv("PORT", "8080") or 8080)
    try:
        server = HTTPServer(("0.0.0.0", port), _OAuthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except OSError:
        pass
