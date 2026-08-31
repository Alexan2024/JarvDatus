import os
from zoneinfo import ZoneInfo


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _time(name: str, default: str) -> tuple[int, int]:
    raw = os.getenv(name, "") or default
    try:
        h, m = raw.split(":")
        return int(h), int(m)
    except Exception:
        h, m = default.split(":")
        return int(h), int(m)


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OWNER_ID = _int("OWNER_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
SETUP_KEY = os.getenv("SETUP_KEY", "").strip()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
DB_PATH = os.getenv("DB_PATH", "/data/jarvis.db").strip()
PORT = _int("PORT", 8080)

TZ_NAME = os.getenv("TZ_NAME", "Europe/Moscow").strip()
TZ = ZoneInfo(TZ_NAME)
MORNING_H, MORNING_M = _time("MORNING_TIME", "08:00")
EVENING_H, EVENING_M = _time("EVENING_TIME", "21:00")

WEATHER_LAT = os.getenv("WEATHER_LAT", "55.7558").strip()
WEATHER_LON = os.getenv("WEATHER_LON", "37.6173").strip()
CITY_NAME = os.getenv("CITY_NAME", "").strip()

RSS_FEEDS = [u.strip() for u in os.getenv("RSS_FEEDS", "").split(",") if u.strip()]

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

TICKTICK_TOKEN = os.getenv("TICKTICK_TOKEN", "").strip()
TICKTICK_CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID", "").strip()
TICKTICK_CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET", "").strip()
TICKTICK_PROJECT_NAME = os.getenv("TICKTICK_PROJECT_NAME", "Jarvis").strip()
TASK_MAX_PROJECTS = _int("TASK_MAX_PROJECTS", 12)
TASK_SKIP_PROJECTS = [p.strip().lower()
                      for p in os.getenv("TASK_SKIP_PROJECTS", "").split(",") if p.strip()]

# Запасное деление на группы, если папок в TickTick нет:
# TASK_GROUPS=Работа:рабочее, Клиенты:рабочее, Дом:личное
TASK_GROUPS = {}
for _pair in os.getenv("TASK_GROUPS", "").split(","):
    if ":" in _pair:
        _name, _group = _pair.split(":", 1)
        if _name.strip() and _group.strip():
            TASK_GROUPS[_name.strip().lower()] = _group.strip()

APPLE_ID = os.getenv("APPLE_ID", "").strip()
APPLE_APP_PASSWORD = os.getenv("APPLE_APP_PASSWORD", "").strip()

PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
if not PUBLIC_URL:
    _rw = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if _rw:
        PUBLIC_URL = f"https://{_rw}"

PRICE_IN = float(os.getenv("PRICE_IN", "3") or 3)
PRICE_OUT = float(os.getenv("PRICE_OUT", "15") or 15)

GMAIL_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
TICKTICK_ENABLED = bool(TICKTICK_CLIENT_ID and TICKTICK_CLIENT_SECRET)
TASKS_ENABLED = bool(TICKTICK_TOKEN or TICKTICK_ENABLED)
CALENDAR_ENABLED = bool(APPLE_ID and APPLE_APP_PASSWORD)
