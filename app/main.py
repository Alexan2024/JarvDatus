import asyncio
import logging

from app import bot as bot_module
from app import config, db, scheduler, web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("jarvis")


def preflight() -> None:
    missing = []
    if not config.TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not config.OWNER_ID:
        missing.append("OWNER_ID")
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise SystemExit("Не заданы переменные: " + ", ".join(missing))


async def amain() -> None:
    preflight()
    db.init()
    await web.start()
    sched = scheduler.build()
    sched.start()
    log.info("расписание: утро %02d:%02d, вечер %02d:%02d (%s)",
             config.MORNING_H, config.MORNING_M,
             config.EVENING_H, config.EVENING_M, config.TZ_NAME)
    try:
        await bot_module.bot.send_message(
            config.OWNER_ID, "Ассистент на связи, сэр.",
            reply_markup=bot_module.KEYBOARD,
        )
    except Exception:
        log.warning("не смог отправить приветствие — проверьте OWNER_ID")
    await bot_module.run()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit) as exc:
        log.info("остановка: %s", exc)
