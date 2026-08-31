"""Сборка утреннего брифа: погода, календарь, почта, новости, хвосты."""
import asyncio
from datetime import date

from app import claude_client, db, render, tasks_view
from app.integrations import calendar, gmail, news, tasks

MAIL_SYSTEM = """Ты разбираешь почту. Для каждого письма дай одну законченную
фразу до 42 символов в формате «Отправитель — суть». Обрывать на полуслове
нельзя: лучше выбрать формулировку короче, чем не поместиться.
Пометь important=true, если письмо требует личного ответа или содержит
срок/деньги. Рекламу и рассылки помечай important=false, суть — «рассылка»."""

NEWS_SYSTEM = """Ты — редактор новостной ленты. Выбери 3-5 самых значимых новостей
и перепиши заголовок каждой своими словами одним законченным предложением
до 90 символов — так, чтобы суть была понятна без перехода по ссылке.
Без кавычек, без названий источников, без обрыва на полуслове.
Обязательно сохраняй поле i — номер исходного заголовка."""


async def summarize_mail(letters: list[dict]) -> list[dict]:
    if not letters:
        return []
    listing = "\n".join(
        f"{i+1}. От: {m['from']} | Тема: {m['subject']} | {m['snippet'][:120]}"
        for i, m in enumerate(letters)
    )
    result = await claude_client.ask_json(
        f"Письма:\n{listing}\n\nВерни JSON: "
        '{"items":[{"line":"...","important":true}]} в том же порядке.',
        system=MAIL_SYSTEM,
        fallback=None,
    )
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"][:6]
    return [{"line": render.cut(f"{m['from'].split('<')[0]} — {m['subject']}", 40),
             "important": m.get("unread", False)} for m in letters[:5]]


async def summarize_news(items: list[dict]) -> list[dict]:
    """Возвращает [{"title": переписанный заголовок, "link": исходная ссылка}]."""
    if not items:
        return []
    listing = "\n".join(f"{i+1}. {n['title']}" for i, n in enumerate(items))
    result = await claude_client.ask_json(
        f"Заголовки:\n{listing}\n\nВерни JSON: "
        '{"items":[{"i":1,"line":"законченное предложение"}]}',
        system=NEWS_SYSTEM,
        fallback=None,
    )
    out = []
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        for row in result["items"][:5]:
            if not isinstance(row, dict) or not row.get("line"):
                continue
            link = ""
            try:
                index = int(row.get("i", 0)) - 1
                if 0 <= index < len(items):
                    link = items[index].get("link", "")
            except (TypeError, ValueError):
                pass
            out.append({"title": str(row["line"]).strip(), "link": link})
    if out:
        return out
    return [{"title": n["title"], "link": n.get("link", "")} for n in items[:4]]


async def gather() -> dict:
    """Собирает всё параллельно, ничего не роняя из-за одного сбоя."""
    today = date.today()
    weather_t = asyncio.create_task(_safe(_weather()))
    events_t = asyncio.create_task(_safe(calendar.today()))
    mail_t = asyncio.create_task(_safe(gmail.recent() if gmail.connected() else _none()))
    news_t = asyncio.create_task(_safe(news.headlines()))
    tasks_t = asyncio.create_task(
        _safe(tasks.open_tasks(limit=60) if tasks.connected() else _none())
    )

    weather = await weather_t
    events = await events_t or []
    letters = await mail_t or []
    raw_news = await news_t or []
    tt_tasks = await tasks_t or []

    mail, headlines = await asyncio.gather(
        _safe(summarize_mail(letters)), _safe(summarize_news(raw_news))
    )

    analysis = tasks_view.analyse(tt_tasks) if tt_tasks else None

    carry = db.carryover(today.isoformat())
    seen = {c["title"].strip().lower() for c in carry}
    # в план дня предлагаем срочные задачи из TickTick, а не всё подряд
    urgent_titles = {r["title"].strip().lower()
                     for r in tasks_view.urgent(analysis, limit=6)} if analysis else set()
    for t in tt_tasks:
        key = t["title"].strip().lower()
        if key in seen or key not in urgent_titles:
            continue
        carry.append({"title": t["title"], "ticktick_id": t["id"],
                      "ticktick_project": t["project_id"], "kind": "carry"})
        seen.add(key)

    return {
        "date": today,
        "weather": weather,
        "events": events,
        "mail": mail or [],
        "news": headlines or [],
        "carry": carry[:6],
        "tasks": analysis,
        "raw_tasks": tt_tasks,
    }


async def _weather():
    from app.integrations import weather as w
    return await w.today()


async def _none():
    return []


async def _safe(coro):
    try:
        return await coro
    except Exception:
        return None


def render_brief(data: dict) -> str:
    """Только моноширинный блок, без новостей."""
    return render.morning_brief(
        data["date"], data["weather"], data["events"],
        data["mail"], data["carry"], data.get("tasks"),
    )


def render_html(data: dict) -> str:
    """Блок в рамке + новости под ним отдельными кликабельными строками.

    Ссылки внутри <pre> Telegram не делает кликабельными, поэтому новости
    живут под блоком обычным текстом.
    """
    import html as html_mod

    parts = ["<pre>" + html_mod.escape(render_brief(data)) + "</pre>"]
    news = data.get("news") or []
    if news:
        lines = ["", "<b>НОВОСТИ</b>"]
        for item in news[:5]:
            title = html_mod.escape(str(item.get("title", "")).strip())
            if not title:
                continue
            link = str(item.get("link", "")).strip()
            if link.startswith("http"):
                lines.append(f'• <a href="{html_mod.escape(link, quote=True)}">{title}</a>')
            else:
                lines.append(f"• {title}")
        if len(lines) > 2:
            parts.append("\n".join(lines))
    return "\n".join(parts)


def by_project(items: list[dict]) -> list[tuple[str, int, int]]:
    """Сводка по спискам: (название, всего, просрочено)."""
    from datetime import datetime

    from app import config

    today = datetime.now(config.TZ).date()
    counts: dict[str, list[int]] = {}
    for item in items:
        name = (item.get("project") or "без списка").strip()
        row = counts.setdefault(name, [0, 0])
        row[0] += 1
        due = tasks_view.parse_due(item.get("due"))
        if due and due < today:
            row[1] += 1
    return sorted(((n, c[0], c[1]) for n, c in counts.items()),
                  key=lambda r: (-r[2], -r[1]))
