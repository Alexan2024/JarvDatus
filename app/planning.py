"""Утренний диалог о плане и вечерний разбор."""
from datetime import date, datetime, timedelta

from app import brief, claude_client, db, render
from app.integrations import ticktick

MODE_KEY = "mode"

PLAN_SYSTEM = """Ты превращаешь свободный рассказ о планах в список дел.
Правила:
- Каждый пункт — короткая формулировка до 20 символов, глагол в инфинитиве.
- Не выдумывай задач, которых человек не называл.
- Если названо время или срок — положи его в поле note (до 8 символов),
  иначе оставь note пустым.
- Если человек говорит, что задачу из списка «переношу» или «не буду» —
  не включай её."""

DEBRIEF_SYSTEM = """Ты сопоставляешь отчёт человека со списком задач.
Для каждой задачи из списка определи статус:
- done — сделал
- partial — сделал частично (в note укажи процент или «частично»)
- dropped — отказался, отменил
- open — не упомянул
Не придумывай новых задач. Отвечай строго по списку, сохраняя id."""

CARRY_SYSTEM = """Человек решает судьбу незакрытых задач.
Для каждой задачи верни action: "move" (перенести на завтра) или "drop" (снять).
Если человек не упомянул задачу — ставь "move"."""


def get_mode() -> str:
    return db.kv_get(MODE_KEY, "chat")


def set_mode(mode: str) -> None:
    db.kv_set(MODE_KEY, mode)


def today_key() -> str:
    return date.today().isoformat()


# ---------- утро ----------

async def build_morning() -> tuple[str, str]:
    data = await brief.gather()
    db.kv_set("morning_context", {
        "events": [
            {"title": e["title"], "note": e["time"]} for e in data["events"]
        ],
        "carry": [
            {"title": c["title"], "ticktick_id": c.get("ticktick_id", ""),
             "ticktick_project": c.get("ticktick_project", "")}
            for c in data["carry"]
        ],
    })
    question = render.note("Что сегодня, сэр? Пишите как есть — разберу.")
    return brief.render_brief(data), question


async def handle_plan_reply(text: str) -> str:
    ctx = db.kv_get("morning_context", {"events": [], "carry": []})
    events, carry = ctx.get("events", []), ctx.get("carry", [])

    listing = ""
    if carry:
        listing = "Незакрытые задачи прошлых дней:\n" + "\n".join(
            f"- {c['title']}" for c in carry
        )

    parsed = await claude_client.ask_json(
        f"Человек рассказал о планах на день:\n«{text}»\n\n{listing}\n\n"
        'Верни JSON: {"tasks":[{"title":"...","note":""}],'
        '"keep_carry":["точное название переносимой задачи"]}\n'
        "В keep_carry положи те незакрытые задачи, которые человек оставляет на "
        "сегодня. Если он про них не сказал — оставь список пустым.",
        system=PLAN_SYSTEM,
        fallback={"tasks": [], "keep_carry": []},
    ) or {"tasks": [], "keep_carry": []}

    items: list[dict] = []
    for e in events:
        items.append({"kind": "event", "title": e["title"], "note": e.get("note", "")})

    kept = {k.strip().lower() for k in parsed.get("keep_carry", []) if isinstance(k, str)}
    for c in carry:
        if c["title"].strip().lower() in kept:
            items.append({
                "kind": "carry", "title": c["title"], "note": "",
                "ticktick_id": c.get("ticktick_id", ""),
                "ticktick_project": c.get("ticktick_project", ""),
            })

    synced = 0
    for t in parsed.get("tasks", []):
        if not isinstance(t, dict) or not t.get("title"):
            continue
        item = {"kind": "task", "title": str(t["title"])[:40],
                "note": str(t.get("note", ""))[:10]}
        if ticktick.connected():
            created = await ticktick.create(item["title"])
            if created.get("id"):
                item["ticktick_id"] = created["id"]
                item["ticktick_project"] = created.get("project_id", "")
                synced += 1
        items.append(item)

    items.sort(key=lambda i: {"event": 1, "task": 0, "carry": 2}.get(i["kind"], 0))
    db.save_plan(today_key(), items)
    db.close_old_open(today_key())
    set_mode("chat")
    return render.plan(date.today(), items, synced > 0)


# ---------- вечер ----------

async def build_evening() -> str | None:
    items = [i for i in db.get_plan(today_key())
             if i["kind"] != "event" and i["status"] == "open"]
    if not items:
        return None
    set_mode("await_debrief")
    return render.debrief_question(date.today(), items)


async def handle_debrief_reply(text: str) -> tuple[str, str | None]:
    items = [i for i in db.get_plan(today_key()) if i["kind"] != "event"]
    listing = "\n".join(f'{i["id"]}: {i["title"]}' for i in items)

    parsed = await claude_client.ask_json(
        f"Список задач (id: название):\n{listing}\n\n"
        f"Отчёт человека:\n«{text}»\n\n"
        'Верни JSON: {"items":[{"id":1,"status":"done","note":""}]}',
        system=DEBRIEF_SYSTEM,
        fallback={"items": []},
    ) or {"items": []}

    by_id = {i["id"]: i for i in items}
    closed = 0
    for row in parsed.get("items", []):
        if not isinstance(row, dict):
            continue
        item = by_id.get(row.get("id"))
        if not item:
            continue
        status = row.get("status", "open")
        if status not in ("done", "partial", "dropped", "open"):
            status = "open"
        db.set_item_status(item["id"], status, str(row.get("note", ""))[:10])
        item["status"], item["note"] = status, str(row.get("note", ""))[:10]
        if status == "done" and item.get("ticktick_id"):
            if await ticktick.complete(item["ticktick_project"], item["ticktick_id"]):
                closed += 1

    done, total = db.day_score(today_key())
    result = render.debrief_result(
        date.today(), items, done, total, db.streak(date.today()), closed
    )

    leftovers = [i for i in items if i["status"] in ("open", "partial")]
    if leftovers:
        set_mode("await_carry")
        db.kv_set("carry_candidates", [i["id"] for i in leftovers])
        names = ", ".join(render.cut(i["title"], 18) for i in leftovers[:4])
        question = render.note(
            f"Незакрытое: {names}. Переносим на завтра или снимаем?"
        )
        return result, question

    set_mode("chat")
    return result, None


async def handle_carry_reply(text: str) -> str:
    ids = db.kv_get("carry_candidates", [])
    items = [i for i in db.get_plan(today_key()) if i["id"] in ids]
    if not items:
        set_mode("chat")
        return render.note("Ничего переносить не нужно.")

    listing = "\n".join(f'{i["id"]}: {i["title"]}' for i in items)
    parsed = await claude_client.ask_json(
        f"Задачи (id: название):\n{listing}\n\nОтвет человека:\n«{text}»\n\n"
        'Верни JSON: {"items":[{"id":1,"action":"move"}]}',
        system=CARRY_SYSTEM,
        fallback={"items": []},
    ) or {"items": []}

    decisions = {r.get("id"): r.get("action", "move")
                 for r in parsed.get("items", []) if isinstance(r, dict)}
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    moved, dropped = [], 0
    for item in items:
        if decisions.get(item["id"], "move") == "drop":
            db.set_item_status(item["id"], "dropped")
            dropped += 1
        else:
            db.set_item_status(item["id"], "moved")
            moved.append(item)

    if moved:
        existing = db.get_plan(tomorrow)
        for m in moved:
            existing.append({
                "kind": "carry", "title": m["title"], "note": "", "status": "open",
                "ticktick_id": m.get("ticktick_id", ""),
                "ticktick_project": m.get("ticktick_project", ""),
            })
        db.save_plan(tomorrow, existing)

    set_mode("chat")
    db.kv_del("carry_candidates")
    parts = []
    if moved:
        parts.append(f"перенесено: {len(moved)}")
    if dropped:
        parts.append(f"снято: {dropped}")
    return render.note("Записал — " + ", ".join(parts) + ". До завтра, сэр.")
