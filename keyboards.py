import os
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def reply_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="▣ Баланс"),
                KeyboardButton(text="🎮 Играть"),
                KeyboardButton(text="≡ Меню"),
            ]
        ],
        resize_keyboard=True,
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🤑 Заработать на чате", "nav:earn")],
            [button("🏅 VIP клуб", "nav:vip"), button("👤 Профиль", "nav:profile")],
            [button("🎁 Бонус спин", "nav:bonus"), button("👥 Реф. программа", "nav:ref")],
            [button("🧾 Чеки", "nav:history"), button("👑 Топ", "nav:top")],
            [button("🎰 Хочу своё казино", "nav:casino")],
            [button("🎮 Играть", "nav:play")],
        ]
    )


def games() -> InlineKeyboardMarkup:
    rows = [
        [
            button("🎲 (до x216)", "game:dice"),
            button("⚽ (до x25)", "game:soccer"),
            button("🏀 (до x25)", "game:basketball"),
        ],
        [
            button("🎯 (до x36)", "game:darts"),
            button("🎳 (до x36)", "game:bowling"),
            button("🎰 (до x400)", "game:slots"),
        ],
        [button("🐧 Авторские", "game:author")],
    ]

    news_url = os.getenv("NEWS_URL", "").strip()
    chat_url = os.getenv("CHAT_URL", "").strip()
    link_row = []
    if news_url.startswith(("http://", "https://")):
        link_row.append(InlineKeyboardButton(text="📣 News", url=news_url))
    if chat_url.startswith(("http://", "https://")):
        link_row.append(InlineKeyboardButton(text="💬 Chat", url=chat_url))
    if link_row:
        rows.append(link_row)

    rows.append([button("↩ Меню", "nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dice_modes() -> InlineKeyboardMarkup:
    # NOT OBSERVED: конкретные режимы внутри раздела кубиков.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🎲 Меньше 1–3 (x1.9)", "mode:dice_low")],
            [button("🎲 Больше 4–6 (x1.9)", "mode:dice_high")],
            [button("🎲 Точная шестёрка (x6)", "mode:dice_one")],
            [button("🎲 Три шестёрки (x216)", "mode:dice_three")],
            [button("↩ К играм", "nav:play")],
        ]
    )


def stake_presets(balance: int) -> InlineKeyboardMarkup:
    presets = [10, 50, 100, 500]
    row = [
        button(f"{amt} 💲", f"stake:{amt}")
        for amt in presets
        if balance >= amt
    ]
    keyboard = []
    if row:
        keyboard.append(row)
    if balance > 0:
        keyboard.append([button(f"Ва-банк ({balance} 💲)", f"stake:{balance}")])
    keyboard.append([button("↩ Отмена", "nav:play")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def after_round_keyboard(game: str, stake: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(f"🔁 Повторить ({stake} 💲)", f"repeat:{game}:{stake}"),
                button(f"✖️2 ({stake * 2} 💲)", f"repeat:{game}:{stake * 2}"),
            ],
            [button("🎮 Выбор игры", "nav:play"), button("≡ Меню", "nav:menu")],
        ]
    )


def vip_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Оформить за 500 💲", "buy:vip")],
            [button("↩ Меню", "nav:menu")],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[button("↩ Меню", "nav:menu")]]
    )
