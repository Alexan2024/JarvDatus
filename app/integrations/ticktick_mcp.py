"""TickTick через официальный MCP-сервер (токен из Настройки → Аккаунт → API Token).

Имена инструментов на сервере могут меняться, поэтому бот не зашивает их
жёстко, а находит подходящие сам по списку tools/list.
"""
import logging
import re
import time

from app import config, db
from app.integrations.mcp_client import MCPClient, MCPError, extract_json, extract_text

log = logging.getLogger(__name__)

URL = "https://mcp.ticktick.com"
_client: MCPClient | None = None
_tools: list[dict] = []
_loaded_at = 0.0


def enabled() -> bool:
    return bool(config.TICKTICK_TOKEN)


def connected() -> bool:
    return enabled()


def _get_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient(URL, config.TICKTICK_TOKEN, name="jarvis-bot")
    return _client


async def tool_list(force: bool = False) -> list[dict]:
    global _tools, _loaded_at
    if _tools and not force and time.time() - _loaded_at < 21600:
        return _tools
    _tools = await _get_client().tools()
    _loaded_at = time.time()
    log.info("TickTick MCP: инструментов %s", len(_tools))
    return _tools


# ---------- подбор инструмента по смыслу ----------

def _properties(tool: dict) -> dict:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _words(name: str) -> list[str]:
    """create_task -> [create, task]; getProjectTasks -> [get, project, tasks]."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if w]


def _has_verb(name: str, verbs: list[str]) -> bool:
    """Сравниваем по целым словам: «undone» не считается за «done»."""
    parts = _words(name)
    return any(word == verb or word == verb + "s" for word in parts for verb in verbs)


def _pick(tools: list[dict], must: list[str], verbs: list[str],
          avoid: list[str] | None = None) -> dict | None:
    avoid = avoid or []
    best = None
    for tool in tools:
        raw = tool.get("name") or ""
        name = raw.lower()
        text = name + " " + (tool.get("description") or "").lower()
        parts = _words(raw)          # разбор до приведения к нижнему регистру
        if any(bad in parts for bad in avoid):
            continue
        if not all(word in text for word in must):
            continue
        if not _has_verb(raw, verbs):
            continue
        if all(word in name for word in must):
            return tool
        best = best or tool
    return best


def _key(props: dict, candidates: list[str]) -> str:
    lower = {k.lower(): k for k in props}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return ""


TITLE_KEYS = ["title", "name", "taskTitle", "summary", "content"]
PROJECT_KEYS = ["projectId", "project_id", "listId", "list_id",
                "projectName", "project_name", "listName", "list_name",
                "project", "list"]
TASK_ID_KEYS = ["taskId", "task_id", "id"]


# ---------- разбор ответов ----------

KNOWN_LIST_KEYS = ("undoneTasks", "undone_tasks", "tasks", "items", "data",
                   "results", "projects", "lists", "children")


def _as_items(payload, prefer: str = "") -> list[dict]:
    """Достаёт список словарей из ответа любой вложенности."""
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if not isinstance(payload, dict):
        return []

    if prefer:
        for key, value in payload.items():
            if prefer in key.lower() and isinstance(value, list):
                found = [i for i in value if isinstance(i, dict)]
                if found:
                    return found

    for key in KNOWN_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            found = [i for i in value if isinstance(i, dict)]
            if found:
                return found

    # рекурсивно ищем первый подходящий список глубже
    for value in payload.values():
        if isinstance(value, (dict, list)):
            found = _as_items(value, prefer)
            if found:
                return found

    if payload.get("id") or payload.get("title") or payload.get("name"):
        return [payload]
    return []


def _title_of(item: dict) -> str:
    for key in ("title", "name", "content", "summary"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _id_of(item: dict, keys: list[str]) -> str:
    for key in keys:
        for variant in (key, key.lower(), key.replace("_", "")):
            for actual in item:
                if actual.lower().replace("_", "") == variant.lower().replace("_", ""):
                    value = item[actual]
                    if isinstance(value, (str, int)) and str(value).strip():
                        return str(value)
    return ""


def _is_done(item: dict) -> bool:
    status = item.get("status")
    if isinstance(status, (int, float)):
        return status != 0
    if isinstance(status, str):
        return status.lower() in ("completed", "done", "complete", "2")
    for key in ("completed", "isCompleted", "completedTime"):
        if item.get(key):
            return True
    return False


# ---------- публичные операции ----------

def _projects_tool(tools: list[dict]) -> dict | None:
    return _pick(tools, ["project"], ["list", "get", "query", "fetch", "all"],
                 avoid=["create", "add", "update", "delete", "task", "member",
                        "by_id", "byid"]) \
        or _pick(tools, ["list"], ["get", "query", "fetch", "all"],
                 avoid=["create", "add", "update", "delete", "task"])


def _tasks_tool(tools: list[dict]) -> dict | None:
    """Сначала ищем «незакрытые задачи проекта», потом любой инструмент чтения."""
    avoid = ["create", "add", "update", "delete", "move",
             "assign", "unassign", "member"]
    for word in ("undone", "uncompleted", "incomplete"):
        found = _pick(tools, [word], ["get", "list", "query", "fetch"], avoid=avoid)
        if found:
            return found
    return _pick(tools, ["task"], ["get", "list", "query", "search", "fetch", "find"],
                 avoid=avoid)


_projects_cache: list[dict] = []
_projects_at = 0.0


async def projects(force: bool = False) -> list[dict]:
    global _projects_cache, _projects_at
    if _projects_cache and not force and time.time() - _projects_at < 900:
        return _projects_cache
    tools = await tool_list()
    tool = _projects_tool(tools)
    if not tool:
        return []
    items = _as_items(extract_json(await _get_client().call(tool["name"], {})),
                      prefer="project")
    out = []
    for item in items:
        pid = _id_of(item, ["id", "projectId", "listId"])
        if pid:
            out.append({"id": pid, "name": _title_of(item)})
    if out:
        _projects_cache, _projects_at = out, time.time()
    return out


async def open_tasks(limit: int = 15) -> list[dict]:
    if not enabled():
        return []
    try:
        tools = await tool_list()
        tool = _tasks_tool(tools)
        if not tool:
            log.warning("TickTick MCP: не нашёл инструмент чтения задач")
            return []
        props = _properties(tool)
        project_key = _key(props, PROJECT_KEYS)

        targets: list[dict] = [{"id": "", "name": ""}]
        if project_key:
            found = await projects()
            if not found:
                log.warning("TickTick MCP: список проектов пуст")
                return []
            preferred = config.TICKTICK_PROJECT_NAME.strip().lower()
            found.sort(key=lambda p: p["name"].strip().lower() != preferred)
            targets = found[:8]

        out: list[dict] = []
        for target in targets:
            if len(out) >= limit:
                break
            args = {}
            if project_key and target["id"]:
                args[project_key] = target["id"]
            status_key = _key(props, ["status", "completed", "filter"])
            if status_key and status_key.lower() == "status":
                args[status_key] = "notCompleted" if "string" in str(
                    props.get(status_key, {}).get("type", "")) else 0
            try:
                result = await _get_client().call(tool["name"], args)
            except Exception as exc:
                log.warning("TickTick MCP: %s(%s) -> %s", tool["name"], args, exc)
                continue
            if result.get("isError"):
                log.warning("TickTick MCP: %s вернул ошибку: %s",
                            tool["name"], extract_text(result)[:200])
                continue
            items = _as_items(extract_json(result), prefer="task")
            if not items:
                items = await _rescue(extract_text(result))
            for item in items:
                if _is_done(item):
                    continue
                title = _title_of(item)
                if not title:
                    continue
                out.append({
                    "id": _id_of(item, TASK_ID_KEYS),
                    "project_id": _id_of(item, PROJECT_KEYS) or target["id"],
                    "title": title,
                    "due": str(item.get("dueDate") or item.get("due_date") or ""),
                })
        return out[:limit]
    except Exception as exc:
        log.warning("TickTick MCP: чтение задач не удалось: %s", exc)
        return []


async def _rescue(text: str) -> list[dict]:
    """Если сервер ответил не JSON, а человеческим текстом — просим Claude разобрать."""
    if not text or len(text) > 12000:
        return []
    from app import claude_client

    parsed = await claude_client.ask_json(
        f"Ответ сервиса задач:\n{text}\n\n"
        'Верни JSON: {"tasks":[{"title":"...","id":"","projectId":""}]} — '
        "только незавершённые задачи. Если задач нет, верни пустой список.",
        system="Ты извлекаешь структурированные данные из текста.",
        fallback=None,
    )
    return _as_items(parsed if isinstance(parsed, (list, dict)) else None)


async def project_id_by_name(name: str) -> str:
    cached = db.kv_get("ticktick_project_id")
    if cached:
        return cached
    try:
        for project in await projects():
            if project["name"].strip().lower() == name.strip().lower():
                db.kv_set("ticktick_project_id", project["id"])
                return project["id"]
    except Exception as exc:
        log.warning("TickTick MCP: список проектов не получен: %s", exc)
    return ""


async def create(title: str, due_iso: str = "") -> dict:
    if not enabled():
        return {}
    try:
        tools = await tool_list()
        tool = _pick(tools, ["task"], ["create", "add", "new"])
        if not tool:
            log.warning("TickTick MCP: не нашёл инструмент создания задач")
            return {}
        props = _properties(tool)
        args = {}
        title_key = _key(props, TITLE_KEYS) or "title"
        args[title_key] = title

        project_key = _key(props, PROJECT_KEYS)
        if project_key:
            if project_key.lower().replace("_", "") in (
                    "project", "list", "projectname", "listname"):
                args[project_key] = config.TICKTICK_PROJECT_NAME
            else:
                pid = await project_id_by_name(config.TICKTICK_PROJECT_NAME)
                if pid:
                    args[project_key] = pid

        if due_iso:
            due_key = _key(props, ["dueDate", "due_date", "due", "dueDateTime", "date"])
            if due_key:
                args[due_key] = due_iso

        result = await _get_client().call(tool["name"], args)
        if result.get("isError"):
            log.warning("TickTick MCP: создание вернуло ошибку: %s", extract_text(result))
            return {}
        created = _as_items(extract_json(result), prefer="task")
        if created:
            return {"id": _id_of(created[0], TASK_ID_KEYS),
                    "project_id": _id_of(created[0], PROJECT_KEYS)}
        return {"id": "", "project_id": ""}
    except Exception as exc:
        log.warning("TickTick MCP: создание задачи не удалось: %s", exc)
        return {}


async def complete(project_id: str, task_id: str) -> bool:
    if not enabled() or not task_id:
        return False
    try:
        tools = await tool_list()
        tool = _pick(tools, ["task"], ["complete", "finish", "done"],
                     avoid=["undone", "uncompleted", "incomplete", "get", "list"]) \
            or _pick(tools, ["task"], ["update"], avoid=["undone"])
        if not tool:
            return False
        props = _properties(tool)
        args = {}
        task_key = _key(props, TASK_ID_KEYS)
        if task_key:
            args[task_key] = task_id
        project_key = _key(props, PROJECT_KEYS)
        if project_key and project_id:
            args[project_key] = project_id
        status_key = _key(props, ["status", "completed", "isCompleted"])
        if status_key and "complete" not in tool["name"].lower():
            args[status_key] = True if status_key.lower() != "status" else "completed"
        result = await _get_client().call(tool["name"], args)
        return not result.get("isError")
    except Exception as exc:
        log.warning("TickTick MCP: закрытие задачи не удалось: %s", exc)
        return False


async def check() -> tuple[bool, str]:
    if not enabled():
        return False, "выкл"
    try:
        tools = await tool_list(force=True)
        return (True, f"{len(tools)} инстр.") if tools else (False, "пусто")
    except MCPError as exc:
        return False, str(exc)[:12]
    except Exception:
        return False, "ошибка"
