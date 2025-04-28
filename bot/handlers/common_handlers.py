from aiogram import types
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from bot.config import dp, API_URL
from bot.handlers.states import ProfileStates
import requests
from bot.logger import logger

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    response = requests.get(f"{API_URL}/api/users/{user_id}/")
    
    if response.status_code == 200:
        await message.answer(
            "🎉 Добро пожаловать назад! Ваш профиль уже создан.\n\n"
            "Доступные команды:\n"
            "- /next - Смотреть анкеты\n"
            "- /referral_link - Ваша реферальная ссылка"
        )
        return
    
    args = message.text.split()
    referrer_id = args[1] if len(args) > 1 else None
    if referrer_id:
        requests.post(f"{API_URL}/api/referrals/", data={"referrer": referrer_id, "referred_user": message.from_user.id})
    await message.answer(
        "👋 Привет! Давай создадим твой профиль для знакомств.\n"
        "📛 Как тебя зовут? (Используй реальное имя для доверия)"
    )
    await state.set_state(ProfileStates.NAME)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "🤖 Доступные команды:\n\n"
        "- /start - Начать работу с ботом\n"
        "- /next - Смотреть анкеты\n"
        "- /referral_link - Ваша реферальная ссылка\n"
        "- /help - Показать это сообщение"
    ) 
