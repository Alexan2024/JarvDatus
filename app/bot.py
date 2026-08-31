"""Телеграм-бот: свободная речь, кнопки, команды — три входа к одним действиям."""
import difflib
import html
import time
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from app import claude_client, config, db, planning, render
from app.integrations import calendar, gmail, tasks, ticktick

bot = Bot(token=config.TELEGRAM_TOKEN)
dp = Dispatcher()
STARTED_AT = time.time()

BTN_BRIEF, BTN_PLAN = "Сводка", "План"
BTN_DEBRIEF, BTN_STATUS = "Итоги", "Статус"

KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_BRIEF), KeyboardButton(text=BTN_PLAN)],
        [KeyboardButton(text=BTN_DEBRIEF), KeyboardButton(text=BTN_STATUS)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Пишите как есть — разберу",
)


def mono(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


async def send_block(text: str) -> None:
    await bot.send_message(config.OWNER_ID, mono(text), parse_mode=ParseMode.HTML)


async def send_html(html_text: str) -> None:
    await bot.send_message(config.OWNER_ID, html_text, parse_mode=ParseMode.HTML,
                           disable_web_page_preview=True)


async def send_text(text: str) -> None:
    await bot.send_message(config.OWNER_ID, html.escape(text),
                           parse_mode=ParseMode.HTML)


async def reply_block(message: Message, text: str) -> None:
    await message.answer(mono(text), parse_mode=ParseMode.HTML)


async def reply_html(message: Message, html_text: str) -> None:
    """Готовая разметка: блок в <pre> плюс кликабельные ссылки под ним."""
    await message.answer(html_text, parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True)


def mine(message: Message) -> bool:
    return config.OWNER_ID and message.from_user.id == config.OWNER_ID


# ======================= действия =======================
# Каждое действие вызывается из трёх мест: команда, кнопка, свободная речь.

async def act_brief(message: Message) -> None:
    await message.answer("Собираю…")
    html_text, _ = await planning.build_morning()
    await reply_html(message, html_text)


async def act_plan_day(message: Message) -> None:
    _, question = await planning.build_morning()
    planning.set_mode("await_plan")
    await reply_block(message, question)


async def act_show_plan(message: Message) -> None:
    items = db.get_plan(planning.today_key())
    if not items:
        return await message.answer(
            "План на сегодня ещё не составлен. Скажите «давай спланируем день»."
        )
    await reply_block(message, render.plan(date.today(), items, False))


async def act_debrief(message: Message) -> None:
    text = await planning.build_evening()
    if not text:
        return await message.answer("На сегодня открытых задач нет, сэр.")
    await reply_block(message, text)


async def act_status(message: Message) -> None:
    now = datetime.now(config.TZ)
    plan_items = db.get_plan(date.today().isoformat())
    active = sum(1 for i in plan_items if i["kind"] != "event" and i["status"] == "open")

    unread = await gmail.unread_count() if gmail.connected() else 0
    events = await calendar.today()
    next_ev = next((e for e in events if e["start"] > now), None)
    if next_ev:
        mins = int((next_ev["start"] - now).total_seconds() // 60)
        next_line = f"{mins} мин" if mins < 600 else next_ev["time"]
    else:
        next_line = "нет"

    spent = db.kv_get("spent_today", {"usd": 0})
    up = int(time.time() - STARTED_AT)
    uptime = f"{up // 86400}д {up % 86400 // 3600}ч" if up >= 86400 else \
             f"{up // 3600}ч {up % 3600 // 60}м"

    await reply_block(message, render.status([
        ("время", now.strftime("%H:%M")),
        ("почта", f"{unread} непроч." if unread else "чисто"),
        ("задачи", f"{active} активных"),
        ("до встречи", next_line),
        ("расход API", f"${spent.get('usd', 0):.2f}"),
        ("аптайм", uptime),
    ]))


async def act_diagnostics(message: Message) -> None:
    checks = []
    gm_ok, gm = await gmail.check()
    checks.append(("почта", gm_ok, gm))
    tt_ok, tt = await tasks.check()
    checks.append(("задачи", tt_ok, tt))
    cal_ok, cal = await calendar.check()
    checks.append(("календарь", cal_ok, cal))
    try:
        await claude_client.ask("Ответь одним словом: работает",
                                system="Отвечай одним словом.", max_tokens=10)
        checks.append(("Claude", True, "ок"))
    except Exception:
        checks.append(("Claude", False, "ошибка"))
    try:
        db.kv_set("_diag", time.time())
        checks.append(("диск", True, "ок"))
    except Exception:
        checks.append(("диск", False, "ошибка"))
    await reply_block(message, render.diagnostics(checks))


async def act_add_task(message: Message, title: str = "") -> None:
    title = (title or "").strip()[:40]
    if not title:
        return await message.answer("Что записать, сэр?")
    created = {}
    if tasks.connected():
        created = await tasks.create(title)
    day = planning.today_key()
    items = db.get_plan(day)
    items.append({
        "kind": "task", "title": title, "note": "", "status": "open",
        "ticktick_id": created.get("id", ""),
        "ticktick_project": created.get("project_id", ""),
    })
    db.save_plan(day, items)
    mark = "✓ записано в TickTick" if created.get("id") else "✓ записано"
    await reply_block(message, render.note(f"{mark}: {render.cut(title, 20)}"))


def _norm(text: str) -> list[str]:
    lowered = str(text).lower().replace("ё", "е").replace("й", "и")
    cleaned = "".join(c if c.isalnum() else " " for c in lowered)
    return [w for w in cleaned.split() if len(w) > 2]


def _similar(a: str, b: str) -> float:
    """Сходство названий: сначала по словам, потом по буквам."""
    wa, wb = _norm(a), _norm(b)
    if not wa or not wb:
        return 0.0
    sa, sb = " ".join(wa), " ".join(wb)
    if sa == sb or sa in sb or sb in sa:
        return 0.95

    # общее значимое слово (с поправкой на окончания): «отчёт» ≈ «отчёта»
    hits = 0
    for x in wa:
        for y in wb:
            if x == y or (len(x) >= 4 and len(y) >= 4
                          and (x.startswith(y[:4]) or y.startswith(x[:4]))):
                hits += 1
                break
    if hits:
        overlap = hits / min(len(wa), len(wb))
        if overlap >= 0.5:
            return 0.75 + 0.2 * overlap

    return difflib.SequenceMatcher(None, sa, sb).ratio() * 0.7


async def _candidates() -> list[dict]:
    """Незакрытые дела: из плана дня и напрямую из TickTick."""
    out = []
    for item in db.get_plan(planning.today_key()):
        if item["kind"] == "event" or item["status"] not in ("open", "partial"):
            continue
        out.append({
            "title": item["title"], "plan_id": item["id"],
            "tt_id": item.get("ticktick_id", ""),
            "tt_project": item.get("ticktick_project", ""),
        })
    if tasks.connected():
        seen = {c["title"].strip().lower() for c in out}
        try:
            for t in await tasks.open_tasks():
                if t["title"].strip().lower() in seen:
                    continue
                out.append({"title": t["title"], "plan_id": None,
                            "tt_id": t["id"], "tt_project": t["project_id"]})
        except Exception:
            pass
    return out


async def act_complete_tasks(message: Message, titles=None) -> None:
    wanted = [str(t) for t in (titles or []) if str(t).strip()]
    if not wanted:
        return await message.answer("Что именно отметить, сэр?")

    pool = await _candidates()
    if not pool:
        return await message.answer("Не вижу незакрытых дел, сэр.")

    closed, in_ticktick, missed = [], 0, []
    used = set()
    for want in wanted:
        best, score = None, 0.0
        for i, cand in enumerate(pool):
            if i in used:
                continue
            value = _similar(want, cand["title"])
            if value > score:
                best, score = i, value
        if best is None or score < 0.7:
            missed.append(want)
            continue
        used.add(best)
        cand = pool[best]
        if cand["plan_id"]:
            db.set_item_status(cand["plan_id"], "done", "")
        if cand["tt_id"] and tasks.connected():
            try:
                if await tasks.complete(cand["tt_project"], cand["tt_id"]):
                    in_ticktick += 1
            except Exception:
                pass
        closed.append(cand["title"])

    if not closed:
        return await message.answer(
            "Не нашёл таких дел: " + ", ".join(render.cut(m, 20) for m in missed)
        )

    body = [render.cut(f"✓ {t}", render.W - 2) for t in closed]
    footer = []
    done, total = db.day_score(planning.today_key())
    if total:
        footer.append(render.row("день", f"{render.bar(done, total)}  {done}/{total}"))
    if in_ticktick:
        footer.append(f"✓ закрыто в TickTick: {in_ticktick}")
    if missed:
        footer.append("не найдено: " + render.cut(", ".join(missed), 14))
    sections = [body] + ([footer] if footer else [])
    await reply_block(message, render.box("ЗАКРЫТО", sections))


async def act_list_tasks(message: Message) -> None:
    if not tasks.connected():
        return await message.answer("Задачи не подключены.")
    items = await tasks.open_tasks()
    if not items:
        return await message.answer("Открытых задач нет, сэр.")
    body = [render.cut(f"□ {i['title']}", render.W - 2) for i in items[:12]]
    await reply_block(message, render.box("ЗАДАЧИ", [body]))


async def act_tasks_overview(message: Message) -> None:
    if not tasks.connected():
        return await message.answer("Задачи не подключены.")
    from app import brief as brief_module
    from app import tasks_view

    items = await tasks.open_tasks(limit=60)
    if not items:
        return await message.answer("Открытых задач нет, сэр.")
    analysis = tasks_view.analyse(items)
    await reply_block(message, render.tasks_overview(
        analysis, brief_module.by_project(items)))

    urgent = tasks_view.urgent(analysis, limit=6)
    if urgent:
        body = []
        for row in urgent:
            if row["note"]:
                body.append(render.row(f"{row['mark']} {row['title']}", row["note"]))
            else:
                body.append(render.cut(f"{row['mark']} {row['title']}", render.W - 2))
        await reply_block(message, render.box("ТРЕБУЕТ ВНИМАНИЯ", [body]))


async def act_remember(message: Message, fact: str = "") -> None:
    fact = (fact or "").strip()
    if not fact:
        return await message.answer("Что запомнить, сэр?")
    db.add_fact(fact)
    await message.answer("Запомнил.")


ACTIONS = {
    "show_brief": lambda m, a: act_brief(m),
    "plan_day": lambda m, a: act_plan_day(m),
    "show_plan": lambda m, a: act_show_plan(m),
    "start_debrief": lambda m, a: act_debrief(m),
    "show_status": lambda m, a: act_status(m),
    "run_diagnostics": lambda m, a: act_diagnostics(m),
    "add_task": lambda m, a: act_add_task(m, a.get("title", "")),
    "list_tasks": lambda m, a: act_list_tasks(m),
    "tasks_overview": lambda m, a: act_tasks_overview(m),
    "complete_tasks": lambda m, a: act_complete_tasks(m, a.get("titles", [])),
    "remember_fact": lambda m, a: act_remember(m, a.get("fact", "")),
}

BUTTONS = {
    BTN_BRIEF: act_brief,
    BTN_PLAN: act_plan_day,
    BTN_DEBRIEF: act_debrief,
    BTN_STATUS: act_status,
}


# ======================= команды =======================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not mine(message):
        return await message.answer("Этот бот приватный.")
    await message.answer(mono(render.box("АССИСТЕНТ ЗАПУЩЕН", [
        [
            "Говорите обычными словами:",
            "«что сегодня»",
            "«давай спланируем день»",
            "«добавь задачу купить хлеб»",
            "«подведём итоги»",
            "«найди новости про X»",
        ],
        [
            "Команды, если удобнее:",
            "/brief /plan /debrief",
            "/status /tasks /setup",
            "/tone /remember /reset",
        ],
    ])), parse_mode=ParseMode.HTML, reply_markup=KEYBOARD)


@dp.message(Command("setup"))
async def cmd_setup(message: Message):
    if not mine(message):
        return
    if not config.PUBLIC_URL:
        return await message.answer(
            "Не задан PUBLIC_URL. В Railway откройте Settings → Networking → "
            "Generate Domain, затем впишите адрес в переменную PUBLIC_URL."
        )
    lines = ["Откройте ссылки и подтвердите доступ:", ""]
    if config.GMAIL_ENABLED:
        state = "готово" if gmail.connected() else "нужно подключить"
        lines.append(f"Gmail ({state}):")
        lines.append(f"{config.PUBLIC_URL}/auth/google?key={config.SETUP_KEY}")
        lines.append("")
    mode = tasks.mode()
    if mode == "mcp":
        lines.append("TickTick: подключён по токену, ссылка не нужна.")
    elif mode == "oauth":
        state = "готово" if ticktick.connected() else "нужно подключить"
        lines.append(f"TickTick ({state}):")
        lines.append(f"{config.PUBLIC_URL}/auth/ticktick?key={config.SETUP_KEY}")
    else:
        lines.append("TickTick: не настроен (нет TICKTICK_TOKEN).")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@dp.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Показывает, что бот видит в TickTick, и какие инструменты нашёл."""
    if not mine(message):
        return
    mode = tasks.mode()
    if mode == "off":
        return await message.answer("TickTick не подключён.")
    lines = [f"режим: {mode}"]
    if mode == "mcp":
        from app.integrations import ticktick_mcp
        try:
            found = await ticktick_mcp.tool_list(force=True)
            lines.append(f"инструментов: {len(found)}")
            chosen = {
                "проекты": ticktick_mcp._projects_tool(found),
                "задачи": ticktick_mcp._tasks_tool(found),
                "создать": ticktick_mcp._pick(found, ["task"], ["create", "add", "new"]),
                "закрыть": ticktick_mcp._pick(
                    found, ["task"], ["complete", "finish", "done"],
                    avoid=["undone", "uncompleted", "incomplete", "get", "list"]),
            }
            for role, tool in chosen.items():
                lines.append(f"  {role}: {(tool or {}).get('name', '— не найден')}")
            projects = await ticktick_mcp.projects(force=True)
            lines.append(f"списков: {len(projects)}")
            lines += ["  · " + p["name"] for p in projects[:10]]
        except Exception as exc:
            lines.append(f"ошибка списка: {exc}")
    open_items = await tasks.open_tasks(limit=60)
    lines.append("")
    lines.append(f"открытых задач: {len(open_items)}")
    for i in open_items[:10]:
        extra = []
        if i.get("due"):
            extra.append(f"срок {i['due']}")
        if i.get("priority") is not None:
            extra.append(f"приоритет {i['priority']}")
        if i.get("group"):
            extra.append(f"папка {i['group']}")
        tail = ("  [" + ", ".join(extra) + "]") if extra else ""
        lines.append(f"□ {i['title'][:26]}{tail}")

    raw = db.kv_get("ticktick_raw_task")
    if raw:
        import json
        lines.append("")
        lines.append("сырые поля одной задачи (для настройки):")
        lines.append(json.dumps(raw, ensure_ascii=False)[:900])
    await message.answer("\n".join(lines))


@dp.message(Command("brief"))
async def cmd_brief(message: Message):
    if mine(message):
        await act_brief(message)


@dp.message(Command("plan"))
async def cmd_plan(message: Message):
    if not mine(message):
        return
    if db.get_plan(planning.today_key()) and not message.text.endswith("new"):
        return await act_show_plan(message)
    await act_plan_day(message)


@dp.message(Command("debrief"))
async def cmd_debrief(message: Message):
    if mine(message):
        await act_debrief(message)


@dp.message(Command("status"))
async def cmd_status(message: Message):
    if mine(message):
        await act_status(message)


@dp.message(Command("diagnostics"))
async def cmd_diag(message: Message):
    if mine(message):
        await act_diagnostics(message)


@dp.message(Command("tone"))
async def cmd_tone(message: Message):
    if not mine(message):
        return
    current = db.kv_get("tone", "jarvis")
    new = "plain" if current == "jarvis" else "jarvis"
    db.kv_set("tone", new)
    await message.answer("Тон: " + ("сдержанный дворецкий" if new == "jarvis"
                                    else "нейтральный деловой"))


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    if not mine(message):
        return
    db.clear_history()
    planning.set_mode("chat")
    await message.answer("История очищена.", reply_markup=KEYBOARD)


@dp.message(Command("remember"))
async def cmd_remember(message: Message):
    if not mine(message):
        return
    fact = message.text.replace("/remember", "", 1).strip()
    if not fact:
        known = db.facts()
        return await message.answer(
            "Помню:\n" + "\n".join(f"— {f}" for f in known) if known
            else "Пока ничего не записано. Напишите: /remember текст"
        )
    await act_remember(message, fact)


@dp.message(Command("forget"))
async def cmd_forget(message: Message):
    if not mine(message):
        return
    db.clear_facts()
    await message.answer("Забыл всё, что помнил о вас.")


# ======================= свободная речь =======================

@dp.message(F.text)
async def on_text(message: Message):
    if not mine(message):
        return
    text = message.text.strip()

    # 1. Кнопки — прямое попадание, без обращения к модели
    if text in BUTTONS:
        return await BUTTONS[text](message)

    # 2. Диалоги плана и разбора — бот ждёт конкретный ответ
    mode = planning.get_mode()
    if mode == "await_plan":
        await message.answer("Составляю…")
        return await reply_block(message, await planning.handle_plan_reply(text))

    if mode == "await_debrief":
        await message.answer("Сверяю…")
        result, question = await planning.handle_debrief_reply(text)
        await reply_block(message, result)
        if question:
            await reply_block(message, question)
        return

    if mode == "await_carry":
        return await reply_block(message, await planning.handle_carry_reply(text))

    # 3. Обычная речь: Claude отвечает сам либо запускает действие
    await chat(message, text)


async def chat(message: Message, text: str) -> None:
    """Живая печать ответа; если Claude решил вызвать действие — выполняем его."""
    placeholder = await message.answer("…")
    last_edit, last_text, partial, actions = 0.0, "", "", []
    try:
        async for kind, payload in claude_client.stream_reply(text):
            if kind == "actions":
                actions = payload
                continue
            partial = payload
            now = time.time()
            if now - last_edit < 1.4 or partial == last_text:
                continue
            last_edit, last_text = now, partial
            try:
                await placeholder.edit_text(html.escape(partial),
                                            parse_mode=ParseMode.HTML)
            except Exception:
                pass
    except Exception as exc:
        return await placeholder.edit_text(f"Сбой при обращении к Claude: {exc}")

    if actions:
        # Действие само покажет результат — заглушка не нужна
        try:
            if partial.strip():
                await placeholder.edit_text(html.escape(partial),
                                            parse_mode=ParseMode.HTML)
            else:
                await placeholder.delete()
        except Exception:
            pass
        for action in actions:
            handler = ACTIONS.get(action["name"])
            if not handler:
                continue
            try:
                await handler(message, action.get("input", {}))
            except Exception as exc:
                await message.answer(f"Не удалось выполнить: {exc}")
        return

    if last_text != partial:
        try:
            await placeholder.edit_text(html.escape(partial or "(пусто)"),
                                        parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def run() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)
