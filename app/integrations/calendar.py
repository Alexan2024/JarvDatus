"""Apple Calendar через CalDAV (Apple ID + пароль приложения)."""
import asyncio
from datetime import datetime, timedelta

import caldav

from app import config

CALDAV_URL = "https://caldav.icloud.com/"


def _sync_events(start: datetime, end: datetime) -> list[dict]:
    client = caldav.DAVClient(
        url=CALDAV_URL,
        username=config.APPLE_ID,
        password=config.APPLE_APP_PASSWORD,
    )
    principal = client.principal()
    out = []
    for cal in principal.calendars():
        try:
            found = cal.search(start=start, end=end, event=True, expand=True)
        except Exception:
            continue
        for ev in found:
            try:
                comp = ev.icalendar_component
            except Exception:
                continue
            summary = str(comp.get("summary", "")) or "(без названия)"
            dtstart = comp.get("dtstart")
            dtend = comp.get("dtend")
            if dtstart is None:
                continue
            sv = dtstart.dt
            evv = dtend.dt if dtend is not None else None
            all_day = not isinstance(sv, datetime)
            if all_day:
                sv = datetime.combine(sv, datetime.min.time(), tzinfo=config.TZ)
                evv = sv + timedelta(days=1)
            else:
                sv = sv.astimezone(config.TZ)
                evv = evv.astimezone(config.TZ) if isinstance(evv, datetime) else sv
            out.append({
                "uid": str(comp.get("uid", "")) + sv.isoformat(),
                "title": summary,
                "start": sv,
                "end": evv,
                "all_day": all_day,
                "location": str(comp.get("location", "")),
                "time": "весь день" if all_day else sv.strftime("%H:%M"),
            })
    out.sort(key=lambda e: e["start"])
    return out


async def events_between(start: datetime, end: datetime) -> list[dict]:
    if not config.CALENDAR_ENABLED:
        return []
    try:
        return await asyncio.to_thread(_sync_events, start, end)
    except Exception:
        return []


async def today() -> list[dict]:
    now = datetime.now(config.TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return await events_between(start, start + timedelta(days=1))


async def upcoming(minutes: int = 20) -> list[dict]:
    now = datetime.now(config.TZ)
    return await events_between(now, now + timedelta(minutes=minutes))


async def check() -> tuple[bool, str]:
    if not config.CALENDAR_ENABLED:
        return False, "выкл"
    try:
        await asyncio.to_thread(
            _sync_events, datetime.now(config.TZ),
            datetime.now(config.TZ) + timedelta(hours=1)
        )
        return True, "ок"
    except Exception:
        return False, "ошибка"
