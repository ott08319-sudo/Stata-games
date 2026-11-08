import asyncio
import os
from collections import Counter
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import DiceEmoji

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

stats_collector = {}

# Неофициальные расшифровки значений (по данным mtcute)
BASKETBALL_MEANINGS = {
    1: "простой промах (отскок влево)",
    2: "попадание в кольцо, затем промах",
    3: "мяч застрял между кольцом и основанием",
    4: "попадание в кольцо, затем гол",
    5: "чистый гол",
}

FOOTBALL_MEANINGS = {
    1: "выше перекладины",
    2: "попадание в правую штангу, затем промах",
    3: "прямой гол в центр",
    4: "правая штанга, гол, левая штанга",
    5: "гол в правый верхний угол",
}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот для статистики эмодзи-игр.\n\n"
        "Доступные команды:\n"
        "`/basket 10` — баскетбол 🏀\n"
        "`/football 10` — футбол ⚽\n\n"
        "Я кину эмодзи указанное число раз и покажу детальную статистику по каждому значению.\n"
        "⚠️ Расшифровка значений неофициальная (по данным mtcute) и может быть неточной."
    )

@dp.message(Command("basket", "football"))
async def handle_game_request(message: Message):
    command = message.text.split()[0][1:]
    emoji_map = {
        "basket": DiceEmoji.BASKETBALL,
        "football": DiceEmoji.FOOTBALL,
    }
    emoji = emoji_map.get(command)
    if not emoji:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(f"Укажи количество, например: `/{command} 10`")
        return
    try:
        count = int(args[1])
    except ValueError:
        await message.answer("Количество должно быть числом.")
        return

    if count > 100:
        await message.answer("Максимум 100 бросков за раз (лимиты Telegram).")
        return
    if count < 1:
        await message.answer("Нужно хотя бы 1 бросок.")
        return

    chat_id = message.chat.id
    await message.answer(f"Запускаю {count} бросков...")

    stats_collector[chat_id] = {
        "emoji": emoji,
        "game": command,
        "expected": count,
        "received": 0,
        "values": [],
        "message_ids": set(),
    }

    for _ in range(count):
        try:
            sent_msg = await bot.send_dice(chat_id=chat_id, emoji=emoji)
            stats_collector[chat_id]["message_ids"].add(sent_msg.message_id)
            await asyncio.sleep(0.3)
        except Exception as e:
            await message.answer(f"Ошибка при отправке: {e}")
            break

@dp.message(F.dice)
async def collect_dice_result(message: Message):
    chat_id = message.chat.id
    if chat_id not in stats_collector:
        return
    data = stats_collector[chat_id]
    if message.message_id not in data["message_ids"]:
        return

    data["values"].append(message.dice.value)
    data["received"] += 1

    if data["received"] >= data["expected"]:
        values = data["values"]
        counter = Counter(values)
        total = len(values)
        game = data["game"]
        emoji_str = data["emoji"].value

        if game == "basket":
            meanings = BASKETBALL_MEANINGS
            title = f"🏀 **Статистика баскетбола** (всего {total} бросков):"
        else:
            meanings = FOOTBALL_MEANINGS
            title = f"⚽ **Статистика футбола** (всего {total} бросков):"

        lines = [title, ""]
        for val in sorted(counter.keys()):
            cnt = counter[val]
            pct = cnt / total * 100
            meaning = meanings.get(val, "неизвестно")
            lines.append(f"  **{val}** — {meaning}: {cnt} раз ({pct:.1f}%)")

        # Итог по голам
        goals = sum(c for v, c in counter.items() if v >= 4)
        lines.append("")
        lines.append(f"🎯 Попаданий (значения 4-5): {goals} из {total} ({goals/total*100:.1f}%)")

        await bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        del stats_collector[chat_id]

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
