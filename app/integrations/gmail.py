"""Gmail через REST API. Токены живут в базе, обновляются автоматически."""
import base64
import time

import httpx

from app import config, db

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/auth/google/callback"


def auth_link(state: str) -> str:
    from urllib.parse import urlencode

    return AUTH_URL + "?" + urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


async def exchange_code(code: str) -> bool:
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(TOKEN_URL, data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        })
    if r.status_code != 200:
        return False
    data = r.json()
    if not data.get("refresh_token"):
        return False
    db.kv_set("google_refresh_token", data["refresh_token"])
    db.kv_set("google_access", {
        "token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 3500) - 60,
    })
    return True


def connected() -> bool:
    return bool(db.kv_get("google_refresh_token"))


async def _access_token() -> str | None:
    cached = db.kv_get("google_access")
    if cached and cached.get("expires_at", 0) > time.time():
        return cached["token"]
    refresh = db.kv_get("google_refresh_token")
    if not refresh:
        return None
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(TOKEN_URL, data={
            "refresh_token": refresh,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
    if r.status_code != 200:
        return None
    data = r.json()
    db.kv_set("google_access", {
        "token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 3500) - 60,
    })
    return data["access_token"]


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


async def recent(hours: int = 24, limit: int = 12) -> list[dict]:
    """Письма из входящих за последние сутки."""
    token = await _access_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    query = f"in:inbox newer_than:{max(1, hours // 24)}d -category:promotions"
    try:
        async with httpx.AsyncClient(timeout=25) as http:
            r = await http.get(f"{API}/messages", headers=headers,
                               params={"q": query, "maxResults": limit})
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("messages", [])]

            letters = []
            for mid in ids:
                d = await http.get(
                    f"{API}/messages/{mid}", headers=headers,
                    params={"format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Date"]},
                )
                if d.status_code != 200:
                    continue
                msg = d.json()
                hs = msg.get("payload", {}).get("headers", [])
                letters.append({
                    "id": mid,
                    "from": _header(hs, "From"),
                    "subject": _header(hs, "Subject"),
                    "snippet": msg.get("snippet", "")[:200],
                    "unread": "UNREAD" in msg.get("labelIds", []),
                })
            return letters
    except Exception:
        return []


async def check() -> tuple[bool, str]:
    if not config.GMAIL_ENABLED:
        return False, "выкл"
    if not connected():
        return False, "нет входа"
    token = await _access_token()
    return (True, "ок") if token else (False, "токен")


async def unread_count() -> int:
    token = await _access_token()
    if not token:
        return 0
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"{API}/labels/UNREAD",
                               headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                return 0
            return int(r.json().get("messagesUnread", 0))
    except Exception:
        return 0
