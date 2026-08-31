"""Разбор задач: сроки, приоритеты, группировка по папкам.

Устойчив к неполным данным: если сервер не отдаёт срок или приоритет,
блок аккуратно сворачивается до счётчиков, а не падает.
"""
import re
from datetime import date, datetime, timezone

from app import config

HIGH_PRIORITY = 5
NO_GROUP = "прочее"


def parse_due(value) -> date | None:
    """Понимает ISO, миллисекунды, «+0300» и «Z». Возвращает дату в вашей зоне."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, timezone.utc).astimezone(
                config.TZ).date()
        except (ValueError, OSError, OverflowError):
            return None

    text = str(value).strip()
    if not text:
        return None

    cleaned = text.replace("Z", "+00:00")
    cleaned = re.sub(r"\.\d+", "", cleaned)
    # «+0300» -> «+03:00»
    cleaned = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", cleaned)
    for candidate in (cleaned, cleaned[:19], cleaned[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=config.TZ)
        return parsed.astimezone(config.TZ).date()
    return None


def is_high(priority) -> bool:
    return isinstance(priority, int) and priority >= HIGH_PRIORITY


def analyse(items: list[dict], today: date | None = None) -> dict:
    """Раскладывает задачи на просроченные, сегодняшние, важные и остальные."""
    today = today or datetime.now(config.TZ).date()
    overdue, due_today, high, rest = [], [], [], []
    groups: dict[str, int] = {}
    dated = 0

    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        group = (item.get("group") or "").strip() or NO_GROUP
        groups[group] = groups.get(group, 0) + 1

        due = parse_due(item.get("due"))
        priority = item.get("priority")
        row = {
            "title": title,
            "project": item.get("project", ""),
            "group": group,
            "due": due,
            "priority": priority,
            "days": (today - due).days if due else 0,
        }
        if due:
            dated += 1
            if due < today:
                overdue.append(row)
                continue
            if due == today:
                due_today.append(row)
                continue
        if is_high(priority):
            high.append(row)
            continue
        rest.append(row)

    overdue.sort(key=lambda r: -r["days"])
    due_today.sort(key=lambda r: (not is_high(r["priority"]), r["title"]))

    ordered = sorted(groups.items(), key=lambda kv: (kv[0] == NO_GROUP, -kv[1]))
    return {
        "total": len(overdue) + len(due_today) + len(high) + len(rest),
        "overdue": overdue,
        "today": due_today,
        "high": high,
        "rest": rest,
        "groups": ordered,
        "has_dates": dated > 0,
        "has_priority": any(isinstance(i.get("priority"), int) for i in items),
    }


def urgent(analysis: dict, limit: int = 5) -> list[dict]:
    """Что требует внимания. Квоты, чтобы просроченное не вытеснило сегодняшнее."""
    buckets = [
        ("⚠", analysis["overdue"], 3, True),
        ("●", analysis["today"], 3, False),
        ("↑", analysis["high"], 2, False),
    ]
    picked: list[dict] = []
    for mark, rows, quota, with_days in buckets:
        for row in rows[:quota]:
            picked.append({"mark": mark, "title": row["title"],
                           "note": f"-{row['days']}д" if with_days and row["days"] else ""})

    # если место осталось — добираем сверх квот, в том же порядке важности
    if len(picked) < limit:
        taken = {(p["mark"], p["title"]) for p in picked}
        for mark, rows, quota, with_days in buckets:
            for row in rows[quota:]:
                if len(picked) >= limit:
                    break
                if (mark, row["title"]) in taken:
                    continue
                picked.append({"mark": mark, "title": row["title"],
                               "note": f"-{row['days']}д" if with_days and row["days"] else ""})
    return picked[:limit]


def hidden_count(analysis: dict, shown: list[dict]) -> int:
    """Сколько срочного не поместилось на экран."""
    total = len(analysis["overdue"]) + len(analysis["today"]) + len(analysis["high"])
    return max(0, total - len(shown))


def counters(analysis: dict, width: int = 24, max_lines: int = 2) -> list[str]:
    """Счётчики по папкам, разложенные по строкам под ширину экрана."""
    parts = [f"{name.lower()} {count}" for name, count in analysis["groups"]]
    if not parts:
        return []

    lines, current, used = [], "", 0
    for i, part in enumerate(parts):
        candidate = part if not current else current + " · " + part
        if len(candidate) <= width:
            current, used = candidate, i + 1
            continue
        lines.append(current)
        if len(lines) == max_lines:
            break
        current, used = part, i + 1
    if current and len(lines) < max_lines:
        lines.append(current)
        used = len(parts)

    leftover = sum(c for _, c in analysis["groups"][used:])
    if leftover and lines:
        tail = lines[-1] + f" · +{leftover}"
        lines[-1] = tail if len(tail) <= width else lines[-1]
    return [line for line in lines if line]
