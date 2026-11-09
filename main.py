import asyncio
import csv
import io
import logging
import os
import sqlite3
from collections import Counter
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import DiceEmoji
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    BufferedInputFile,
)

# ==================== КОНФИГ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "stats.db"
LOG_PATH = DATA_DIR / "bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ДАННЫЕ ====================
GAMES = {
    "dice": {"emoji": DiceEmoji.DICE, "name": "🎲 Кубик"},
    "dart": {"emoji": DiceEmoji.DART, "name": "🎯 Дартс"},
    "basket": {"emoji": DiceEmoji.BASKETBALL, "name": "🏀 Баскетбол"},
    "football": {"emoji": DiceEmoji.FOOTBALL, "name": "⚽ Футбол"},
    "bowling": {"emoji": DiceEmoji.BOWLING, "name": "🎳 Боулинг"},
}

BASKET_MEANINGS = {1: "промах (отскок)", 2: "попадание, затем промах",
                   3: "застрял", 4: "попадание, затем гол", 5: "чистый гол"}
FOOTBALL_MEANINGS = {1: "выше", 2: "штанга, промах", 3: "гол в центр",
                     4: "штанга, гол, штанга", 5: "гол в угол"}

# ==================== БАЗА ====================
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS throws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
            game TEXT NOT NULL, value INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ug ON throws(user_id, game)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON throws(created_at)")
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY, pause REAL DEFAULT 0.4, max_count INTEGER DEFAULT 50)""")
        conn.commit()
        conn.close()
        log.info("DB ready: %s", DB_PATH)
    except Exception as e:
        log.error("DB init: %s", e)

def save_throw(user_id, chat_id, game, value):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO throws (user_id,chat_id,game,value) VALUES (?,?,?,?)",
                     (user_id, chat_id, game, value))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("save: %s", e)

def get_user_stats(user_id, game):
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT value, COUNT(*) FROM throws WHERE user_id=? AND game=? GROUP BY value",
                            (user_id, game)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error("stats: %s", e)
        return []

def get_total(user_id, game):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT COUNT(*) FROM throws WHERE user_id=? AND game=?",
                           (user_id, game)).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        log.error("total: %s", e)
        return 0

def get_top(game, limit=10):
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""SELECT user_id, COUNT(*) FROM throws WHERE game=?
                               GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT ?""",
                            (game, limit)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error("top: %s", e)
        return []

def get_daily(user_id, game, days=1):
    try:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""SELECT value, COUNT(*) FROM throws
                               WHERE user_id=? AND game=? AND created_at>=?
                               GROUP BY value""", (user_id, game, since)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error("daily: %s", e)
        return []

def reset_user(user_id, game=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        if game:
            conn.execute("DELETE FROM throws WHERE user_id=? AND game=?", (user_id, game))
        else:
            conn.execute("DELETE FROM throws WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log.error("reset: %s", e)
        return False

def get_settings(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT pause, max_count FROM settings WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        return row if row else (0.4, 50)
    except Exception as e:
        log.error("settings: %s", e)
        return (0.4, 50)

def save_settings(user_id, pause, max_count):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO settings (user_id,pause,max_count) VALUES (?,?,?)",
                     (user_id, pause, max_count))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("save_settings: %s", e)

# ==================== КЛАВИАТУРЫ ====================
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎲 Кубик"), KeyboardButton(text="🎯 Дартс")],
        [KeyboardButton(text="🏀 Баскетбол"), KeyboardButton(text="⚽ Футбол")],
        [KeyboardButton(text="🎳 Боулинг")],
        [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="🏆 Топ")],
        [KeyboardButton(text="⚙️ Настройки")],
    ], resize_keyboard=True)

def count_kb(game):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(n), callback_data=f"run:{game}:{n}") for n in (1, 3, 5)],
        [InlineKeyboardButton(text="10", callback_data=f"run:{game}:10")],
    ])

def reset_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, сбросить", callback_data="reset:yes"),
         InlineKeyboardButton(text="Отмена", callback_data="reset:no")],
    ])

# ==================== ФОРМАТ ====================
def meanings(game):
    if game == "basket": return BASKET_MEANINGS
    if game == "football": return FOOTBALL_MEANINGS
    return {}

def format_stats(game, values, prefix=""):
    if not values:
        return "Нет данных."
    c = Counter(values)
    total = len(values)
    m = meanings(game)
    name = GAMES.get(game, {}).get("name", game)
    lines = [f"{prefix}{name} — {total} бросков:", ""]
    for val in sorted(c):
        cnt = c[val]
        mean = f" ({m[val]})" if val in m else ""
        lines.append(f"  {val}{mean}: {cnt} ({cnt/total*100:.1f}%)")
    if game in ("basket", "football"):
        goals = sum(n for v, n in c.items() if v >= 4)
        lines.append(f"\nПопаданий (4-5): {goals}/{total} ({goals/total*100:.1f}%)")
    mc = c.most_common(1)[0]
    lines.append(f"\nЧаще всего: {mc[0]} ({mc[1]})")
    return "\n".join(lines)

async def safe_send(chat_id, text, **kw):
    try:
        await bot.send_message(chat_id, text, **kw)
    except TelegramRetryAfter as e:
        log.warning("Flood, wait %s", e.retry_after)
        await asyncio.sleep(e.retry_after)
        with suppress(Exception):
            await bot.send_message(chat_id, text, **kw)
    except TelegramBadRequest as e:
        log.error("BadRequest: %s", e)
    except Exception as e:
        log.error("Send: %s", e)

# ==================== ИГРА (4 СЕКУНДЫ) ====================
async def run_game(chat_id: int, user_id: int, game: str, count: int):
    if game not in GAMES or not 1 <= count <= 50:
        await safe_send(chat_id, "Ошибка: игра или количество (1-50).")
        return

    info = GAMES[game]
    values = []
    pause, _ = get_settings(user_id)

    await safe_send(chat_id, f"Кидаю {info['name']} {count} раз... Это займёт ~{count * 4} сек.")

    for i in range(count):
        try:
            sent = await bot.send_dice(chat_id=chat_id, emoji=info["emoji"])
            await asyncio.sleep(4)

            if sent.dice is None:
                log.warning("dice None (game=%s, i=%d)", game, i)
                continue

            val = sent.dice.value
            if not val:
                log.warning("dice.value=0 (game=%s, i=%d)", game, i)
                continue

            values.append(val)
            save_throw(user_id, chat_id, game, val)
            log.info("DICE %s val=%s (%d/%d)", game, val, len(values), count)

        except TelegramRetryAfter as e:
            log.warning("Flood, wait %s", e.retry_after)
            await asyncio.sleep(e.retry_after)
            with suppress(Exception):
                sent = await bot.send_dice(chat_id=chat_id, emoji=info["emoji"])
                await asyncio.sleep(4)
                if sent.dice and sent.dice.value:
                    values.append(sent.dice.value)
                    save_throw(user_id, chat_id, game, sent.dice.value)

        except Exception as e:
            log.error("dice: %s", e)
            break

        await asyncio.sleep(pause)

    if not values:
        await safe_send(chat_id, "Не получил результатов. Попробуй ещё раз.")
        return

    await safe_send(chat_id, format_stats(game, values, "📊 "))

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "👋 Привет! Я считаю статистику эмодзи-игр.\n"
        "Выбери игру кнопкой или командой /game N\n\n"
        "/mystats — статистика за всё время\n"
        "/daily — за сегодня\n"
        "/top — лидерборд\n"
        "/export — CSV\n"
        "/reset — сброс\n"
        "/settings — настройки",
        reply_markup=main_kb())

@dp.message(Command("mystats"))
async def cmd_mystats(m: Message):
    uid = m.from_user.id
    lines = ["📈 Статистика за всё время:"]
    has = False
    for g, info in GAMES.items():
        t = get_total(uid, g)
        if t:
            has = True
            rows = get_user_stats(uid, g)
            lines.append(f"\n{info['name']} ({t}):")
            for v, c in sorted(rows):
                lines.append(f"  {v}: {c} ({c/t*100:.1f}%)")
    await m.answer("\n".join(lines) if has else "Нет данных. Сыграй!", reply_markup=main_kb())

@dp.message(Command("daily"))
async def cmd_daily(m: Message):
    uid = m.from_user.id
    lines = ["📅 За сегодня:"]
    has = False
    for g, info in GAMES.items():
        rows = get_daily(uid, g, 1)
        if rows:
            has = True
            t = sum(c for _, c in rows)
            lines.append(f"\n{info['name']} ({t}):")
            for v, c in sorted(rows):
                lines.append(f"  {v}: {c} ({c/t*100:.1f}%)")
    await m.answer("\n".join(lines) if has else "Сегодня ещё не играл.", reply_markup=main_kb())

@dp.message(Command("top"))
async def cmd_top(m: Message):
    lines = ["🏆 Топ игроков:"]
    for g, info in GAMES.items():
        rows = get_top(g, 5)
        if rows:
            lines.append(f"\n{info['name']}:")
            for i, (uid, cnt) in enumerate(rows, 1):
                lines.append(f"  {i}. user {uid} — {cnt}")
    await m.answer("\n".join(lines) if len(lines) > 1 else "Пока нет данных.", reply_markup=main_kb())

@dp.message(Command("export"))
async def cmd_export(m: Message):
    uid = m.from_user.id
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT game, value, created_at FROM throws WHERE user_id=? ORDER BY created_at",
                            (uid,)).fetchall()
        conn.close()
        if not rows:
            await m.answer("Нет данных для экспорта.")
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["game", "value", "created_at"])
        w.writerows(rows)
        data = buf.getvalue().encode("utf-8")
        await m.answer_document(BufferedInputFile(data, filename="stats.csv"))
    except Exception as e:
        log.error("export: %s", e)
        await m.answer("Ошибка экспорта.")

@dp.message(Command("reset"))
async def cmd_reset(m: Message):
    await m.answer("Сбросить всю статистику?", reply_markup=reset_kb())

@dp.callback_query(F.data.startswith("reset:"))
async def on_reset(c: CallbackQuery):
    await c.answer()
    if c.data == "reset:yes":
        reset_user(c.from_user.id)
        await c.message.answer("Статистика сброшена.", reply_markup=main_kb())
    else:
        await c.message.answer("Отменено.", reply_markup=main_kb())

@dp.message(Command("settings"))
async def cmd_settings(m: Message):
    pause, max_count = get_settings(m.from_user.id)
    await m.answer(f"⚙️ Пауза: {pause} сек, лимит: {max_count}.\n"
                   "Изменить: /setpause 0.5 или /setlimit 30", reply_markup=main_kb())

@dp.message(Command("setpause"))
async def cmd_setpause(m: Message):
    p = m.text.split()
    if len(p) < 2:
        await m.answer("Формат: /setpause 0.5")
        return
    try:
        pause = float(p[1])
        if not 0.1 <= pause <= 5:
            raise ValueError
    except ValueError:
        await m.answer("Пауза от 0.1 до 5.")
        return
    _, lim = get_settings(m.from_user.id)
    save_settings(m.from_user.id, pause, lim)
    await m.answer(f"Пауза: {pause}")

@dp.message(Command("setlimit"))
async def cmd_setlimit(m: Message):
    p = m.text.split()
    if len(p) < 2 or not p[1].isdigit():
        await m.answer("Формат: /setlimit 30")
        return
    lim = int(p[1])
    if not 1 <= lim <= 50:
        await m.answer("Лимит от 1 до 50.")
        return
    pause, _ = get_settings(m.from_user.id)
    save_settings(m.from_user.id, pause, lim)
    await m.answer(f"Лимит: {lim}")

@dp.message(F.text.in_({"🎲 Кубик", "🎯 Дартс", "🏀 Баскетбол", "⚽ Футбол", "🎳 Боулинг"}))
async def on_btn(m: Message):
    mp = {"🎲 Кубик": "dice", "🎯 Дартс": "dart", "🏀 Баскетбол": "basket",
          "⚽ Футбол": "football", "🎳 Боулинг": "bowling"}
    g = mp.get(m.text)
    if g:
        await m.answer("Сколько раз?", reply_markup=count_kb(g))

@dp.message(F.text == "📊 Моя статистика")
async def on_stats_btn(m: Message):
    await cmd_mystats(m)

@dp.message(F.text == "🏆 Топ")
async def on_top_btn(m: Message):
    await cmd_top(m)

@dp.message(F.text == "⚙️ Настройки")
async def on_settings_btn(m: Message):
    await cmd_settings(m)

@dp.callback_query(F.data.startswith("run:"))
async def on_cb(c: CallbackQuery):
    try:
        _, g, n = c.data.split(":")
        await c.answer()
        await run_game(c.message.chat.id, c.from_user.id, g, int(n))
    except Exception as e:
        log.error("cb: %s", e)
        with suppress(Exception):
            await c.answer("Ошибка", show_alert=True)

@dp.message(Command("dice", "dart", "basket", "football", "bowling"))
async def on_cmd(m: Message):
    p = m.text.split()
    g = p[0][1:]
    if len(p) < 2 or not p[1].isdigit():
        await m.answer(f"Формат: /{g} 10")
        return
    _, lim = get_settings(m.from_user.id)
    cnt = min(int(p[1]), lim)
    await run_game(m.chat.id, m.from_user.id, g, cnt)

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    log.info("Starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
