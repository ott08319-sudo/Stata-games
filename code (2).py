import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = os.getenv("DB_FILE", "redcube.sqlite3")

START_BALANCE = 1000
DAILY_BONUS = 250
GAME_COST = 50
WIN_REWARD = 250

logging.basicConfig(level=logging.INFO)


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                balance INTEGER NOT NULL DEFAULT 1000,
                games INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                referrals INTEGER NOT NULL DEFAULT 0,
                last_bonus TEXT,
                referrer_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with db() as con:
        return con.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def ensure_user(message: Message, referrer_id: Optional[int] = None) -> None:
    user = message.from_user
    if not user:
        return

    with db() as con:
        exists = con.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user.id,)
        ).fetchone()

        if exists:
            con.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (user.username or "", user.first_name or "", user.id),
            )
            return

        valid_referrer = None
        if referrer_id and referrer_id != user.id:
            if con.execute(
                "SELECT user_id FROM users WHERE user_id = ?", (referrer_id,)
            ).fetchone():
                valid_referrer = referrer_id

        con.execute(
            """INSERT INTO users
               (user_id, username, first_name, balance, referrer_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user.id,
                user.username or "",
                user.first_name or "",
                START_BALANCE,
                valid_referrer,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        if valid_referrer:
            con.execute(
                """UPDATE users
                   SET referrals = referrals + 1, balance = balance + 100
                   WHERE user_id = ?""",
                (valid_referrer,),
            )


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Играть", callback_data="game")],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
            InlineKeyboardButton(text="🎁 Бонус", callback_data="bonus"),
        ],
        [
            InlineKeyboardButton(text="🏆 Лидеры", callback_data="leaders"),
            InlineKeyboardButton(text="👥 Пригласить", callback_data="referral"),
        ],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]
    ])


def welcome_text(name: str) -> str:
    return (
        "🔴 <b>Red Cube</b>\n\n"
        f"Привет, {name}! Здесь можно играть, получать бонусы "
        "и соревноваться в рейтинге.\n\n"
        f"💰 Стартовый баланс: <b>{START_BALANCE} монет</b>\n"
        "⚠️ Монеты виртуальные и не имеют денежной стоимости."
    )


async def start(message: Message) -> None:
    referrer = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            try:
                referrer = int(parts[1][4:])
            except ValueError:
                pass

    ensure_user(message, referrer)
    name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(welcome_text(name), reply_markup=main_keyboard())


async def menu(call: CallbackQuery) -> None:
    name = call.from_user.first_name or "друг"
    await call.message.edit_text(
        welcome_text(name), reply_markup=main_keyboard()
    )
    await call.answer()


async def profile(call: CallbackQuery) -> None:
    user = get_user(call.from_user.id)
    if not user:
        await call.answer("Напиши /start, чтобы создать профиль.", show_alert=True)
        return

    winrate = round(user["wins"] / user["games"] * 100) if user["games"] else 0
    text = (
        "👤 <b>Твой профиль</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"💰 Баланс: <b>{user['balance']}</b> монет\n"
        f"🎮 Игр: {user['games']}\n"
        f"🏆 Побед: {user['wins']}\n"
        f"📈 Винрейт: {winrate}%\n"
        f"👥 Рефералов: {user['referrals']}"
    )
    await call.message.edit_text(text, reply_markup=back_keyboard())
    await call.answer()


async def bonus(call: CallbackQuery) -> None:
    today = datetime.now(timezone.utc).date().isoformat()

    with db() as con:
        user = con.execute(
            "SELECT last_bonus FROM users WHERE user_id = ?",
            (call.from_user.id,),
        ).fetchone()

        if not user:
            await call.answer("Напиши /start, чтобы создать профиль.", show_alert=True)
            return

        if user["last_bonus"] == today:
            text = "🎁 Ты уже забрал бонус сегодня. Возвращайся завтра!"
        else:
            con.execute(
                """UPDATE users SET balance = balance + ?, last_bonus = ?
                   WHERE user_id = ?""",
                (DAILY_BONUS, today, call.from_user.id),
            )
            text = f"🎁 Бонус получен: <b>{DAILY_BONUS}</b> виртуальных монет!"

    await call.message.edit_text(text, reply_markup=back_keyboard())
    await call.answer()


async def game(call: CallbackQuery) -> None:
    user = get_user(call.from_user.id)
    if not user:
        await call.answer("Напиши /start, чтобы создать профиль.", show_alert=True)
        return

    if user["balance"] < GAME_COST:
        await call.answer("Недостаточно виртуальных монет.", show_alert=True)
        return

    with db() as con:
        con.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (GAME_COST, call.from_user.id),
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=str(n), callback_data=f"guess:{n}")
            for n in range(1, 4)
        ],
        [
            InlineKeyboardButton(text=str(n), callback_data=f"guess:{n}")
            for n in range(4, 7)
        ],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ])

    await call.message.edit_text(
        "🎲 <b>Угадай число</b>\n\n"
        "Выбери число от 1 до 6.\n"
        f"Стоимость игры: {GAME_COST} монет.\n"
        f"За победу: +{WIN_REWARD} монет.",
        reply_markup=keyboard,
    )
    await call.answer()


async def guess(call: CallbackQuery) -> None:
    try:
        selected = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Некорректный выбор.", show_alert=True)
        return

    if selected not in range(1, 7):
        await call.answer("Некорректный выбор.", show_alert=True)
        return

    secret = random.randint(1, 6)
    won = selected == secret

    with db() as con:
        con.execute(
            """UPDATE users
               SET games = games + 1, wins = wins + ?, balance = balance + ?
               WHERE user_id = ?""",
            (int(won), WIN_REWARD if won else 0, call.from_user.id),
        )
        user = con.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (call.from_user.id,),
        ).fetchone()

    if won:
        text = (
            f"🎉 <b>Победа!</b> Выпало число {secret}.\n"
            f"Начислено {WIN_REWARD} монет."
        )
    else:
        text = f"🙂 Выпало число {secret}. В этот раз не угадал!"

    text += f"\n\n💰 Баланс: {user['balance']}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Ещё раз", callback_data="game")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ])

    await call.message.edit_text(text, reply_markup=keyboard)
    await call.answer()


async def leaders(call: CallbackQuery) -> None:
    with db() as con:
        rows = con.execute(
            """SELECT first_name, username, balance FROM users
               ORDER BY balance DESC LIMIT 10"""
        ).fetchall()

    lines = ["🏆 <b>Топ игроков</b>", ""]
    for index, row in enumerate(rows, 1):
        name = f"@{row['username']}" if row["username"] else row["first_name"] or "Игрок"
        lines.append(f"{index}. {name} — <b>{row['balance']}</b> монет")

    await call.message.edit_text("\n".join(lines), reply_markup=back_keyboard())
    await call.answer()


async def referral(call: CallbackQuery) -> None:
    me = await call.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{call.from_user.id}"

    text = (
        "👥 <b>Приглашай друзей</b>\n\n"
        "За каждого нового игрока начисляется 100 виртуальных монет.\n\n"
        f"Твоя ссылка:\n<code>{link}</code>"
    )
    await call.message.edit_text(text, reply_markup=back_keyboard())
    await call.answer()


async def help_page(call: CallbackQuery) -> None:
    text = (
        "ℹ️ <b>Как играть</b>\n\n"
        f"• Стартовый баланс — {START_BALANCE} монет.\n"
        f"• Ежедневный бонус — {DAILY_BONUS} монет.\n"
        f"• Одна игра стоит {GAME_COST} монет.\n"
        f"• За угаданное число начисляется {WIN_REWARD} монет.\n\n"
        "Монеты виртуальные: их нельзя вывести или обменять на деньги."
    )
    await call.message.edit_text(text, reply_markup=back_keyboard())
    await call.answer()


async def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "Задай переменную BOT_TOKEN с токеном от @BotFather."
        )

    init_db()
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.register(start, CommandStart())
    dp.message.register(start, Command("menu"))
    dp.callback_query.register(menu, F.data == "menu")
    dp.callback_query.register(profile, F.data == "profile")
    dp.callback_query.register(bonus, F.data == "bonus")
    dp.callback_query.register(game, F.data == "game")
    dp.callback_query.register(guess, F.data.startswith("guess:"))
    dp.callback_query.register(leaders, F.data == "leaders")
    dp.callback_query.register(referral, F.data == "referral")
    dp.callback_query.register(help_page, F.data == "help")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())