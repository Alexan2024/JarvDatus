"""Единая точка доступа к задачам.

Два способа подключить TickTick:
  1. TICKTICK_TOKEN  — токен из Настройки → Аккаунт → API Token (проще, рекомендуется)
  2. TICKTICK_CLIENT_ID + SECRET — старая схема OAuth через developer.ticktick.com
Если задан токен, используется он.
"""
from app import config
from app.integrations import ticktick as oauth_backend
from app.integrations import ticktick_mcp as mcp_backend


def backend():
    if mcp_backend.enabled():
        return mcp_backend
    return oauth_backend


def mode() -> str:
    if mcp_backend.enabled():
        return "mcp"
    if config.TICKTICK_ENABLED:
        return "oauth"
    return "off"


def connected() -> bool:
    return backend().connected()


async def open_tasks(limit: int = 15):
    return await backend().open_tasks(limit)


async def create(title: str, due_iso: str = ""):
    return await backend().create(title, due_iso)


async def complete(project_id: str, task_id: str):
    return await backend().complete(project_id, task_id)


async def check():
    return await backend().check()
