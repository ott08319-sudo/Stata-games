import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from database import Database
from handlers import router
from middlewares import LogErrorsMiddleware


def load_local_env(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


async def main() -> None:
    load_local_env()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Задайте BOT_TOKEN в .env или переменных окружения хостинга")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(os.getenv("DB_PATH", "game.sqlite3"))
    db.refund_unfinished_rounds()

    bot = Bot(token=token)
    dp = Dispatcher()
    router.message.middleware(LogErrorsMiddleware())
    router.callback_query.middleware(LogErrorsMiddleware())
    dp.include_router(router)

    try:
        await dp.start_polling(bot, db=db)
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
