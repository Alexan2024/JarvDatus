"""ASCII-рендер. Ширину держит код, не модель — поэтому рамки никогда не едут."""
from datetime import date, datetime

W = 26  # внутренняя ширина рамки

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
BARS = "▁▂▃▄▅▆▇█"


def ru_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def fit(text: str, width: int) -> str:
    """Обрезает по ширине, сохраняя расставленные пробелы."""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def cut(text: str, width: int) -> str:
    """Схлопывает пробелы и обрезает — для «сырого» текста от модели."""
    return fit(" ".join(str(text).split()), width)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            while len(w) > width:
                lines.append(w[:width])
                w = w[width:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# ---------- рамки ----------

def box(title: str, sections: list[list[str]]) -> str:
    """sections — список блоков строк, между блоками рисуется разделитель."""
    out = ["┌" + "─" * W + "┐"]
    if title:
        out.append("│ " + fit(title, W - 2).ljust(W - 2) + " │")
        out.append("├" + "─" * W + "┤")
    for i, block in enumerate(sections):
        if i:
            out.append("├" + "─" * W + "┤")
        for line in block:
            out.append("│ " + fit(line, W - 2).ljust(W - 2) + " │")
    out.append("└" + "─" * W + "┘")
    return "\n".join(out)


def note(text: str) -> str:
    """Короткая реплика в рамке."""
    return box("", [wrap(text, W - 2)])


def bar(done: int, total: int, cells: int = 10) -> str:
    if total <= 0:
        return "░" * cells
    filled = round(cells * done / total)
    return "▓" * filled + "░" * (cells - filled)


def spark(values: list[float]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    return "".join(BARS[min(7, int((v - lo) / span * 7))] for v in vals)


def row(left: str, right: str = "") -> str:
    """Строка с прижатой вправо колонкой."""
    inner = W - 2
    right = cut(right, inner - 2)
    left = cut(left, max(1, inner - len(right) - 1))
    gap = inner - len(left) - len(right)
    return left + " " * max(1, gap) + right


# ---------- утренний бриф (открытый стиль) ----------

def morning_brief(d: date, weather, events, mail, news, carry) -> str:
    line = "─" * W
    out = [f"{ru_date(d)} · {datetime.now().strftime('%H:%M')}", line]

    if weather:
        out.append("ПОГОДА")
        out.append("  " + cut(weather.get("summary", ""), W - 2))
        if weather.get("spark"):
            out.append(f"  {weather['spark']}   дождь {weather.get('rain', 0)}%")
        out.append("")

    if events:
        out.append("КАЛЕНДАРЬ")
        for e in events[:6]:
            out.append("  " + fit(f"{e['time']}  {cut(e['title'], 17)}", W - 2))
        if weather is not None and events:
            gap = free_gap(events)
            if gap:
                out.append("  " + fit(gap, W - 2))
        out.append("")

    if mail:
        out.append(f"ПОЧТА · {len(mail)}")
        for m in mail[:5]:
            mark = "!" if m.get("important") else "·"
            out.append("  " + fit(f"{mark}  {cut(m['line'], 19)}", W - 2))
        out.append("")

    if news:
        out.append("НОВОСТИ")
        for n in news[:5]:
            out.append("  " + fit(f"·  {cut(n, 20)}", W - 2))
        out.append("")

    if carry:
        out.append("СО ВЧЕРА")
        for c in carry[:5]:
            out.append("  " + fit(f"←  {cut(c['title'], 20)}", W - 2))
        out.append("")

    while out and out[-1] == "":
        out.pop()
    out.append(line)
    return "\n".join(out)


def free_gap(events: list[dict]) -> str:
    """Самое большое окно между событиями."""
    times = []
    for e in events:
        if e.get("start") and e.get("end"):
            times.append((e["start"], e["end"]))
    if len(times) < 2:
        return ""
    times.sort()
    best, gap = None, 0
    for i in range(len(times) - 1):
        delta = (times[i + 1][0] - times[i][1]).total_seconds() / 60
        if delta > gap:
            gap, best = delta, (times[i][1], times[i + 1][0])
    if best and gap >= 60:
        return f"свободно {best[0]:%H:%M}–{best[1]:%H:%M}"
    return ""


# ---------- план дня ----------

MARK = {"task": "□", "event": "■", "carry": "←"}
DONE_MARK = {"done": "✓", "partial": "~", "dropped": "✗", "moved": "→", "open": "□"}


def plan(d: date, items: list[dict], synced: bool) -> str:
    body = []
    for i, it in enumerate(items, 1):
        mark = MARK.get(it.get("kind", "task"), "□")
        title = it.get("title", "")
        when = it.get("note", "") or ""
        body.append(row(f"{mark} {i}  {title}", when))
    tasks = sum(1 for i in items if i.get("kind") != "event")
    events = sum(1 for i in items if i.get("kind") == "event")
    footer = [
        f"{tasks} {plural(tasks, 'задача', 'задачи', 'задач')} · "
        f"{events} {plural(events, 'событие', 'события', 'событий')}"
    ]
    if synced:
        footer.append("✓ синхронизировано")
    return box(f"ПЛАН · {ru_date(d)}", [body, footer])


# ---------- вечерний итог ----------

def debrief_question(d: date, items: list[dict]) -> str:
    body = ["Утром было заявлено:"]
    for i, it in enumerate(items, 1):
        body.append(fit(f"□ {i}  {cut(it['title'], 18)}", W - 2))
    return box(f"ИТОГИ · {ru_date(d)}", [body, ["Что закрыли, сэр?"]])


def debrief_result(d: date, items: list[dict], done: int, total: int, days: int,
                   closed: int) -> str:
    body = []
    for it in items:
        mark = DONE_MARK.get(it.get("status", "open"), "□")
        extra = it.get("note", "") if it.get("status") == "partial" else ""
        body.append(row(f"{mark} {it['title']}", extra))
    stats = [
        row("день", f"{bar(done, total)}  {done}/{total}"),
        row("серия", f"{days} {plural(days, 'день', 'дня', 'дней')}" if days else "—"),
    ]
    sections = [body, stats]
    if closed:
        sections.append([f"✓ закрыто в TickTick: {closed}"])
    return box(f"ИТОГ · {ru_date(d)}", sections)


# ---------- статус ----------

def status(rows: list[tuple[str, str]]) -> str:
    return box("СИСТЕМЫ В НОРМЕ", [[row(k, v) for k, v in rows]])


def diagnostics(checks: list[tuple[str, bool, str]]) -> str:
    body = []
    for name, ok, detail in checks:
        body.append(row(f"{'✓' if ok else '✗'} {name}", fit(detail, 10)))
    return box("ДИАГНОСТИКА", [body])
