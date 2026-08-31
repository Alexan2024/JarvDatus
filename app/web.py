"""Мини-сайт для подключения Gmail и TickTick в один клик."""
import logging

from aiohttp import web

from app import config
from app.integrations import gmail, ticktick

log = logging.getLogger(__name__)

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ассистент</title>
<style>body{{font-family:ui-monospace,Menlo,monospace;background:#0d0f12;color:#d7dde3;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}}
.card{{border:1px solid #2a3038;padding:28px 32px;max-width:420px;line-height:1.6}}
h1{{font-size:16px;letter-spacing:.08em;margin:0 0 16px;color:#7fd1a8}}
a{{color:#7fd1a8}}</style></head>
<body><div class="card"><h1>{title}</h1><p>{body}</p></div></body></html>"""


def page(title: str, body: str, status: int = 200) -> web.Response:
    return web.Response(text=PAGE.format(title=title, body=body),
                        content_type="text/html", status=status)


def guard(request: web.Request) -> bool:
    return bool(config.SETUP_KEY) and request.query.get("key") == config.SETUP_KEY


async def root(request: web.Request) -> web.Response:
    return page("АССИСТЕНТ", "Сервис работает. Управление — в Telegram.")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def auth_google(request: web.Request) -> web.Response:
    if not guard(request):
        return page("ДОСТУП ЗАКРЫТ", "Неверный ключ.", 403)
    if not config.GMAIL_ENABLED:
        return page("GMAIL", "Не заданы GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET.")
    raise web.HTTPFound(gmail.auth_link(config.SETUP_KEY))


async def auth_google_cb(request: web.Request) -> web.Response:
    code = request.query.get("code", "")
    if request.query.get("state") != config.SETUP_KEY or not code:
        return page("ОШИБКА", "Запрос не прошёл проверку.", 400)
    ok = await gmail.exchange_code(code)
    return page("GMAIL", "Готово. Можно закрыть вкладку." if ok
                else "Не удалось получить токен. Проверьте redirect URI.")


async def auth_ticktick(request: web.Request) -> web.Response:
    if not guard(request):
        return page("ДОСТУП ЗАКРЫТ", "Неверный ключ.", 403)
    if not config.TICKTICK_ENABLED:
        return page("TICKTICK", "Не заданы TICKTICK_CLIENT_ID и TICKTICK_CLIENT_SECRET.")
    raise web.HTTPFound(ticktick.auth_link(config.SETUP_KEY))


async def auth_ticktick_cb(request: web.Request) -> web.Response:
    code = request.query.get("code", "")
    if request.query.get("state") != config.SETUP_KEY or not code:
        return page("ОШИБКА", "Запрос не прошёл проверку.", 400)
    ok = await ticktick.exchange_code(code)
    return page("TICKTICK", "Готово. Можно закрыть вкладку." if ok
                else "Не удалось получить токен. Проверьте redirect URI.")


async def start() -> web.AppRunner:
    app = web.Application()
    app.add_routes([
        web.get("/", root),
        web.get("/health", health),
        web.get("/auth/google", auth_google),
        web.get("/auth/google/callback", auth_google_cb),
        web.get("/auth/ticktick", auth_ticktick),
        web.get("/auth/ticktick/callback", auth_ticktick_cb),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    log.info("web on :%s", config.PORT)
    return runner
