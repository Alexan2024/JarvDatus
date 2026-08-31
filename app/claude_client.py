"""Обёртка над Claude API: стриминг, поиск в интернете, разбор в JSON."""
import json
import re
from datetime import date

from anthropic import AsyncAnthropic

from app import config, db

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

PERSONA = """Ты — личный ассистент. Отвечаешь по-русски.

Характер: сдержанный, суховато-ироничный, в духе британского дворецкого.
Обращаешься на «вы», иногда «сэр». Без подхалимства и восторгов.

Правила:
- Кратко. Никакой воды, никаких вступлений вроде «Конечно!» и «Отличный вопрос».
- Если не знаешь — ищи в интернете, у тебя есть инструмент поиска.
- Не выдумывай фактов. Не уверен — так и скажи.
- Форматирование обычным текстом, без markdown-заголовков.
- Ирония допустима, но короткая и редкая. Ты полезен в первую очередь."""

PLAIN = """Ты — ассистент. Отвечаешь по-русски, кратко и по делу."""


def system_prompt() -> str:
    tone = db.kv_get("tone", "jarvis")
    base = PERSONA if tone == "jarvis" else PLAIN
    known = db.facts()
    if known:
        base += "\n\nЧто ты знаешь о владельце:\n" + "\n".join(f"- {f}" for f in known)
    base += f"\n\nСегодня {date.today().isoformat()}, таймзона {config.TZ_NAME}."
    return base


def _track(usage) -> None:
    if not usage:
        return
    spent = db.kv_get("spent_today", {"day": "", "usd": 0.0})
    today = date.today().isoformat()
    if spent.get("day") != today:
        spent = {"day": today, "usd": 0.0}
    cost = (
        getattr(usage, "input_tokens", 0) / 1_000_000 * config.PRICE_IN
        + getattr(usage, "output_tokens", 0) / 1_000_000 * config.PRICE_OUT
    )
    spent["usd"] = round(spent.get("usd", 0.0) + cost, 4)
    db.kv_set("spent_today", spent)


async def stream_reply(user_text: str):
    """Отдаёт куски текста по мере генерации. Поиск в интернете включён."""
    messages = db.history(20) + [{"role": "user", "content": user_text}]
    text = ""
    async with client.messages.stream(
        model=config.MODEL,
        max_tokens=2000,
        system=system_prompt(),
        messages=messages,
        tools=[WEB_SEARCH_TOOL],
    ) as stream:
        async for chunk in stream.text_stream:
            text += chunk
            yield text
        final = await stream.get_final_message()
        _track(final.usage)
    db.add_message("user", user_text)
    db.add_message("assistant", text or "(пусто)")


async def ask(prompt: str, system: str | None = None, search: bool = False,
              max_tokens: int = 1500) -> str:
    kwargs = {
        "model": config.MODEL,
        "max_tokens": max_tokens,
        "system": system or system_prompt(),
        "messages": [{"role": "user", "content": prompt}],
    }
    if search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]
    resp = await client.messages.create(**kwargs)
    _track(resp.usage)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


async def ask_json(prompt: str, system: str = "", fallback=None):
    """Просим чистый JSON и аккуратно его достаём."""
    sys = (system or "Ты — парсер данных.") + (
        "\n\nОтвечай ТОЛЬКО валидным JSON без пояснений, без markdown-ограждений."
    )
    raw = (await ask(prompt, system=sys, max_tokens=2000)).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return fallback
