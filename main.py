import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from database import Database
from handlers import router
from middlewares import LogErrorsMiddleware


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Задайте BOT_TOKEN в .env или переменных окружения")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
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
