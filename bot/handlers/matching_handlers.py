from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import logging
from bot.config import dp, API_URL
from bot.storage.redis import queue_manager
from bot.storage.minio import download_image_from_minio
import requests
from urllib.parse import urlparse
from bot.logger import logger
import asyncio

logger = logging.getLogger(__name__)

@dp.message(Command("next"))
async def next_profile(message: types.Message):
    user_id = message.from_user.id

    try:
        # Получаем список пользователей
        response = requests.get(f"{API_URL}/api/users/?exclude_user={user_id}")
        if response.status_code != 200:
            logger.error(f"API request failed with status {response.status_code}")
            await message.answer("😔 Произошла ошибка при загрузке анкет. Попробуйте позже!")
            return

        profile = await queue_manager.get_next_profile(user_id)

        if not profile:
            await message.answer("😔 Пока нет новых анкет. Попробуйте позже!")
            return
        
        # Проверяем наличие изображений
        if not profile.get('images'):
            await message.answer("😔 У этого пользователя нет фотографий.")
            return
            
        # Загружаем фото из MinIO
        image_url = profile['images'][0]['image']
        # Извлекаем путь к файлу из URL
        parsed_url = urlparse(image_url)
        # Убираем хост и ведущий слеш из пути
        image_path = parsed_url.path.lstrip('/').replace('minio:9000/media/', '')
        
        logger.info(f"Trying to download image from path: {image_path}")
        photo_data = await download_image_from_minio(image_path)
        
        if not photo_data:
            await message.answer("😔 Не удалось загрузить фотографию пользователя.")
            return
        
        # Создаем клавиатуру с кнопками
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="❤️", callback_data=f"like_{profile['telegram_id']}"),
                    InlineKeyboardButton(text="➡️", callback_data=f"skip_{profile['telegram_id']}")
                ]
            ]
        )
        
        # Отправляем анкету
        await message.answer_photo(
            photo_data,
            caption=f"👤 {profile['name']}, {profile['age']}\n"
                   f"🏙 {profile['city']}\n\n"
                   f"📝 {profile['bio']}",
            reply_markup=keyboard
        )
    except requests.RequestException as e:
        logger.error(f"Network error: {str(e)}")
        await message.answer("😔 Произошла ошибка при подключении к серверу. Попробуйте позже!")
    except Exception as e:
        logger.error(f"Error showing profile: {str(e)}")
        await message.answer("😔 Произошла ошибка при отображении анкеты. Попробуйте позже!")

@dp.message(Command("matches"))
async def show_matches(message: types.Message):
    user_id = message.from_user.id
    
    try:
        response = requests.get(f"{API_URL}/api/matches/{user_id}/")
        response.raise_for_status()
        matches = response.json()
        
        if not matches:
            await message.answer("😔 У вас пока нет мэтчей.")
            return
        
        for match in matches:
            # Загружаем фото из MinIO
            photo_data = await download_image_from_minio(match['images'][0]['image'])
            
            await message.answer_photo(
                photo_data,
                caption=f"👤 {match['name']}, {match['age']}\n"
                       f"🏙 {match['city']}\n\n"
                       f"📝 {match['bio']}\n\n"
                       f"💬 Напишите @{match['username']} в Telegram"
            )
            
    except Exception as e:
        logger.error(f"Error showing matches: {str(e)}")
        await message.answer("🚫 Ошибка при получении мэтчей! Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith(('like_', 'skip_')))
async def process_profile_action(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    action, profile_id = callback_query.data.split('_')
    
    try:
        # Отправляем лайк или пропуск
        response = requests.post(
            f"{API_URL}/api/likes/",
            json={
                'from_user': user_id,
                'to_user': profile_id,
                'is_skip': action == 'skip'
            }
        )
        response.raise_for_status()
        
        if action == 'like':
            # Проверяем, есть ли мэтч
            response = requests.get(
                f"{API_URL}/api/matches/check/",
                params={
                    'user1': user_id,
                    'user2': profile_id
                }
            )
            response.raise_for_status()
            is_match = response.json()['is_match']
            
            if is_match:
                # Получаем данные о мэтче
                response = requests.get(f"{API_URL}/api/users/{profile_id}/")
                response.raise_for_status()
                match = response.json()
                
                # Отправляем сообщение о мэтче
                await callback_query.message.answer(
                    f"🎉 У вас мэтч с {match['name']}!\n"
                    f"💬 Напишите @{match['username']} в Telegram"
                )
            else:
                await callback_query.message.answer("✅ Лайк отправлен!")
        
        else:  # skip
            await callback_query.message.answer("➡️ Следующая анкета...")
        
        # Удаляем сообщение с анкетой
        await callback_query.message.delete()
        
    except requests.exceptions.RequestException as e:
        logger.error(f"API request error: {str(e)}")
        await callback_query.message.answer("😔 Произошла ошибка при отправке действия. Попробуйте позже!")
    except Exception as e:
        logger.error(f"Error processing profile action: {str(e)}")
        await callback_query.message.answer("😔 Произошла ошибка при обработке действия. Попробуйте позже!") 
