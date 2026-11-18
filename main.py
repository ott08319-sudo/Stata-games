import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import DiceEmoji, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ==========================================
# КОНФИГУРАЦИЯ БОТА (RED CUBE / SPINS STYLE)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = {123456789}  # Замените на свой Telegram ID
DB_PATH = "red_cube_spins.db"

START_BALANCE = 1000.0
DAILY_BONUS_AMOUNT = 500.0
REFERRAL_BONUS = 250.0
CURRENCY = "💎"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger("RedCubeSpinsBot")

router = Router()


# ==========================================
# БАЗА ДАННЫХ (SQLite)
# ==========================================
class Database:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    balance REAL DEFAULT 1000.0,
                    total_won REAL DEFAULT 0.0,
                    total_lost REAL DEFAULT 0.0,
                    games_played INTEGER DEFAULT 0,
                    referrer_id INTEGER DEFAULT NULL,
                    referrals_count INTEGER DEFAULT 0,
                    last_bonus TEXT DEFAULT NULL,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS promocodes (
                    code TEXT PRIMARY KEY,
                    reward REAL NOT NULL,
                    uses_left INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_activations (
                    user_id INTEGER,
                    code TEXT,
                    PRIMARY KEY (user_id, code)
                );

                CREATE TABLE IF NOT EXISTS duels (
                    duel_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    creator_id INTEGER,
                    opponent_id INTEGER DEFAULT NULL,
                    bet REAL,
                    status TEXT DEFAULT 'open',
                    created_at TEXT
                );
                """
            )

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def register_user(
        self,
        user_id: int,
        username: Optional[str],
        full_name: str,
        referrer_id: Optional[int] = None,
    ) -> bool:
        if self.get_user(user_id):
            with self._connect() as conn:
                conn.execute(
                    "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                    (username, full_name, user_id),
                )
            return False

        valid_ref = None
        if referrer_id and referrer_id != user_id and self.get_user(referrer_id):
            valid_ref = referrer_id

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, full_name, balance, referrer_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    full_name,
                    START_BALANCE,
                    valid_ref,
                    datetime.utcnow().isoformat(),
                ),
            )
            if valid_ref:
                conn.execute(
                    """
                    UPDATE users
                    SET balance = balance + ?, referrals_count = referrals_count + 1
                    WHERE user_id = ?
                    """,
                    (REFERRAL_BONUS, valid_ref),
                )
        return True

    def update_balance(self, user_id: int, delta: float):
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 2) WHERE user_id = ?",
                (delta, user_id),
            )

    def record_game(self, user_id: int, bet: float, payout: float):
        net = round(payout - bet, 2)
        won = max(0.0, net)
        lost = max(0.0, -net)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET balance = ROUND(balance + ?, 2),
                    total_won = ROUND(total_won + ?, 2),
                    total_lost = ROUND(total_lost + ?, 2),
                    games_played = games_played + 1
                WHERE user_id = ?
                """,
                (net, won, lost, user_id),
            )

    def claim_daily_bonus(self, user_id: int) -> tuple[bool, str]:
        user = self.get_user(user_id)
        now = datetime.utcnow()
        if user and user["last_bonus"]:
            last = datetime.fromisoformat(user["last_bonus"])
            diff = now - last
            if diff < timedelta(hours=24):
                rem = timedelta(hours=24) - diff
                hours, remainder = divmod(int(rem.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                return False, f"{hours}ч {minutes}м"

        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
                (DAILY_BONUS_AMOUNT, now.isoformat(), user_id),
            )
        return True, ""

    def get_top_players(self, limit: int = 10):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM users ORDER BY balance DESC LIMIT ?", (limit,)
            ).fetchall()

    def create_promo(self, code: str, reward: float, uses: int):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO promocodes (code, reward, uses_left) VALUES (?, ?, ?)",
                (code.upper(), reward, uses),
            )

    def activate_promo(self, user_id: int, code: str) -> tuple[bool, str]:
        code = code.upper().strip()
        with self._connect() as conn:
            promo = conn.execute(
                "SELECT * FROM promocodes WHERE code = ?", (code,)
            ).fetchone()
            if not promo:
                return False, "❌ Промокод не найден."
            if promo["uses_left"] <= 0:
                return False, "❌ Лимит активаций этого промокода исчерпан."
            used = conn.execute(
                "SELECT 1 FROM promo_activations WHERE user_id = ? AND code = ?",
                (user_id, code),
            ).fetchone()
            if used:
                return False, "❌ Вы уже активировали этот промокод."

            conn.execute(
                "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
                (user_id, code),
            )
            conn.execute(
                "UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?",
                (code,),
            )
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (promo["reward"], user_id),
            )
            return True, f"✅ Промокод <b>{code}</b> активирован! Начислено <b>{promo['reward']} {CURRENCY}</b>."

    def create_duel(self, chat_id: int, creator_id: int, bet: float) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET balance = ROUND(balance - ?, 2) WHERE user_id = ?",
                (bet, creator_id),
            )
            cur = conn.execute(
                """
                INSERT INTO duels (chat_id, creator_id, bet, status, created_at)
                VALUES (?, ?, ?, 'open', ?)
                """,
                (chat_id, creator_id, bet, datetime.utcnow().isoformat()),
            )
            return cur.lastrowid

    def get_duel(self, duel_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM duels WHERE duel_id = ?", (duel_id,)
            ).fetchone()

    def close_duel(self, duel_id: int, status: str, opponent_id: Optional[int] = None):
        with self._connect() as conn:
            conn.execute(
                "UPDATE duels SET status = ?, opponent_id = ? WHERE duel_id = ?",
                (status, opponent_id, duel_id),
            )


db = Database(DB_PATH)


# ==========================================
# FSM СОСТОЯНИЯ
# ==========================================
class GameStates(StatesGroup):
    waiting_bet = State()
    waiting_promo = State()


# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎲 Играть"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="🏆 Топ игроков")],
            [KeyboardButton(text="🎟 Промокод"), KeyboardButton(text="👥 Рефералы")],
        ],
        resize_keyboard=True,
    )


def games_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟥 Красный Куб (🎲)", callback_data="game:cube"),
                InlineKeyboardButton(text="🎰 Spins Слот (🎰)", callback_data="game:spins"),
            ],
            [
                InlineKeyboardButton(text="🎯 Дартс (x2.5)", callback_data="game:darts"),
                InlineKeyboardButton(text="🎳 Боулинг (x2.5)", callback_data="game:bowling"),
            ],
            [
                InlineKeyboardButton(text="🏀 Баскетбол (x2.0)", callback_data="game:basketball"),
                InlineKeyboardButton(text="⚽ Футбол (x1.8)", callback_data="game:football"),
            ],
        ]
    )


def cube_modes_kb(bet: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📉 Меньше 1-3 (x1.9)", callback_data=f"play:cube:low:{bet}"),
                InlineKeyboardButton(text="📈 Больше 4-6 (x1.9)", callback_data=f"play:cube:high:{bet}"),
            ],
            [
                InlineKeyboardButton(text="🔴 Чётное (x1.9)", callback_data=f"play:cube:even:{bet}"),
                InlineKeyboardButton(text="⚫ Нечётное (x1.9)", callback_data=f"play:cube:odd:{bet}"),
            ],
            [
                InlineKeyboardButton(text="🎯 Точное число 6 (x5.0)", callback_data=f"play:cube:six:{bet}"),
            ],
            [
                InlineKeyboardButton(text="💰 Изменить ставку", callback_data="game:cube"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="menu:games"),
            ],
        ]
    )


def bet_selector_kb(game_type: str, balance: float) -> InlineKeyboardMarkup:
    presets = [50, 100, 250, 500, 1000]
    row1 = [
        InlineKeyboardButton(text=f"{b} {CURRENCY}", callback_data=f"bet:{game_type}:{b}")
        for b in presets[:3]
    ]
    row2 = [
        InlineKeyboardButton(text=f"{b} {CURRENCY}", callback_data=f"bet:{game_type}:{b}")
        for b in presets[3:]
    ]
    all_in = max(10.0, round(balance, 2))
    row2.append(InlineKeyboardButton(text="🔥 Ва-банк", callback_data=f"bet:{game_type}:{all_in}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row1,
            row2,
            [
                InlineKeyboardButton(text="✏️ Своя ставка", callback_data=f"custom_bet:{game_type}"),
                InlineKeyboardButton(text="🔙 К играм", callback_data="menu:games"),
            ],
        ]
    )


def repeat_game_kb(game_type: str, mode: str, bet: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔄 Повторить ({bet} {CURRENCY})",
                    callback_data=f"play:{game_type}:{mode}:{bet}",
                ),
                InlineKeyboardButton(
                    text="✖️2 Удвоить",
                    callback_data=f"play:{game_type}:{mode}:{round(bet * 2, 2)}",
                ),
            ],
            [
                InlineKeyboardButton(text="💰 Новая ставка", callback_data=f"game:{game_type}"),
                InlineKeyboardButton(text="🎮 Все игры", callback_data="menu:games"),
            ],
        ]
    )


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def format_name(user_row: sqlite3.Row) -> str:
    if user_row["username"]:
        return f"@{user_row['username']}"
    return user_row["full_name"]


def decode_slot_value(value: int) -> tuple[str, float]:
    """
    Декодирование результата Telegram 🎰 (значения 1..64).
    В Telegram 64 = 777, 1 = BAR-BAR-BAR, 22 = Виноград x3, 43 = Лимон x3.
    """
    if value == 64:
        return "7️⃣7️⃣7️⃣ ДЖЕКПОТ!", 10.0
    if value in (1, 22, 43):
        return "🍒 Три в ряд!", 5.0

    # Проверка двух одинаковых символов по 2-битным барабанам (0..3)
    b1 = (value - 1) & 3
    b2 = ((value - 1) >> 2) & 3
    b3 = ((value - 1) >> 4) & 3
    if b1 == b2 or b2 == b3 or b1 == b3:
        return "✨ Двойное совпадение!", 1.5

    return "💨 Мимо", 0.0


# ==========================================
# ОБРАБОТЧИКИ КОМАНД И МЕНЮ
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    ref_id = None
    if command.args and command.args.isdigit():
        ref_id = int(command.args)

    is_new = db.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_id=ref_id,
    )

    user = db.get_user(message.from_user.id)
    welcome_bonus_text = (
        f"\n🎉 Вам начислен стартовый бонус: <b>{START_BALANCE} {CURRENCY}</b>!"
        if is_new
        else ""
    )

    await message.answer(
        f"🟥 <b>RED CUBE & SPINS</b> 🎰\n\n"
        f"Добро пожаловать, <b>{message.from_user.full_name}</b>!{welcome_bonus_text}\n"
        f"Ваш баланс: <b>{user['balance']} {CURRENCY}</b>\n\n"
        f"🎮 <b>Доступные режимы:</b>\n"
        f"• <b>Красный Куб (🎲)</b> — угадай исход броска\n"
        f"• <b>Spins Слот (🎰)</b> — крути барабан до x10\n"
        f"• <b>Спорт-игры (🎯🎳🏀⚽)</b> — быстрые множители\n"
        f"• <b>Дуэли в чатах:</b> <code>/duel 100</code>\n\n"
        f"Выберите действие в меню ниже 👇",
        reply_markup=main_reply_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text.in_({"🎲 Играть", "/play", "/games"}))
async def menu_games_msg(message: Message, state: FSMContext):
    await state.clear()
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"🎮 <b>Игровой зал Red Cube / Spins</b>\n"
        f"💰 Ваш баланс: <b>{user['balance']} {CURRENCY}</b>\n\n"
        f"Выберите игру:",
        reply_markup=games_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "menu:games")
async def menu_games_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = db.get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🎮 <b>Игровой зал Red Cube / Spins</b>\n"
        f"💰 Ваш баланс: <b>{user['balance']} {CURRENCY}</b>\n\n"
        f"Выберите игру:",
        reply_markup=games_menu_kb(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(F.text.in_({"👤 Профиль", "/profile", "б", "баланс", "Б", "Баланс"}))
async def cmd_profile(message: Message):
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"👤 <b>Профиль игрока</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"💰 Баланс: <b>{user['balance']} {CURRENCY}</b>\n"
        f"🎲 Сыграно игр: <b>{user['games_played']}</b>\n"
        f"📈 Всего выиграно: <b>{user['total_won']} {CURRENCY}</b>\n"
        f"📉 Всего проиграно: <b>{user['total_lost']} {CURRENCY}</b>\n"
        f"👥 Рефералов: <b>{user['referrals_count']}</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text.in_({"🎁 Бонус", "/bonus"}))
async def cmd_bonus(message: Message):
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    ok, wait_time = db.claim_daily_bonus(message.from_user.id)
    if ok:
        user = db.get_user(message.from_user.id)
        await message.answer(
            f"🎁 Вы получили ежедневный бонус <b>+{DAILY_BONUS_AMOUNT} {CURRENCY}</b>!\n"
            f"💰 Текущий баланс: <b>{user['balance']} {CURRENCY}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            f"⏳ Вы уже забирали бонус!\nСледующий будет доступен через <b>{wait_time}</b>.",
            parse_mode=ParseMode.HTML,
        )


@router.message(F.text.in_({"🏆 Топ игроков", "/top", "топ", "Топ"}))
async def cmd_top(message: Message):
    top = db.get_top_players(10)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ-10 богатейших игроков:</b>\n"]
    for idx, row in enumerate(top, start=1):
        prefix = medals[idx - 1] if idx <= 3 else f"<b>{idx}.</b>"
        lines.append(f"{prefix} {format_name(row)} — <b>{row['balance']} {CURRENCY}</b>")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(F.text.in_({"👥 Рефералы", "/ref"}))
async def cmd_ref(message: Message, bot: Bot):
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={message.from_user.id}"
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте <b>+{REFERRAL_BONUS} {CURRENCY}</b> за каждого нового игрока!\n\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"📊 Приглашено друзей: <b>{user['referrals_count']}</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text.in_({"🎟 Промокод", "/promo"}))
async def cmd_promo(message: Message, state: FSMContext):
    await state.set_state(GameStates.waiting_promo)
    await message.answer("🎟 Введите промокод:")


@router.message(GameStates.waiting_promo)
async def process_promo(message: Message, state: FSMContext):
    await state.clear()
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    _, text = db.activate_promo(message.from_user.id, message.text or "")
    await message.answer(text, parse_mode=ParseMode.HTML)


# ==========================================
# ВЫБОР ИГРЫ И СТАВКИ
# ==========================================
GAME_TITLES = {
    "cube": "🟥 Красный Куб (🎲)",
    "spins": "🎰 Spins Слот (🎰)",
    "darts": "🎯 Дартс (попадание 4-6 → x2.5)",
    "bowling": "🎳 Боулинг (страйк 6 → x3.0, 5 → x1.5)",
    "basketball": "🏀 Баскетбол (попадание 4-5 → x2.0)",
    "football": "⚽ Футбол (гол 3-5 → x1.8)",
}


@router.callback_query(F.data.startswith("game:"))
async def select_game_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    game_type = callback.data.split(":")[1]
    user = db.get_user(callback.from_user.id)
    title = GAME_TITLES.get(game_type, game_type)

    await callback.message.edit_text(
        f"{title}\n"
        f"💰 Ваш баланс: <b>{user['balance']} {CURRENCY}</b>\n\n"
        f"Выберите размер ставки:",
        reply_markup=bet_selector_kb(game_type, user["balance"]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("custom_bet:"))
async def custom_bet_cb(callback: CallbackQuery, state: FSMContext):
    game_type = callback.data.split(":")[1]
    await state.set_state(GameStates.waiting_bet)
    await state.update_data(game_type=game_type)
    await callback.message.answer("✏️ Введите сумму ставки числом (например, <code>150</code>):", parse_mode=ParseMode.HTML)
    await callback.answer()


@router.message(GameStates.waiting_bet)
async def process_custom_bet(message: Message, state: FSMContext):
    data = await state.get_data()
    game_type = data.get("game_type", "cube")
    await state.clear()

    try:
        bet = round(float((message.text or "").replace(",", ".")), 2)
    except ValueError:
        await message.answer("❌ Некорректная сумма ставки.")
        return

    user = db.get_user(message.from_user.id)
    if bet < 10:
        await message.answer(f"❌ Минимальная ставка: <b>10 {CURRENCY}</b>.", parse_mode=ParseMode.HTML)
        return
    if bet > user["balance"]:
        await message.answer("❌ Недостаточно средств на балансе.")
        return

    if game_type == "cube":
        await message.answer(
            f"🟥 <b>Красный Куб</b> | Ставка: <b>{bet} {CURRENCY}</b>\n"
            f"Выберите исход броска:",
            reply_markup=cube_modes_kb(bet),
            parse_mode=ParseMode.HTML,
        )
    else:
        await execute_single_game(message, message.from_user.id, game_type, "default", bet)


@router.callback_query(F.data.startswith("bet:"))
async def preset_bet_cb(callback: CallbackQuery):
    _, game_type, bet_str = callback.data.split(":")
    bet = float(bet_str)
    user = db.get_user(callback.from_user.id)

    if bet < 10 or bet > user["balance"]:
        await callback.answer("❌ Недостаточно средств для этой ставки!", show_alert=True)
        return

    if game_type == "cube":
        await callback.message.edit_text(
            f"🟥 <b>Красный Куб</b> | Ставка: <b>{bet} {CURRENCY}</b>\n"
            f"Выберите исход броска:",
            reply_markup=cube_modes_kb(bet),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
    else:
        await callback.answer()
        await execute_single_game(callback.message, callback.from_user.id, game_type, "default", bet)


# ==========================================
# ИГРОВАЯ МЕХАНИКА (DICE & SPINS)
# ==========================================
@router.callback_query(F.data.startswith("play:"))
async def play_game_cb(callback: CallbackQuery):
    _, game_type, mode, bet_str = callback.data.split(":")
    bet = float(bet_str)
    user = db.get_user(callback.from_user.id)

    if bet < 10 or bet > user["balance"]:
        await callback.answer("❌ Недостаточно средств!", show_alert=True)
        return

    await callback.answer()
    await execute_single_game(callback.message, callback.from_user.id, game_type, mode, bet)


async def execute_single_game(
    target_msg: Message, user_id: int, game_type: str, mode: str, bet: float
):
    emoji_map = {
        "cube": DiceEmoji.DICE,
        "spins": DiceEmoji.SLOT_MACHINE,
        "darts": DiceEmoji.DARTS,
        "bowling": DiceEmoji.BOWLING,
        "basketball": DiceEmoji.BASKETBALL,
        "football": DiceEmoji.FOOTBALL,
    }
    dice_emoji = emoji_map.get(game_type, DiceEmoji.DICE)

    # Отправляем нативный анимированный эмодзи Telegram
    dice_msg = await target_msg.answer_dice(emoji=dice_emoji)
    val = dice_msg.dice.value

    multiplier = 0.0
    outcome_desc = ""

    if game_type == "cube":
        if mode == "low" and val in (1, 2, 3):
            multiplier = 1.9
        elif mode == "high" and val in (4, 5, 6):
            multiplier = 1.9
        elif mode == "even" and val % 2 == 0:
            multiplier = 1.9
        elif mode == "odd" and val % 2 == 1:
            multiplier = 1.9
        elif mode == "six" and val == 6:
            multiplier = 5.0
        outcome_desc = f"Выпало число: <b>{val}</b>"

    elif game_type == "spins":
        outcome_desc, multiplier = decode_slot_value(val)

    elif game_type == "darts":
        if val == 6:
            multiplier = 3.0
            outcome_desc = "🎯 В яблочко!"
        elif val in (4, 5):
            multiplier = 1.8
            outcome_desc = "🎯 Хорошее попадание!"
        else:
            outcome_desc = "💨 Мимо центра"

    elif game_type == "bowling":
        if val == 6:
            multiplier = 3.0
            outcome_desc = "🎳 СТРАЙК!"
        elif val == 5:
            multiplier = 1.5
            outcome_desc = "🎳 Почти страйк (5 кеглей)"
        else:
            outcome_desc = f"🎳 Сбито кеглей: {val}"

    elif game_type == "basketball":
        if val in (4, 5):
            multiplier = 2.0
            outcome_desc = "🏀 Точный бросок в кольцо!"
        else:
            outcome_desc = "💨 Мяч не попал в кольцо"

    elif game_type == "football":
        if val in (3, 4, 5):
            multiplier = 1.8
            outcome_desc = "⚽ ГООООЛ!"
        else:
            outcome_desc = "🧤 Вратарь отбил мяч"

    payout = round(bet * multiplier, 2)
    db.record_game(user_id, bet, payout)
    updated_user = db.get_user(user_id)

    # Небольшая пауза под анимацию броска в клиенте Telegram
    await asyncio.sleep(2.5)

    if payout > 0:
        result_header = f"🎉 <b>ПОБЕДА! +{payout} {CURRENCY}</b> (x{multiplier})"
    else:
        result_header = f"💔 <b>ПРОИГРЫШ! -{bet} {CURRENCY}</b>"

    await target_msg.answer(
        f"{result_header}\n"
        f"{outcome_desc}\n"
        f"💰 Баланс: <b>{updated_user['balance']} {CURRENCY}</b>",
        reply_markup=repeat_game_kb(game_type, mode, bet),
        parse_mode=ParseMode.HTML,
    )


# ==========================================
# PVP ДУЭЛИ В ЧАТАХ (/duel)
# ==========================================
@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject):
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = db.get_user(message.from_user.id)

    if not command.args:
        await message.answer("⚔️ Использование: <code>/duel 100</code>", parse_mode=ParseMode.HTML)
        return

    try:
        bet = round(float(command.args.replace(",", ".")), 2)
    except ValueError:
        await message.answer("❌ Укажите корректную ставку.")
        return

    if bet < 10 or bet > user["balance"]:
        await message.answer("❌ Недостаточно средств или ставка меньше 10.")
        return

    duel_id = db.create_duel(message.chat.id, message.from_user.id, bet)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚔️ Принять дуэль ({bet} {CURRENCY})",
                    callback_data=f"duel_accept:{duel_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"duel_cancel:{duel_id}",
                ),
            ]
        ]
    )
    await message.answer(
        f"🟥 <b>ДУЭЛЬ НА КУБИКАХ #{duel_id}</b>\n\n"
        f"👤 Создатель: <b>{message.from_user.full_name}</b>\n"
        f"💰 Ставка: <b>{bet} {CURRENCY}</b>\n"
        f"🏆 Банк: <b>{round(bet * 2 * 0.95, 2)} {CURRENCY}</b> (комиссия 5%)\n\n"
        f"Кто примет вызов?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("duel_cancel:"))
async def duel_cancel_cb(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[1])
    duel = db.get_duel(duel_id)
    if not duel or duel["status"] != "open":
        await callback.answer("Дуэль уже завершена.", show_alert=True)
        return
    if callback.from_user.id != duel["creator_id"]:
        await callback.answer("Отменить может только создатель дуэли!", show_alert=True)
        return

    db.close_duel(duel_id, "cancelled")
    db.update_balance(duel["creator_id"], duel["bet"])
    await callback.message.edit_text(f"❌ Дуэль #{duel_id} отменена создателем.")
    await callback.answer()


@router.callback_query(F.data.startswith("duel_accept:"))
async def duel_accept_cb(callback: CallbackQuery):
    duel_id = int(callback.data.split(":")[1])
    duel = db.get_duel(duel_id)
    if not duel or duel["status"] != "open":
        await callback.answer("Дуэль уже неактивна!", show_alert=True)
        return

    opponent_id = callback.from_user.id
    if opponent_id == duel["creator_id"]:
        await callback.answer("Нельзя играть против самого себя!", show_alert=True)
        return

    db.register_user(opponent_id, callback.from_user.username, callback.from_user.full_name)
    opponent = db.get_user(opponent_id)
    bet = duel["bet"]

    if opponent["balance"] < bet:
        await callback.answer("❌ У вас недостаточно средств для принятия дуэли!", show_alert=True)
        return

    db.update_balance(opponent_id, -bet)
    db.close_duel(duel_id, "finished", opponent_id)
    await callback.answer("⚔️ Дуэль началась!")
    await callback.message.edit_text(f"🎲 <b>Дуэль #{duel_id} принята!</b> Бросаем кубики...", parse_mode=ParseMode.HTML)

    creator = db.get_user(duel["creator_id"])
    d1 = await callback.message.answer_dice(emoji=DiceEmoji.DICE)
    d2 = await callback.message.answer_dice(emoji=DiceEmoji.DICE)
    v1, v2 = d1.dice.value, d2.dice.value

    await asyncio.sleep(3.0)

    if v1 == v2:
        db.update_balance(duel["creator_id"], bet)
        db.update_balance(opponent_id, bet)
        await callback.message.answer(
            f"🤝 <b>Ничья в дуэли #{duel_id}!</b> ({v1} : {v2})\nСтавки возвращены игрокам.",
            parse_mode=ParseMode.HTML,
        )
        return

    prize = round(bet * 2 * 0.95, 2)
    winner_id = duel["creator_id"] if v1 > v2 else opponent_id
    winner_name = format_name(creator) if v1 > v2 else format_name(opponent)
    db.update_balance(winner_id, prize)

    await callback.message.answer(
        f"🏆 <b>Итоги дуэли #{duel_id}</b>\n\n"
        f"• {format_name(creator)}: <b>{v1}</b>\n"
        f"• {format_name(opponent)}: <b>{v2}</b>\n\n"
        f"Победитель: <b>{winner_name}</b> забирает <b>{prize} {CURRENCY}</b>!",
        parse_mode=ParseMode.HTML,
    )


# ==========================================
# БЫСТРЫЕ ТЕКСТОВЫЕ СТАВКИ В ЧАТЕ
# ==========================================
@router.message(F.text.regexp(r"^(куб|спин|слот)\s+(\d+(?:[.,]\d+)?)$"))
async def quick_chat_bet(message: Message):
    parts = message.text.lower().split()
    cmd_word, amount_str = parts[0], parts[1]
    bet = round(float(amount_str.replace(",", ".")), 2)

    db.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = db.get_user(message.from_user.id)
    if bet < 10 or bet > user["balance"]:
        await message.reply("❌ Недостаточно средств или ставка меньше 10.")
        return

    if cmd_word == "куб":
        await execute_single_game(message, message.from_user.id, "cube", "high", bet)
    else:
        await execute_single_game(message, message.from_user.id, "spins", "default", bet)


# ==========================================
# АДМИН-КОМАНДЫ
# ==========================================
@router.message(Command("addpromo"))
async def admin_add_promo(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer("Формат: <code>/addpromo BONUS500 500 50</code>", parse_mode=ParseMode.HTML)
        return
    try:
        code, reward, uses = command.args.split()
        db.create_promo(code, float(reward), int(uses))
        await message.answer(
            f"✅ Промокод <b>{code.upper()}</b> создан: <b>{reward} {CURRENCY}</b> ({uses} активаций).",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await message.answer("❌ Ошибка параметров. Пример: <code>/addpromo START 500 100</code>", parse_mode=ParseMode.HTML)


@router.message(Command("give"))
async def admin_give(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS or not command.args:
        return
    try:
        uid_str, amount_str = command.args.split()
        uid, amount = int(uid_str), float(amount_str)
        db.update_balance(uid, amount)
        await message.answer(f"✅ Баланс игрока <code>{uid}</code> изменён на <b>{amount} {CURRENCY}</b>.", parse_mode=ParseMode.HTML)
    except Exception:
        await message.answer("❌ Формат: <code>/give USER_ID 1000</code>", parse_mode=ParseMode.HTML)


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("Укажите токен бота в переменной окружения BOT_TOKEN или прямо в main.py!")
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Бот Red Cube / Spins запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
