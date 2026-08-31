"""Минимальный MCP-клиент (Streamable HTTP) — ровно столько, сколько нужно боту."""
import json
import logging

import httpx

log = logging.getLogger(__name__)
PROTOCOL = "2025-06-18"


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, url: str, token: str, name: str = "jarvis-bot"):
        self.url = url
        self.token = token
        self.name = name
        self.session_id: str | None = None
        self._ready = False
        self._counter = 0

    # ---------- транспорт ----------

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse_sse(text: str) -> dict:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk:
                continue
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                return obj
        return {}

    async def _post(self, http: httpx.AsyncClient, payload: dict) -> dict | None:
        response = await http.post(self.url, headers=self._headers(), json=payload)
        sid = response.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if response.status_code in (202, 204):
            return None
        if response.status_code == 404 and self.session_id:
            self.session_id, self._ready = None, False
            raise MCPError("сессия истекла")
        if response.status_code in (401, 403):
            raise MCPError("токен отклонён")
        response.raise_for_status()

        if "text/event-stream" in response.headers.get("content-type", ""):
            envelope = self._parse_sse(response.text)
        else:
            envelope = response.json()
        if isinstance(envelope, dict) and envelope.get("error"):
            raise MCPError(str(envelope["error"]))
        if isinstance(envelope, dict):
            return envelope.get("result")
        return None

    async def _rpc(self, http: httpx.AsyncClient, method: str, params: dict) -> dict:
        self._counter += 1
        result = await self._post(http, {
            "jsonrpc": "2.0", "id": self._counter, "method": method, "params": params,
        })
        return result or {}

    # ---------- рукопожатие ----------

    async def connect(self, http: httpx.AsyncClient) -> None:
        if self._ready:
            return
        await self._rpc(http, "initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": self.name, "version": "1.0"},
        })
        await self._post(http, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._ready = True

    # ---------- инструменты ----------

    async def tools(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as http:
            for attempt in (1, 2):
                try:
                    await self.connect(http)
                    result = await self._rpc(http, "tools/list", {})
                    return result.get("tools", [])
                except MCPError:
                    if attempt == 2:
                        raise
                    self._ready, self.session_id = False, None
        return []

    async def call(self, name: str, arguments: dict) -> dict:
        async with httpx.AsyncClient(timeout=45) as http:
            for attempt in (1, 2):
                try:
                    await self.connect(http)
                    return await self._rpc(
                        http, "tools/call", {"name": name, "arguments": arguments}
                    )
                except MCPError:
                    if attempt == 2:
                        raise
                    self._ready, self.session_id = False, None
        return {}


def extract_text(result: dict) -> str:
    """Достаёт текст из ответа tools/call."""
    if not isinstance(result, dict):
        return ""
    parts = []
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    if not parts and result.get("structuredContent"):
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    return "\n".join(parts).strip()


def extract_json(result: dict):
    """Пытается разобрать ответ как JSON; иначе возвращает None."""
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    text = extract_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        if start < 0:
            return None
        for end in (text.rfind("]"), text.rfind("}")):
            if end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None
