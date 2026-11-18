import asyncio
import html
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database import Database
from keyboards import dice_modes, games, main_menu, reply_menu, vip_menu

router = Router()

# NOT OBSERVED: правила выигрыша, значения и множители, кроме надписей
# «до x...» на скриншоте. Все выплаты работают только с игровой валютой.
GAME_RULES = {
    "dice_one": ("🎲", "Кубик", 1),
    "dice_three": ("🎲", "Три кубика", 3),
    "soccer": ("⚽", "Футбол", 1),
    "basketball": ("🏀", "Баскетбол", 1),
    "darts": ("🎯", "Дартс", 1),
    "bowling": ("🎳", "Боулинг", 1),
    "slots": ("🎰", "Слоты", 1),
}


class BetState(StatesGroup):
    amount = State()


def player(event: Message | CallbackQuery, db: Database):
    sender = event.from_user
    return db.user(sender.id, sender.full_name)


def payout_multiplier(game: str, values: list[int]) -> int:
    # NOT OBSERVED: условия выигрыша для рабочего прототипа.
    value = values[0]
    if game == "dice_one":
        return 6 if value == 6 else 0
    if game == "dice_three":
        return 216 if values == [6, 6, 6] else 0
    if game in ("soccer", "basketball"):
        return 25 if value == 5 else (2 if value == 4 else 0)
    if game in ("darts", "bowling"):
        return 36 if value == 6 else (2 if value == 5 else 0)
    if game == "slots":
        return 400 if value == 64 else (2 if value <= 8 else 0)
    return 0


async def show_menu(message: Message) -> None:
    await message.answer(
        "Выберите раздел:",
        reply_markup=main_menu(),
    )


async def show_games(message: Message, db: Database, user_id: int) -> None:
    full_name = message.from_user.full_name if message.from_user else "Игрок"
    user = db.user(user_id, full_name)
    text = "\n".join([
        "🎮 Выберите игру, на которую хотите сделать ставку!",
        "",
        "⊕ Ставка: 0 💲",
        f"▣ Баланс: {user['balance']} 💲",
    ])
    await message.answer(text, reply_markup=games())


@router.message(CommandStart())
async def start(message: Message, db: Database) -> None:
    parts = (message.text or "").split(maxsplit=1)
    referral = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
    db.user(message.from_user.id, message.from_user.full_name, referral)
    await message.answer("Главное меню", reply_markup=reply_menu())
    await show_menu(message)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ставка отменена.", reply_markup=reply_menu())


@router.message(Command("play"))
@router.message(F.text.casefold().in_({"играть", "🎮 играть"}))
async def play(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    player(message, db)
    await show_games(message, db, message.from_user.id)


@router.message(Command("balance"))
@router.message(F.text.casefold().in_({"баланс", "▣ баланс"}))
async def balance(message: Message, db: Database) -> None:
    user = player(message, db)
    await message.answer(f"▣ Баланс: {user['balance']} 💲")


@router.message(Command("menu"))
@router.message(F.text.casefold().in_({"меню", "☰ меню"}))
async def menu(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    player(message, db)
    await show_menu(message)


@router.callback_query(F.data.startswith("nav:"))
async def navigation(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await callback.answer()
    user = player(callback, db)
    destination = callback.data.split(":", 1)[1]
    message = callback.message
    if message is None:
        return

    if destination == "menu":
        await state.clear()
        await show_menu(message)
    elif destination == "play":
        await state.clear()
        text = "\n".join([
            "🎮 Выберите игру, на которую хотите сделать ставку!",
            "",
            "⊕ Ставка: 0 💲",
            f"▣ Баланс: {user['balance']} 💲",
        ])
        await message.answer(text, reply_markup=games())
    elif destination == "profile":
        text = "\n".join([
            "👤 Профиль",
            f"ID: {user['user_id']}",
            f"Баланс: {user['balance']} 💲",
            f"VIP: {'да' if user['vip'] else 'нет'}",
        ])
        await message.answer(text)
    elif destination == "vip":
        text = "\n".join([
            "🎖 VIP клуб",
            "Пробная версия: VIP-статус отображается в профиле. Стоимость 500 💲.",
        ])
        await message.answer(text, reply_markup=vip_menu())
    elif destination == "bonus":
        # NOT OBSERVED: периодичность и размер бонуса.
        reward = secrets.choice((25, 50, 75, 100, 150))
        claimed, current = db.bonus(callback.from_user.id, reward)
        if claimed:
            text = "\n".join([
                f"🎁 Бонус спин: +{reward} 💲",
                f"Баланс: {current} 💲",
            ])
            await message.answer(text)
        else:
            await message.answer("Бонус уже получен. Попробуйте через 24 часа.")
    elif destination == "ref":
        bot_info = await callback.bot.get_me()
        text = "\n".join([
            "👥 Реф. программа",
            "Награда за нового игрока: 50 💲.",
            f"https://t.me/{bot_info.username}?start={callback.from_user.id}",
        ])
        await message.answer(text)
    elif destination == "history":
        rows = db.history(callback.from_user.id)
        lines = ["🎟 Чеки — последние 10 ставок:"]
        for row in rows:
            lines.append(
                f"{html.escape(row['game'])}: ставка {row['stake']} 💲, "
                f"выплата {row['payout']} 💲 ({row['status']})"
            )
        await message.answer("\n".join(lines) if rows else "Чеков пока нет.")
    elif destination == "top":
        rows = db.top()
        lines = ["👑 Топ игроков"]
        for index, row in enumerate(rows, 1):
            lines.append(
                f"{index}. {html.escape(row['name'])}: {row['balance']} 💲"
            )
        await message.answer("\n".join(lines))
    elif destination in ("earn", "casino"):
        await message.answer(
            "Этот раздел пока не подключён: его работа "
            "не видна на предоставленном скриншоте."
        )
    else:
        await message.answer("Раздел не найден.")


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    player(callback, db)
    if callback.data != "buy:vip":
        return
    if db.buy_vip(callback.from_user.id):
        await callback.message.answer("VIP-статус оформлен.")
    else:
        await callback.message.answer(
            "Недостаточно средств или VIP уже оформлен."
        )


@router.callback_query(F.data.startswith("game:"))
async def choose_game(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    game = callback.data.split(":", 1)[1]
    if game == "author":
        await callback.message.answer(
            "Авторские игры пока не подключены: правила не показаны."
        )
    elif game == "dice":
        await callback.message.answer(
            "Выберите исход кубика:", reply_markup=dice_modes()
        )
    elif game in GAME_RULES:
        await state.set_state(BetState.amount)
        await state.update_data(game=game)
        text = "\n".join([
            "Введите сумму ставки целым числом от 1 до 100000 💲.",
            "Отмена: /cancel",
        ])
        await callback.message.answer(text)


@router.callback_query(F.data.startswith("mode:"))
async def choose_dice_mode(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer()
    game = callback.data.split(":", 1)[1]
    if game not in ("dice_one", "dice_three"):
        return
    await state.set_state(BetState.amount)
    await state.update_data(game=game)
    text = "\n".join([
        "Введите сумму ставки целым числом от 1 до 100000 💲.",
        "Отмена: /cancel",
    ])
    await callback.message.answer(text)


@router.message(BetState.amount)
async def accept_stake(
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

    player(message, db)
    round_id = db.reserve(message.from_user.id, game, stake)
    if round_id is None:
        await message.answer("Недостаточно средств. Проверьте баланс.")
        return

    emoji, name, throws = GAME_RULES[game]
    values = []
    try:
        for _ in range(throws):
            dice_message = await message.answer_dice(emoji=emoji)
            values.append(dice_message.dice.value)
    except Exception:
        db.refund(round_id)
        raise

    multiplier = payout_multiplier(game, values)
    payout = stake * multiplier
    current_balance = db.settle(round_id, payout)
    await asyncio.sleep(4)

    result_text = "\n".join([
        f"{name}: {', '.join(map(str, values))}",
        f"{'Выигрыш' if payout else 'Без выигрыша'}: {payout} 💲",
        f"Баланс: {current_balance} 💲",
    ])
    await message.answer(result_text, reply_markup=games())
