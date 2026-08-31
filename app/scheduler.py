"""Расписание: утро, вечер, пинг перед встречей."""
import logging
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import bot as bot_module
from app import config, db, planning, render
from app.integrations import calendar

log = logging.getLogger(__name__)


async def morning_job() -> None:
    try:
        brief_html, question = await planning.build_morning()
        await bot_module.send_html(brief_html)
        planning.set_mode("await_plan")
        await bot_module.send_block(question)
    except Exception as exc:
        log.exception("morning failed")
        await bot_module.send_text(f"Утренний бриф не собрался: {exc}")


async def evening_job() -> None:
    try:
        text = await planning.build_evening()
        if text:
            await bot_module.send_block(text)
        else:
            done, total = db.day_score(date.today().isoformat())
            if total:
                await bot_module.send_block(render.debrief_result(
                    date.today(),
                    [i for i in db.get_plan(date.today().isoformat())
                     if i["kind"] != "event"],
                    done, total, db.streak(date.today()), 0,
                ))
    except Exception as exc:
        log.exception("evening failed")
        await bot_module.send_text(f"Вечерний разбор не собрался: {exc}")


async def meeting_ping() -> None:
    """За 15 минут до события — короткое напоминание."""
    if not config.CALENDAR_ENABLED:
        return
    try:
        now = datetime.now(config.TZ)
        for ev in await calendar.upcoming(minutes=17):
            if ev["all_day"]:
                continue
            minutes = int((ev["start"] - now).total_seconds() // 60)
            if minutes < 0 or minutes > 16:
                continue
            if db.was_pinged(ev["uid"]):
                continue
            db.mark_pinged(ev["uid"])
            lines = [render.row(ev["time"], f"через {minutes} мин"), ev["title"]]
            if ev.get("location"):
                lines.append(render.cut(ev["location"], 24))
            await bot_module.send_block(render.box("СКОРО", [lines]))
    except Exception:
        log.exception("meeting ping failed")


def build() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=config.TZ)
    sched.add_job(morning_job, "cron", hour=config.MORNING_H,
                  minute=config.MORNING_M, id="morning", misfire_grace_time=1800)
    sched.add_job(evening_job, "cron", hour=config.EVENING_H,
                  minute=config.EVENING_M, id="evening", misfire_grace_time=1800)
    sched.add_job(meeting_ping, "interval", minutes=5, id="ping",
                  misfire_grace_time=120)
    return sched
