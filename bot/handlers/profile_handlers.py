from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.filters.command import Command
from bot.config import dp, bot, API_URL
from bot.handlers.states import ProfileStates
import requests
from bot.logger import logger
import os

@dp.message(Command("edit"))
async def edit_profile(message: types.Message, state: FSMContext):
    await message.answer(
        "📛 Как тебя зовут? (Используй реальное имя для доверия)"
    )
    await state.set_state(ProfileStates.NAME)

@dp.message(ProfileStates.NAME)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("Имя должно быть от 2 до 100 символов. Попробуй еще раз!")
        return
    
    await state.update_data(name=name)
    await message.answer(
        "✅ Отлично! Теперь укажи свой пол:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Мужской"), types.KeyboardButton(text="Женский")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(ProfileStates.GENDER)

@dp.message(ProfileStates.GENDER)
async def process_gender(message: types.Message, state: FSMContext):
    gender = 'M' if message.text == 'Мужской' else 'F'
    await state.update_data(gender=gender)
    
    await message.answer(
        "✅ Отлично! Теперь укажи, кого ты ищешь:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="Мужчин"), types.KeyboardButton(text="Женщин")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(ProfileStates.SEEKING_GENDER)

@dp.message(ProfileStates.SEEKING_GENDER)
async def process_seeking_gender(message: types.Message, state: FSMContext):
    seeking_gender = 'M' if message.text == 'Мужчин' else 'F'
    await state.update_data(seeking_gender=seeking_gender)
    
    await message.answer(
        "📅 Сколько тебе лет?",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ProfileStates.AGE)

@dp.message(ProfileStates.AGE)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите число!")
        return
    
    age = int(message.text)
    if age < 18 or age > 100:
        await message.answer("Возраст должен быть от 18 до 100 лет!")
        return
    
    await state.update_data(age=age)
    await message.answer("🏙 В каком городе ты живешь?")
    await state.set_state(ProfileStates.CITY)

@dp.message(ProfileStates.CITY)
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await message.answer("📝 Напиши коротко о себе (максимум 500 символов):")
    await state.set_state(ProfileStates.BIO)

@dp.message(ProfileStates.BIO)
async def process_bio(message: types.Message, state: FSMContext):
    if len(message.text) > 500:
        await message.answer("Слишком длинное описание! Максимум 500 символов.")
        return
    
    await state.update_data(bio=message.text)
    await message.answer("📸 Теперь отправь свои фотографии (можно несколько):")
    await state.set_state(ProfileStates.PHOTOS)

@dp.message(ProfileStates.PHOTOS)
async def process_photos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    # Если это не фото, а текстовая команда
    if not message.photo:
        if message.text == '/done':
            if not data.get('photos_uploaded'):
                await message.answer("📸 Для завершения профиля нужно добавить хотя бы одно фото!")
                return
            
            await message.answer(
                "🎉 Профиль успешно создан!\n\n"
                "Теперь ты можешь:\n"
                "- /next - Смотреть анкеты\n"
                "- /edit - Редактировать профиль\n"
                "- /matches - Посмотреть мэтчи"
            )
            await state.clear()
            return
        else:
            await message.answer("📸 Пожалуйста, отправьте фото или напишите /done для завершения")
            return
    
    # Если это первое фото - создаем пользователя
    if 'user_created' not in data:
        user_data = {
            'telegram_id': message.from_user.id,
            'name': data['name'],
            'gender': data['gender'],
            'age': data['age'],
            'seeking_gender': data['seeking_gender'],
            'city': data['city'],
            'bio': data['bio']
        }
        
        try:
            # Сначала проверяем, существует ли пользователь
            check_response = requests.get(f"{API_URL}/api/users/{message.from_user.id}/")
            if check_response.status_code == 200:
                # Пользователь уже существует, обновляем данные
                response = requests.patch(
                    f"{API_URL}/api/users/{message.from_user.id}/",
                    json=user_data
                )
            else:
                # Создаем нового пользователя
                response = requests.post(f"{API_URL}/api/users/", json=user_data)
            
            response.raise_for_status()
            
        except Exception as e:
            logger.error(f"User creation/update error: {str(e)}")
            await message.answer("🚫 Ошибка создания/обновления профиля! Попробуйте позже.")
            await state.clear()
            return
        
        await state.update_data(user_created=True)
    
    # Загрузка фото
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file_info.file_path}"
    
    try:
        # Загружаем фото с указанием telegram_id
        with requests.get(file_url, stream=True) as image:
            files = {
                'image': (file_info.file_path, image.raw, 'image/jpeg'),
                'telegram_id': (None, str(message.from_user.id))
            }
            response = requests.post(
                f"{API_URL}/api/images/",
                files=files
            )
            response.raise_for_status()
            
            # Обновляем счетчик загруженных фото
            photos_uploaded = data.get('photos_uploaded', 0) + 1
            await state.update_data(photos_uploaded=photos_uploaded)
            
            await message.answer(
                f"✅ Фото успешно добавлено! ({photos_uploaded}/5)\n"
                "Отправьте еще фото или напишите /done для завершения"
            )
    except Exception as e:
        logger.error(f"Photo upload error: {str(e)}")
        await message.answer("🚫 Ошибка загрузки фото! Попробуйте снова.") 
