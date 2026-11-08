import asyncio
import logging
import os
import sqlite3
from collections import Counter
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import DiceEmoji
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

# ==================== КОНФИГ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "stats.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ДАННЫЕ ====================
# Все игры, кроме слотов. value = результат (1..N)
GAMES = {
    "dice": {"emoji": DiceEmoji.DICE, "max": 6, "name": "🎲 Кубик"},
    "dart": {"emoji": DiceEmoji.DART, "max": 6, "name": "🎯 Дартс"},
    "basket": {"emoji": DiceEmoji.BASKETBALL, "max": 5, "name": "🏀 Баскетбол"},
    "football": {"emoji": DiceEmoji.FOOTBALL, "max": 5, "name": "⚽ Футбол"},
    "bowling": {"emoji": DiceEmoji.BOWLING, "max": 6, "name": "🎳 Боулинг"},
}

# Неофициальные расшифровки для баскетбола и футбола (mtcute)
BASKET_MEANINGS = {
    1: "простой промах (отскок влево)",
    2: "попадание в кольцо, затем промах",
    3: "мяч застрял между кольцом и основанием",
    4: "попадание в кольцо, затем гол",
    5: "чистый гол",
}
FOOTBALL_MEANINGS = {
    1: "выше перекладины",
    2: "правая штанга, затем промах",
    3: "прямой гол в центр",
    4: "правая штанга, гол, левая штанга",
    5: "гол в правый верхний угол",
}

# Активные сессии сбора статистики: chat_id -> dict
pending: dict[int, dict] = {}

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS throws (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                game TEXT NOT NULL,
                value INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_game ON throws(user_id, game)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat ON throws(chat_id)")
        conn.commit()
        conn.close()
        log.info("DB initialized at %s", DB_PATH)
    except Exception as e:
        log.error("DB init error: %s", e)

def save_throw(user_id: int, chat_id: int, game: str, value: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO throws (user_id, chat_id, game, value) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, game, value),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("save_throw error: %s", e)

def get_user_stats(user_id: int, game: str) -> list[tuple[int, int]]:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT value, COUNT(*) FROM throws WHERE user_id = ? AND game = ? GROUP BY value",
            (user_id, game),
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error("get_user_stats error: %s", e)
        return []

def get_total_throws(user_id: int, game: str) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT COUNT(*) FROM throws WHERE user_id = ? AND game = ?",
            (user_id, game),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        log.error("get_total_throws error: %s", e)
        return 0

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🎲 Кубик"), KeyboardButton(text="🎯 Дартс")],
        [KeyboardButton(text="🏀 Баскетбол"), KeyboardButton(text="⚽ Футбол")],
        [KeyboardButton(text="🎳 Боулинг")],
        [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_count_keyboard(game: str) -> InlineKeyboardMarkup:
    """Кнопки для выбора количества бросков."""
    buttons = []
    row = []
    for n in [5, 10, 25, 50]:
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"run:{game}:{n}"))
    buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="100 (долго)", callback_data=f"run:{game}:100"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ХЕЛПЕРЫ ====================
def get_meanings(game: str) -> dict[int, str]:
    if game == "basket":
        return BASKET_MEANINGS
    if game == "football":
        return FOOTBALL_MEANINGS
    return {}

def format_stats(game: str, values: list[int], title_prefix: str = "") -> str:
    """Формирует текстовый отчёт по списку значений."""
    if not values:
        return "Нет данных для статистики."

    counter = Counter(values)
    total = len(values)
    meanings = get_meanings(game)
    game_info = GAMES.get(game, {})
    game_name = game_info.get("name", game)

    lines = [f"{title_prefix}{game_name} — {total} бросков:", ""]

    for val in sorted(counter.keys()):
        cnt = counter[val]
        pct = cnt / total * 100
        meaning = meanings.get(val, "")
        suffix = f" — {meaning}" if meaning else ""
        lines.append(f"  {val}{suffix}: {cnt} раз ({pct:.1f}%)")

    # Итог по «попаданиям» для спортивных
    if game in ("basket", "football"):
        goals = sum(c for v, c in counter.items() if v >= 4)
        lines.append("")
        lines.append(f"🎯 Попаданий (4-5): {goals} из {total} ({goals/total*100:.1f}%)")

    # Мода (самое частое)
    most_common = counter.most_common(1)[0]
    lines.append("")
    lines.append(f"📌 Чаще всего: {most_common[0]} ({most_common[1]} раз)")

    return "\n".join(lines)

async def safe_send(chat_id: int, text: str, **kwargs):
    """Безопасная отправка с обработкой ошибок Telegram."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        log.warning("Flood control, retry after %s", e.retry_after)
        await asyncio.sleep(e.retry_after)
        with suppress(Exception):
            await bot.send_message(chat_id, text, **kwargs)
    except TelegramBadRequest as e:
        log.error("Bad request: %s", e)
    except Exception as e:
        log.error("send error: %s", e)

# ==================== ЛОГИКА СБОРА ====================
async def finalize_after_timeout(chat_id: int, delay: int = 4):
    """Если результаты перестали приходить — выводим что есть."""
    await asyncio.sleep(delay)
    data = pending.get(chat_id)
    if not data:
        return
    # Продлеваем, если недавно был результат
    elapsed = asyncio.get_event_loop().time() - data.get("last_update", 0)
    if elapsed < 3:
        data["task"] = asyncio.create_task(finalize_after_timeout(chat_id, 2))
        return
    await send_session_stats(chat_id, data)
    pending.pop(chat_id, None)

async def send_session_stats(chat_id: int, data: dict):
    values = data["values"]
    game = data["game"]
    if not values:
        await safe_send(chat_id, "Не получил ни одного результата. Попробуй ещё раз.")
        return
    text = format_stats(game, values, "📊 Сессия: ")
    await safe_send(chat_id, text)

async def run_game(chat_id: int, user_id: int, game: str, count: int):
    """Отправляет кубики и настраивает сбор."""
    if game not in GAMES:
        return
    info = GAMES[game]
    emoji = info["emoji"]

    # Лимит Telegram: не больше 100 за раз
    if count > 100:
        await safe_send(chat_id, "Максимум 100 бросков за раз (лимит Telegram).")
        return
    if count < 1:
        await safe_send(chat_id, "Минимум 1 бросок.")
        return

    # Если уже есть активная сессия — отменяем таймер
    if chat_id in pending and pending[chat_id].get("task"):
        pending[chat_id]["task"].cancel()

    pending[chat_id] = {
        "user_id": user_id,
        "game": game,
        "emoji": emoji,
        "expected": count,
        "values": [],
        "last_update": asyncio.get_event_loop().time(),
        "task": None,
    }

    await safe_send(chat_id, f"Кидаю {info['name']} {count} раз...")

    for i in range(count):
        try:
            await bot.send_dice(chat_id=chat_id, emoji=emoji)
        except TelegramRetryAfter as e:
            log.warning("Flood during send, waiting %s", e.retry_after)
            await asyncio.sleep(e.retry_after)
            with suppress(Exception):
                await bot.send_dice(chat_id=chat_id, emoji=emoji)
        except Exception as e:
            log.error("send_dice error: %s", e)
            break
        await asyncio.sleep(0.35)  # пауза, чтобы не флудить

    # Запускаем таймер завершения
    pending[chat_id]["task"] = asyncio.create_task(finalize_after_timeout(chat_id))

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я считаю статистику эмодзи-игр.\n\n"
        "Выбери игру кнопкой ниже или напиши команду:\n"
        "/dice 10 — кубик\n"
        "/dart 10 — дартс\n"
        "/basket 10 — баскетбол\n"
        "/football 10 — футбол\n"
        "/bowling 10 — боулинг\n\n"
        "/mystats — моя статистика за всё время\n"
        "/help — помощь\n\n"
        "⚠️ Расшифровка значений для баскетбола и футбола неофициальная.",
        reply_markup=get_main_keyboard(),
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 Как пользоваться:\n\n"
        "1. Нажми кнопку с игрой или напиши /игра число\n"
        "2. Бот кинет эмодзи указанное число раз\n"
        "3. После завершения пришлёт статистику\n\n"
        "Команды:\n"
        "/dice N — кубик 🎲\n"
        "/dart N — дартс 🎯\n"
        "/basket N — баскетбол 🏀\n"
        "/football N — футбол ⚽\n"
        "/bowling N — боулинг 🎳\n"
        "/mystats — статистика за всё время\n\n"
        "Лимит: 100 бросков за раз.",
        reply_markup=get_main_keyboard(),
    )

@dp.message(Command("mystats"))
async def cmd_mystats(message: Message):
    user_id = message.from_user.id
    lines = ["📈 Твоя статистика за всё время:", ""]
    has_data = False
    for game_key, info in GAMES.items():
        total = get_total_throws(user_id, game_key)
        if total == 0:
            continue
        has_data = True
        rows = get_user_stats(user_id, game_key)
        lines.append(f"{info['name']} — {total} бросков:")
        for val, cnt in sorted(rows):
            pct = cnt / total * 100
            lines.append(f"  {val}: {cnt} ({pct:.1f}%)")
        lines.append("")

    if not has_data:
        await message.answer("Пока нет сохранённой статистики. Сыграй во что-нибудь!")
    else:
        await message.answer("\n".join(lines), reply_markup=get_main_keyboard())

# Обработчик кнопок Reply-клавиатуры
@dp.message(F.text.in_({"🎲 Кубик", "🎯 Дартс", "🏀 Баскетбол", "⚽ Футбол", "🎳 Боулинг"}))
async def on_game_button(message: Message):
    mapping = {
        "🎲 Кубик": "dice",
        "🎯 Дартс": "dart",
        "🏀 Баскетбол": "basket",
        "⚽ Футбол": "football",
        "🎳 Боулинг": "bowling",
    }
    game = mapping.get(message.text)
    if game:
        await message.answer(
            f"Сколько раз кинуть {GAMES[game]['name']}?",
            reply_markup=get_count_keyboard(game),
        )

@dp.callback_query(F.data.startswith("run:"))
async def on_run_callback(callback: CallbackQuery):
    try:
        _, game, count_str = callback.data.split(":")
        count = int(count_str)
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        await callback.answer()
        await run_game(chat_id, user_id, game, count)
    except Exception as e:
        log.error("callback error: %s", e)
        with suppress(Exception):
            await callback.answer("Ошибка. Попробуй ещё раз.", show_alert=True)

# Команды /dice, /dart и т.д.
@dp.message(Command("dice", "dart", "basket", "football", "bowling"))
async def on_game_command(message: Message):
    parts = message.text.split()
    game = parts[0][1:]
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(f"Формат: /{game} 10")
        return
    count = int(parts[1])
    await run_game(message.chat.id, message.from_user.id, game, count)

# Сбор результатов
@dp.message(F.dice)
async def on_dice(message: Message):
    chat_id = message.chat.id
    data = pending.get(chat_id)
    if not data:
        return
    # Проверяем, что эмодзи совпадает (по значению enum)
    if message.dice.emoji != data["emoji"].value:
        return

    value = message.dice.value
    data["values"].append(value)
    data["last_update"] = asyncio.get_event_loop().time()
    save_throw(data["user_id"], chat_id, data["game"], value)
    log.info("DICE %s value=%s (%d/%d)", data["game"], value, len(data["values"]), data["expected"])

    # Если собрали все — сразу выводим
    if len(data["values"]) >= data["expected"]:
        if data.get("task"):
            data["task"].cancel()
        await send_session_stats(chat_id, data)
        pending.pop(chat_id, None)

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    log.info("Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
