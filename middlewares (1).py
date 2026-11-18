import logging

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)


class LogErrorsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("Ошибка при обработке события")
            message = getattr(event, "message", None)
            if message is None and hasattr(event, "answer"):
                message = event

            if message is not None:
                try:
                    await message.answer(
                        "Не удалось выполнить действие. Попробуйте ещё раз."
                    )
                except Exception:
                    logger.exception("Не удалось отправить сообщение об ошибке")
            if hasattr(event, "answer") and event is not message:
                try:
                    await event.answer()
                except Exception:
                    pass
