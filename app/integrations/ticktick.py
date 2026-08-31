"""TickTick через официальный Open API (OAuth2, scopes tasks:read tasks:write)."""
import time
from urllib.parse import urlencode

import httpx

from app import config, db

AUTH_URL = "https://ticktick.com/oauth/authorize"
TOKEN_URL = "https://ticktick.com/oauth/token"
API = "https://api.ticktick.com/open/v1"
SCOPE = "tasks:write tasks:read"


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/auth/ticktick/callback"


def auth_link(state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": config.TICKTICK_CLIENT_ID,
        "scope": SCOPE,
        "state": state,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
    })


def connected() -> bool:
    return bool(db.kv_get("ticktick_token"))


async def exchange_code(code: str) -> bool:
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(TOKEN_URL, data={
            "client_id": config.TICKTICK_CLIENT_ID,
            "client_secret": config.TICKTICK_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "scope": SCOPE,
            "redirect_uri": redirect_uri(),
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        return False
    data = r.json()
    if not data.get("access_token"):
        return False
    db.kv_set("ticktick_token", {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + data.get("expires_in", 15552000) - 3600,
    })
    return True


async def _token() -> str | None:
    tok = db.kv_get("ticktick_token")
    if not tok:
        return None
    if tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    if not tok.get("refresh_token"):
        return tok["access_token"]
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(TOKEN_URL, data={
            "client_id": config.TICKTICK_CLIENT_ID,
            "client_secret": config.TICKTICK_CLIENT_SECRET,
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
            "scope": SCOPE,
        })
    if r.status_code != 200:
        return tok["access_token"]
    data = r.json()
    db.kv_set("ticktick_token", {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", tok["refresh_token"]),
        "expires_at": time.time() + data.get("expires_in", 15552000) - 3600,
    })
    return data["access_token"]


async def _headers() -> dict | None:
    token = await _token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def projects() -> list[dict]:
    h = await _headers()
    if not h:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(f"{API}/project", headers=h)
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def default_project_id() -> str:
    """Ищем (или подсказываем создать) рабочий список."""
    cached = db.kv_get("ticktick_project_id")
    if cached:
        return cached
    for p in await projects():
        if p.get("name", "").strip().lower() == config.TICKTICK_PROJECT_NAME.lower():
            db.kv_set("ticktick_project_id", p["id"])
            return p["id"]
    all_projects = await projects()
    if all_projects:
        db.kv_set("ticktick_project_id", all_projects[0]["id"])
        return all_projects[0]["id"]
    return ""


async def open_tasks(limit: int = 15) -> list[dict]:
    """Незакрытые задачи из всех списков."""
    h = await _headers()
    if not h:
        return []
    out = []
    try:
        async with httpx.AsyncClient(timeout=25) as http:
            for p in await projects():
                r = await http.get(f"{API}/project/{p['id']}/data", headers=h)
                if r.status_code != 200:
                    continue
                for t in r.json().get("tasks", []):
                    if t.get("status", 0) == 0:
                        out.append({
                            "id": t["id"],
                            "project_id": p["id"],
                            "title": t.get("title", ""),
                            "due": t.get("dueDate", ""),
                        })
                if len(out) >= limit:
                    break
    except Exception:
        return out
    return out[:limit]


async def create(title: str, due_iso: str = "") -> dict:
    h = await _headers()
    if not h:
        return {}
    project_id = await default_project_id()
    payload = {"title": title}
    if project_id:
        payload["projectId"] = project_id
    if due_iso:
        payload["dueDate"] = due_iso
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(f"{API}/task", headers=h, json=payload)
            if r.status_code not in (200, 201):
                return {}
            data = r.json()
            return {"id": data.get("id", ""),
                    "project_id": data.get("projectId", project_id)}
    except Exception:
        return {}


async def complete(project_id: str, task_id: str) -> bool:
    h = await _headers()
    if not h or not task_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                f"{API}/project/{project_id}/task/{task_id}/complete", headers=h
            )
            return r.status_code in (200, 201, 204)
    except Exception:
        return False


async def check() -> tuple[bool, str]:
    if not config.TICKTICK_ENABLED:
        return False, "выкл"
    if not connected():
        return False, "нет входа"
    return (True, "ок") if await _headers() else (False, "токен")
