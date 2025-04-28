from aiogram.filters.command import Command
from aiogram.types import Message
from bot.config import dp


@dp.message(Command("referral_link"))
async def get_referral_link(message: Message):
    await message.answer(f"Ваша реферальная ссылка - t.me/verigooddatingbot?start={message.from_user.id}")
