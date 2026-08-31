"""Телеграм-бот: команды, чат с Claude, живая печать ответа."""
import asyncio
import html
import time
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from app import claude_client, config, db, planning, render
from app.integrations import calendar, gmail, tasks, ticktick

bot = Bot(token=config.TELEGRAM_TOKEN)
dp = Dispatcher()
STARTED_AT = time.time()


def mono(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


async def send_block(text: str) -> None:
    await bot.send_message(config.OWNER_ID, mono(text), parse_mode=ParseMode.HTML)


async def send_text(text: str) -> None:
    await bot.send_message(config.OWNER_ID, html.escape(text),
                           parse_mode=ParseMode.HTML)


def mine(message: Message) -> bool:
    return config.OWNER_ID and message.from_user.id == config.OWNER_ID


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not mine(message):
        return await message.answer("Этот бот приватный.")
    await message.answer(mono(render.box("АССИСТЕНТ ЗАПУЩЕН", [[
        "/status  — сводка систем",
        "/brief   — бриф сейчас",
        "/plan    — спланировать день",
        "/debrief — разбор дня",
        "/setup   — подключить сервисы",
        "/tone    — сменить тон",
        "/reset   — очистить диалог",
    ]])), parse_mode=ParseMode.HTML)


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


@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not mine(message):
        return
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

    await message.answer(mono(render.status([
        ("время", now.strftime("%H:%M")),
        ("почта", f"{unread} непроч." if unread else "чисто"),
        ("задачи", f"{active} активных"),
        ("до встречи", next_line),
        ("расход API", f"${spent.get('usd', 0):.2f}"),
        ("аптайм", uptime),
    ])), parse_mode=ParseMode.HTML)


@dp.message(Command("diagnostics"))
async def cmd_diag(message: Message):
    if not mine(message):
        return
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
    await message.answer(mono(render.diagnostics(checks)), parse_mode=ParseMode.HTML)


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
            lines += ["· " + (t.get("name") or "?") for t in found[:25]]
        except Exception as exc:
            lines.append(f"ошибка списка: {exc}")
    open_items = await tasks.open_tasks()
    lines.append("")
    lines.append(f"открытых задач: {len(open_items)}")
    lines += ["□ " + i["title"][:30] for i in open_items[:10]]
    await message.answer("\n".join(lines))


@dp.message(Command("brief"))
async def cmd_brief(message: Message):
    if not mine(message):
        return
    await message.answer("Собираю…")
    text, _ = await planning.build_morning()
    await message.answer(mono(text), parse_mode=ParseMode.HTML)


@dp.message(Command("plan"))
async def cmd_plan(message: Message):
    if not mine(message):
        return
    today = db.get_plan(planning.today_key())
    if today and not message.text.endswith("new"):
        return await message.answer(
            mono(render.plan(date.today(), today, False)), parse_mode=ParseMode.HTML
        )
    _, question = await planning.build_morning()
    planning.set_mode("await_plan")
    await message.answer(mono(question), parse_mode=ParseMode.HTML)


@dp.message(Command("debrief"))
async def cmd_debrief(message: Message):
    if not mine(message):
        return
    text = await planning.build_evening()
    if not text:
        return await message.answer("На сегодня открытых задач нет, сэр.")
    await message.answer(mono(text), parse_mode=ParseMode.HTML)


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
    await message.answer("История очищена.")


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
    db.add_fact(fact)
    await message.answer("Запомнил.")


@dp.message(Command("forget"))
async def cmd_forget(message: Message):
    if not mine(message):
        return
    db.clear_facts()
    await message.answer("Забыл всё, что помнил о вас.")


@dp.message(F.text)
async def on_text(message: Message):
    if not mine(message):
        return
    text = message.text.strip()
    mode = planning.get_mode()

    if mode == "await_plan":
        await message.answer("Составляю…")
        result = await planning.handle_plan_reply(text)
        return await message.answer(mono(result), parse_mode=ParseMode.HTML)

    if mode == "await_debrief":
        await message.answer("Сверяю…")
        result, question = await planning.handle_debrief_reply(text)
        await message.answer(mono(result), parse_mode=ParseMode.HTML)
        if question:
            await message.answer(mono(question), parse_mode=ParseMode.HTML)
        return

    if mode == "await_carry":
        result = await planning.handle_carry_reply(text)
        return await message.answer(mono(result), parse_mode=ParseMode.HTML)

    await chat(message, text)


async def chat(message: Message, text: str) -> None:
    """Живая печать: бот редактирует своё сообщение по мере генерации."""
    placeholder = await message.answer("…")
    last_edit, last_text, partial = 0.0, "", ""
    try:
        async for partial in claude_client.stream_reply(text):
            now = time.time()
            if now - last_edit < 1.4 or partial == last_text:
                continue
            last_edit, last_text = now, partial
            try:
                await placeholder.edit_text(html.escape(partial),
                                            parse_mode=ParseMode.HTML)
            except Exception:
                pass
        if last_text != partial:
            await placeholder.edit_text(html.escape(partial or "(пусто)"),
                                        parse_mode=ParseMode.HTML)
    except Exception as exc:
        await placeholder.edit_text(f"Сбой при обращении к Claude: {exc}")


async def run() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)
