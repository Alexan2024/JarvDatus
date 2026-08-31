"""Обёртка над Claude API: стриминг, поиск в интернете, разбор в JSON."""
import json
import re
from datetime import date

from anthropic import AsyncAnthropic

from app import config, db

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

# Действия бота, которые Claude может запустить сам, поняв просьбу своими словами.
ACTION_TOOLS = [
    {
        "name": "show_brief",
        "description": (
            "Показать сводку дня: погода, календарь, почта, новости, "
            "незакрытые задачи. Вызывай, когда просят сводку, бриф, "
            "«что сегодня», «что нового», «как день выглядит»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "plan_day",
        "description": (
            "Начать составление плана на день: бот спросит, что человек "
            "собирается делать. Вызывай на «давай спланируем день», "
            "«составь план», «надо распланировать»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "show_plan",
        "description": (
            "Показать уже составленный план на сегодня. Вызывай на "
            "«что у меня в плане», «покажи список дел», «что мне сегодня делать»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_debrief",
        "description": (
            "Начать вечерний разбор дня: бот покажет утренний список и спросит, "
            "что закрыто. Вызывай на «подведём итоги», «давай разберём день», "
            "«отчитаюсь за день»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_task",
        "description": (
            "Создать задачу в TickTick и добавить её в план на сегодня. "
            "Вызывай на «добавь задачу…», «запиши в дела…», «напомни сделать…». "
            "НЕ вызывай, если человек рассказывает о планах на весь день — "
            "для этого есть plan_day."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Короткая формулировка задачи, глагол в инфинитиве",
                }
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_tasks",
        "description": (
            "Отметить задачи выполненными: закрыть их в TickTick и в плане дня. "
            "Вызывай на «отметь X как сделанное», «закрой задачу X», "
            "«я сделал X и Y», «первую и третью выполнил». "
            "Названия бери из списка дел на сегодня, который дан тебе ниже; "
            "если человек назвал номера — подставь соответствующие названия."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Названия задач, которые нужно закрыть",
                }
            },
            "required": ["titles"],
        },
    },
    {
        "name": "list_tasks",
        "description": (
            "Показать незакрытые задачи из TickTick. Вызывай на "
            "«что у меня в задачах», «покажи тикток», «что висит»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tasks_overview",
        "description": (
            "Полный разбор задач: сколько в каком списке, что просрочено, "
            "как распределено по папкам. Вызывай на «покажи задачи по проектам», "
            "«что у меня по спискам», «разбери задачи», «что просрочено»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "show_status",
        "description": (
            "Сводка систем: время, непрочитанная почта, активные задачи, "
            "ближайшая встреча, расход API. Вызывай на «статус», «как дела у тебя», "
            "«сколько я потратил», «когда следующая встреча»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_diagnostics",
        "description": (
            "Проверить подключения: почта, задачи, календарь, Claude, диск. "
            "Вызывай на «всё ли работает», «проверь подключения», «диагностика»."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "remember_fact",
        "description": (
            "Запомнить факт о владельце навсегда. Вызывай на «запомни, что…», "
            "«имей в виду, что…». Факты потом подмешиваются в каждый разговор."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "Факт одной фразой"}
            },
            "required": ["fact"],
        },
    },
]

ROUTING_RULES = """
У тебя есть инструменты-действия (show_brief, plan_day, add_task и другие).
Когда просьба человека соответствует действию — вызывай инструмент и НЕ пиши
ничего лишнего: бот сам покажет результат в своём оформлении.
Если человек просто спрашивает или беседует — отвечай текстом, инструменты
действий не трогай. Для фактических вопросов о мире пользуйся поиском."""

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


def _integrations_note() -> str:
    """Что подключено — чтобы модель не выдумывала, будто доступа нет."""
    from app.integrations import gmail, tasks

    rows = [
        ("почта Gmail", gmail.connected()),
        ("задачи TickTick", tasks.connected()),
        ("календарь Apple", bool(config.CALENDAR_ENABLED)),
    ]
    lines = [f"- {name}: {'подключено' if ok else 'не подключено'}"
             for name, ok in rows]
    return (
        "\n\nТвои подключения (это факт, не додумывай обратное):\n"
        + "\n".join(lines)
        + "\nЕсли что-то подключено — ты можешь этим пользоваться через свои "
          "инструменты. Никогда не говори, что доступа нет, если выше указано "
          "«подключено»."
    )


def _today_tasks_note() -> str:
    """Текущий план дня — чтобы «отметь вторую» работало."""
    items = [i for i in db.get_plan(date.today().isoformat())
             if i["kind"] != "event" and i["status"] in ("open", "partial")]
    if not items:
        return ""
    listing = "\n".join(f"{n}. {i['title']}" for n, i in enumerate(items, 1))
    return f"\n\nНезакрытые дела на сегодня:\n{listing}"


def system_prompt() -> str:
    tone = db.kv_get("tone", "jarvis")
    base = PERSONA if tone == "jarvis" else PLAIN
    known = db.facts()
    if known:
        base += "\n\nЧто ты знаешь о владельце:\n" + "\n".join(f"- {f}" for f in known)
    base += f"\n\nСегодня {date.today().isoformat()}, таймзона {config.TZ_NAME}."
    base += _integrations_note()
    base += _today_tasks_note()
    base += "\n" + ROUTING_RULES
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
    """Отдаёт ("text", кусок) по мере генерации и ("actions", список) в конце."""
    messages = db.history(20) + [{"role": "user", "content": user_text}]
    text = ""
    actions: list[dict] = []
    async with client.messages.stream(
        model=config.MODEL,
        max_tokens=2000,
        system=system_prompt(),
        messages=messages,
        tools=[WEB_SEARCH_TOOL] + ACTION_TOOLS,
    ) as stream:
        async for chunk in stream.text_stream:
            text += chunk
            yield "text", text
        final = await stream.get_final_message()
        _track(final.usage)
        known = {t["name"] for t in ACTION_TOOLS}
        for block in final.content:
            if getattr(block, "type", "") == "tool_use" and block.name in known:
                actions.append({"name": block.name,
                                "input": dict(block.input or {})})

    db.add_message("user", user_text)
    if actions:
        names = ", ".join(a["name"] for a in actions)
        db.add_message("assistant", text or f"(выполнено действие: {names})")
    else:
        db.add_message("assistant", text or "(пусто)")
    yield "actions", actions


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
