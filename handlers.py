import asyncio
import html
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database import Database
from keyboards import (
    after_round_keyboard,
    back_menu,
    dice_modes,
    games,
    main_menu,
    reply_menu,
    stake_presets,
    vip_menu,
)

router = Router()

# NOT OBSERVED: точные коэффициенты и условия победы для каждого броска.
# На скриншоте видны только максимальные множители («до x216», «до x25»,
# «до x36», «до x400»). Логика ниже использует виртуальную валюту.
GAME_RULES = {
    "dice_low": ("🎲", "Кубик (1–3)", 1),
    "dice_high": ("🎲", "Кубик (4–6)", 1),
    "dice_one": ("🎲", "Кубик (6)", 1),
    "dice_three": ("🎲", "Три кубика (6-6-6)", 3),
    "soccer": ("⚽", "Футбол", 1),
    "basketball": ("🏀", "Баскетбол", 1),
    "darts": ("🎯", "Дартс", 1),
    "bowling": ("🎳", "Боулинг", 1),
    "slots": ("🎰", "Слоты", 1),
}


class BetState(StatesGroup):
    amount = State()


def sync_player(event: Message | CallbackQuery, db: Database):
    sender = event.from_user
    return db.user(sender.id, sender.full_name)


def payout_multiplier(game: str, values: list[int]) -> float:
    # NOT OBSERVED: формулы расчёта множителей.
    v = values[0]
    if game == "dice_low":
        return 1.9 if v <= 3 else 0.0
    if game == "dice_high":
        return 1.9 if v >= 4 else 0.0
    if game == "dice_one":
        return 6.0 if v == 6 else 0.0
    if game == "dice_three":
        return 216.0 if values == [6, 6, 6] else 0.0
    if game == "soccer":
        return 25.0 if v == 5 else (2.0 if v in (3, 4) else 0.0)
    if game == "basketball":
        return 25.0 if v == 5 else (2.0 if v == 4 else 0.0)
    if game == "darts":
        return 36.0 if v == 6 else (2.0 if v == 5 else 0.0)
    if game == "bowling":
        return 36.0 if v == 6 else (2.0 if v == 5 else 0.0)
    if game == "slots":
        if v == 64:  # 777
            return 400.0
        if v in (1, 22, 43):  # три одинаковых символа
            return 15.0
        if v <= 16:
            return 2.0
        return 0.0
    return 0.0


async def show_menu(message: Message) -> None:
    await message.answer("Выберите раздел:", reply_markup=main_menu())


async def show_games(message: Message, balance: int) -> None:
    await message.answer(
        "🎮 Выберите игру, на которую хотите сделать ставку!

"
        f"⊕ Ставка: 0 💲
"
        f"▣ Баланс: {balance} 💲",
        reply_markup=games(),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    referral = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
    db.user(message.from_user.id, message.from_user.full_name, referral)
    await message.answer("Добро пожаловать!", reply_markup=reply_menu())
    await show_menu(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=reply_menu())


@router.message(Command("play"))
@router.message(F.text.in_({"Играть", "🎮 Играть"}))
async def cmd_play(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user = sync_player(message, db)
    await show_games(message, user["balance"])


@router.message(Command("balance"))
@router.message(F.text.in_({"Баланс", "▣ Баланс"}))
async def cmd_balance(message: Message, db: Database) -> None:
    user = sync_player(message, db)
    await message.answer(f"▣ Баланс: {user['balance']} 💲")


@router.message(Command("menu"))
@router.message(F.text.in_({"Меню", "≡ Меню"}))
async def cmd_menu(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    sync_player(message, db)
    await show_menu(message)


@router.callback_query(F.data.startswith("nav:"))
async def cb_navigation(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await callback.answer()
    user = sync_player(callback, db)
    dest = callback.data.split(":", 1)[1]
    message = callback.message
    if message is None:
        return

    if dest == "menu":
        await state.clear()
        await show_menu(message)
    elif dest == "play":
        await state.clear()
        await show_games(message, user["balance"])
    elif dest == "profile":
        refs = db.referral_count(user["user_id"])
        await message.answer(
            f"👤 <b>Профиль</b>

"
            f"🆔 ID: <code>{user['user_id']}</code>
"
            f"▣ Баланс: <b>{user['balance']} 💲</b>
"
            f"🏅 VIP-статус: <b>{'Активен' if user['vip'] else 'Нет'}</b>
"
            f"👥 Приглашено друзей: <b>{refs}</b>",
            parse_mode="HTML",
            reply_markup=back_menu(),
        )
    elif dest == "vip":
        await message.answer(
            "🏅 <b>VIP клуб</b>

"
            "Статус отображается в профиле и таблице лидеров.
"
            "Стоимость оформления: <b>500 💲</b>.",
            parse_mode="HTML",
            reply_markup=vip_menu(),
        )
    elif dest == "bonus":
        # NOT OBSERVED: размер и периодичность бонус-спина.
        reward = secrets.choice((25, 50, 75, 100, 150))
        claimed, current, remaining = db.bonus(callback.from_user.id, reward)
        if claimed:
            await message.answer(
                f"🎁 Бонус спин: <b>+{reward} 💲</b>
"
                f"▣ Баланс: <b>{current} 💲</b>",
                parse_mode="HTML",
                reply_markup=back_menu(),
            )
        else:
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await message.answer(
                f"⏳ Бонус уже получен. Следующий через {hours} ч {minutes} мин.",
                reply_markup=back_menu(),
            )
    elif dest == "ref":
        me = await callback.bot.get_me()
        refs = db.referral_count(callback.from_user.id)
        await message.answer(
            "👥 <b>Реф. программа</b>

"
            "Награда за каждого нового игрока: <b>50 💲</b>
"
            f"Приглашено: <b>{refs}</b>

"
            f"Ваша ссылка:
<code>https://t.me/{me.username}?start={callback.from_user.id}</code>",
            parse_mode="HTML",
            reply_markup=back_menu(),
        )
    elif dest == "history":
        rows = db.history(callback.from_user.id)
        if not rows:
            await message.answer("🧾 Чеков пока нет.", reply_markup=back_menu())
            return
        lines = ["🧾 <b>Последние 10 ставок:</b>
"]
        for r in rows:
            lines.append(
                f"• #{r['id']} | {html.escape(r['game'])} — "
                f"ставка {r['stake']} 💲, выплата {r['payout']} 💲 ({r['status']})"
            )
        await message.answer(
            "
".join(lines), parse_mode="HTML", reply_markup=back_menu()
        )
    elif dest == "top":
        rows = db.top()
        if not rows:
            await message.answer("👑 Топ пока пуст.", reply_markup=back_menu())
            return
        lines = ["👑 <b>Топ игроков:</b>
"]
        for idx, r in enumerate(rows, 1):
            badge = " 🏅" if r["vip"] else ""
            lines.append(
                f"{idx}. {html.escape(r['name'])}{badge} — <b>{r['balance']} 💲</b>"
            )
        await message.answer(
            "
".join(lines), parse_mode="HTML", reply_markup=back_menu()
        )
    elif dest in ("earn", "casino"):
        await message.answer(
            "ℹ️ Этот раздел не подключён в текущей версии: "
            "его сценарий не наблюдался на скриншоте.",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data == "buy:vip")
async def cb_buy_vip(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    sync_player(callback, db)
    if db.buy_vip(callback.from_user.id):
        await callback.message.answer(
            "✅ VIP-статус успешно оформлен!", reply_markup=back_menu()
        )
    else:
        await callback.message.answer(
            "❌ Недостаточно средств или VIP уже оформлен.",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data.startswith("game:"))
async def cb_choose_game(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await callback.answer()
    user = sync_player(callback, db)
    game = callback.data.split(":", 1)[1]

    if game == "author":
        await callback.message.answer(
            "🐧 Раздел «Авторские» не подключён: правила не наблюдались на скриншоте.",
            reply_markup=back_menu(),
        )
        return

    if game == "dice":
        await callback.message.answer(
            "🎲 Выберите исход кубика:", reply_markup=dice_modes()
        )
        return

    if game in GAME_RULES:
        await state.set_state(BetState.amount)
        await state.update_data(game=game)
        _, title, _ = GAME_RULES[game]
        await callback.message.answer(
            f"Игра: <b>{title}</b>
"
            f"▣ Баланс: <b>{user['balance']} 💲</b>

"
            "Введите сумму ставки (от 1 до 100000 💲) или выберите кнопку:",
            parse_mode="HTML",
            reply_markup=stake_presets(user["balance"]),
        )


@router.callback_query(F.data.startswith("mode:"))
async def cb_choose_mode(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await callback.answer()
    user = sync_player(callback, db)
    game = callback.data.split(":", 1)[1]
    if game not in GAME_RULES:
        return

    await state.set_state(BetState.amount)
    await state.update_data(game=game)
    _, title, _ = GAME_RULES[game]
    await callback.message.answer(
        f"Режим: <b>{title}</b>
"
        f"▣ Баланс: <b>{user['balance']} 💲</b>

"
        "Введите сумму ставки (от 1 до 100000 💲) или выберите кнопку:",
        parse_mode="HTML",
        reply_markup=stake_presets(user["balance"]),
    )


@router.callback_query(BetState.amount, F.data.startswith("stake:"))
async def cb_preset_stake(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await callback.answer()
    raw = callback.data.split(":", 1)[1]
    if not raw.isdigit():
        return
    stake = int(raw)
    data = await state.get_data()
    game = data.get("game")
    await state.clear()
    if game not in GAME_RULES or callback.message is None:
        return
    await execute_round(
        callback.message, db, callback.from_user.id, callback.from_user.full_name, game, stake
    )


@router.callback_query(F.data.startswith("repeat:"))
async def cb_repeat_round(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    _, game, raw_stake = parts
    if game not in GAME_RULES or callback.message is None:
        return
    await execute_round(
        callback.message,
        db,
        callback.from_user.id,
        callback.from_user.full_name,
        game,
        int(raw_stake),
    )


@router.message(BetState.amount)
async def msg_accept_stake(
    message: Message, db: Database, state: FSMContext
) -> None:
    text = (message.text or "").strip()
    if not text.isascii() or not text.isdecimal() or len(text) > 6:
        await message.answer("Введите целое число от 1 до 100000.")
        return
    stake = int(text)
    if not 1 <= stake <= 100000:
        await message.answer("Введите целое число от 1 до 100000.")
        return

    game = (await state.get_data()).get("game")
    await state.clear()
    if game not in GAME_RULES:
        await message.answer("Выберите игру заново: /play")
        return

    await execute_round(
        message, db, message.from_user.id, message.from_user.full_name, game, stake
    )


async def execute_round(
    message: Message,
    db: Database,
    user_id: int,
    full_name: str,
    game: str,
    stake: int,
) -> None:
    if not 1 <= stake <= 100000:
        await message.answer("Ставка должна быть от 1 до 100000 💲.")
        return

    db.user(user_id, full_name)
    round_id = db.reserve(user_id, game, stake)
    if round_id is None:
        await message.answer(
            "❌ Недостаточно средств на балансе. Используйте «Бонус спин» в меню.",
            reply_markup=back_menu(),
        )
        return

    emoji, title, throws = GAME_RULES[game]
    values = []
    try:
        for _ in range(throws):
            dice_msg = await message.answer_dice(emoji=emoji)
            values.append(dice_msg.dice.value)
    except Exception:
        db.refund(round_id)
        raise

    multiplier = payout_multiplier(game, values)
    payout = int(stake * multiplier)
    outcome_str = "-".join(map(str, values))
    current_balance = db.settle(round_id, payout, outcome_str)

    await asyncio.sleep(3.8)

    status_line = (
        f"✅ <b>Выигрыш: +{payout} 💲</b> (x{multiplier:g})"
        if payout > 0
        else "❌ <b>Без выигрыша</b>"
    )
    await message.answer(
        f"🎮 <b>{title}</b>
"
        f"Выпало: <b>{', '.join(map(str, values))}</b>
"
        f"{status_line}
"
        f"▣ Баланс: <b>{current_balance} 💲</b>",
        parse_mode="HTML",
        reply_markup=after_round_keyboard(game, stake),
    )
