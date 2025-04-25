import os
import logging
import asyncio
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
import requests
from typing import Optional
import json
from datetime import datetime
import redis.asyncio as redis
import pika
import threading
import boto3
from botocore.exceptions import ClientError
import tempfile
from aiogram.types import BufferedInputFile

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher(storage=MemoryStorage())
API_URL = os.getenv('API_URL', 'http://web:8000')

# Настройки Redis и RabbitMQ
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'rabbitmq')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', '5672'))
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'rabbit')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD', 'rabbit')

# Настройки MinIO
MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'minio:9000')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
MINIO_BUCKET = os.getenv('MINIO_BUCKET', 'media')

# Инициализация клиента MinIO
s3_client = boto3.client(
    's3',
    endpoint_url=f'http://{MINIO_ENDPOINT}',
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    config=boto3.session.Config(signature_version='s3v4'),
    region_name='us-east-1'
)

async def download_image_from_minio(image_path: str) -> Optional[BufferedInputFile]:
    """Скачивает изображение из MinIO и возвращает BufferedInputFile"""
    try:
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
            # Скачиваем файл из MinIO
            s3_client.download_fileobj(MINIO_BUCKET, image_path, tmp_file)
            # Читаем содержимое файла
            with open(tmp_file.name, 'rb') as f:
                image_bytes = f.read()
                return BufferedInputFile(image_bytes, filename=image_path)
    except ClientError as e:
        logger.error(f"Error downloading image from MinIO: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading image: {str(e)}")
        return None
    finally:
        # Удаляем временный файл
        try:
            os.unlink(tmp_file.name)
        except Exception as e:
            logger.error(f"Error deleting temp file: {str(e)}")

class ProfileQueueManager:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.redis = None
        self.connected = False

    async def connect(self):
        if not self.connected:
            try:
                self.redis = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                await self.redis.ping()
                self.connected = True
                logger.info("Successfully connected to Redis")
            except Exception as e:
                logger.error(f"Error connecting to Redis: {str(e)}")
                self.connected = False
                raise

    async def disconnect(self):
        if self.connected and self.redis:
            try:
                await self.redis.close()
                self.connected = False
                logger.info("Successfully disconnected from Redis")
            except Exception as e:
                logger.error(f"Error disconnecting from Redis: {str(e)}")

    def get_queue_key(self, user_id: int) -> str:
        return f"profile_queue:{user_id}"

    async def add_profiles_to_queue(self, user_id: int, profiles: list):
        if not self.connected:
            await self.connect()
            
        queue_key = self.get_queue_key(user_id)
        try:
            async with self.redis.pipeline() as pipe:
                for profile in profiles:
                    await pipe.rpush(queue_key, json.dumps(profile))
                await pipe.execute()
            logger.info(f"Added {len(profiles)} profiles to queue for user {user_id}")
        except Exception as e:
            logger.error(f"Error adding profiles to queue: {str(e)}")
            self.connected = False
            await self.connect()
            # Повторная попытка
            async with self.redis.pipeline() as pipe:
                for profile in profiles:
                    await pipe.rpush(queue_key, json.dumps(profile))
                await pipe.execute()

    async def get_next_profile(self, user_id: int) -> Optional[dict]:
        if not self.connected:
            await self.connect()
            
        queue_key = self.get_queue_key(user_id)
        try:
            profile_data = await self.redis.lpop(queue_key)
            if profile_data:
                return json.loads(profile_data)
            return None
        except Exception as e:
            logger.error(f"Error getting next profile: {str(e)}")
            self.connected = False
            await self.connect()
            # Повторная попытка
            profile_data = await self.redis.lpop(queue_key)
            if profile_data:
                return json.loads(profile_data)
            return None

    async def get_queue_length(self, user_id: int) -> int:
        if not self.connected:
            await self.connect()
            
        queue_key = self.get_queue_key(user_id)
        try:
            return await self.redis.llen(queue_key)
        except Exception as e:
            logger.error(f"Error getting queue length: {str(e)}")
            return 0

class ProfileStates(StatesGroup):
    NAME = State()
    GENDER = State()
    SEEKING_GENDER = State()
    AGE = State()
    CITY = State()
    BIO = State()
    PHOTOS = State()

# Инициализация менеджера очереди
queue_manager = ProfileQueueManager(REDIS_URL)

class EventManager:
    def __init__(self, rabbitmq_host: str, rabbitmq_port: int, rabbitmq_user: str, rabbitmq_password: str):
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_user = rabbitmq_user
        self.rabbitmq_password = rabbitmq_password
        self.connection = None
        self.channel = None
        self.match_queue = None
        self.connected = False
        self.lock = threading.Lock()

    def start(self):
        if not self.connected:
            try:
                parameters = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    port=self.rabbitmq_port,
                    credentials=pika.PlainCredentials(
                        self.rabbitmq_user,
                        self.rabbitmq_password
                    )
                )
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                
                # Объявляем обменники
                self.channel.exchange_declare(
                    exchange="user_events",
                    exchange_type="topic",
                    durable=True
                )
                self.channel.exchange_declare(
                    exchange="interaction_events",
                    exchange_type="topic",
                    durable=True
                )
                self.channel.exchange_declare(
                    exchange="matches",
                    exchange_type="fanout",
                    durable=True
                )
                
                # Создаем очередь для мэтчей
                self.match_queue = self.channel.queue_declare(
                    queue="match_notifications",
                    durable=True
                )
                self.channel.queue_bind(
                    exchange="matches",
                    queue="match_notifications"
                )
                
                # Запускаем обработчик мэтчей в отдельном потоке
                threading.Thread(target=self.process_matches, daemon=True).start()
                
                self.connected = True
                logger.info("Successfully connected to RabbitMQ")
                
            except Exception as e:
                logger.error(f"Error connecting to RabbitMQ: {str(e)}")
                self.connected = False
                raise

    def stop(self):
        if self.connected:
            try:
                if self.channel:
                    self.channel.close()
                if self.connection:
                    self.connection.close()
                self.connected = False
                logger.info("Successfully disconnected from RabbitMQ")
            except Exception as e:
                logger.error(f"Error disconnecting from RabbitMQ: {str(e)}")

    def send_event(self, exchange: str, event_type: str, data: dict):
        with self.lock:
            if not self.connected:
                self.start()
            
            event = {
                'type': event_type,
                'data': data,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            try:
                self.channel.basic_publish(
                    exchange=exchange,
                    routing_key=event_type,
                    body=json.dumps(event),
                    properties=pika.BasicProperties(
                        delivery_mode=2  # persistent
                    )
                )
            except Exception as e:
                logger.error(f"Error sending event to RabbitMQ: {str(e)}")
                self.connected = False
                self.start()
                # Повторная отправка
                self.channel.basic_publish(
                    exchange=exchange,
                    routing_key=event_type,
                    body=json.dumps(event),
                    properties=pika.BasicProperties(
                        delivery_mode=2  # persistent
                    )
                )

    async def process_matches(self):
        async def callback(ch, method, properties, body):
            try:
                match_data = json.loads(body)
                user1_id = match_data['data']['user1_id']
                user2_id = match_data['data']['user2_id']
                
                # Получаем информацию о пользователях через Telegram Bot API
                user1_info = await bot.get_chat_member(user1_id, user1_id)
                user2_info = await bot.get_chat_member(user2_id, user2_id)
                
                # Отправляем сообщение первому пользователю
                await bot.send_message(
                    user1_id,
                    f"🎉 У вас взаимная симпатия с {user2_info.user.first_name}! Напишите привет!"
                )
                # Отправляем сообщение второму пользователю
                await bot.send_message(
                    user2_id,
                    f"🎉 У вас взаимная симпатия с {user1_info.user.first_name}! Напишите привет!"
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"Error processing match: {str(e)}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        while True:
            try:
                if not self.connected:
                    self.start()
                
                # Создаем новое соединение для обработки сообщений
                parameters = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    port=self.rabbitmq_port,
                    credentials=pika.PlainCredentials(
                        self.rabbitmq_user,
                        self.rabbitmq_password
                    )
                )
                connection = pika.BlockingConnection(parameters)
                channel = connection.channel()
                
                # Объявляем очередь
                channel.queue_declare(queue='match_notifications', durable=True)
                channel.queue_bind(
                    exchange='matches',
                    queue='match_notifications'
                )
                
                # Устанавливаем QoS
                channel.basic_qos(prefetch_count=1)
                
                # Начинаем потребление
                channel.basic_consume(
                    queue='match_notifications',
                    on_message_callback=callback,
                    consumer_tag='match_consumer'
                )
                
                try:
                    channel.start_consuming()
                except pika.exceptions.ConnectionClosedByBroker:
                    logger.warning("Connection closed by broker, reconnecting...")
                    continue
                except pika.exceptions.AMQPChannelError:
                    logger.warning("Channel error, reconnecting...")
                    continue
                except pika.exceptions.AMQPConnectionError:
                    logger.warning("Connection error, reconnecting...")
                    continue
                
            except Exception as e:
                logger.error(f"Error in match processing: {str(e)}")
                time.sleep(5)  # Пауза перед повторной попыткой
            finally:
                try:
                    if 'connection' in locals() and connection.is_open:
                        connection.close()
                except:
                    pass

# Инициализация менеджера событий
event_manager = EventManager(
    rabbitmq_host=RABBITMQ_HOST,
    rabbitmq_port=RABBITMQ_PORT,
    rabbitmq_user=RABBITMQ_USER,
    rabbitmq_password=RABBITMQ_PASSWORD
)

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    response = requests.get(f"{API_URL}/api/users/{user_id}/")
    
    if response.status_code == 200:
        event_manager.send_event(
            'user_events',
            'user_returned',
            {'user_id': user_id}
        )
        await message.answer(
            "🎉 Добро пожаловать назад! Ваш профиль уже создан.\n\n"
            "Доступные команды:\n"
            "- /next - Смотреть анкеты\n"
            "- /edit - Редактировать профиль\n"
            "- /matches - Посмотреть мэтчи"
        )
        return
    
    await message.answer(
        "👋 Привет! Давай создадим твой профиль для знакомств.\n"
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
            
            # Отправляем событие создания/обновления профиля
            event_manager.send_event(
                'user_events',
                'profile_created' if check_response.status_code != 200 else 'profile_updated',
                {
                    'user_id': message.from_user.id,
                    'profile_data': user_data
                }
            )
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

@dp.message(Command("next"))
async def next_profile(message: types.Message):
    user_id = message.from_user.id
    queue_length = await queue_manager.get_queue_length(user_id)
    
    if queue_length == 0:
        try:
            response = requests.get(
                f"{API_URL}/api/users/",
                params={
                    'exclude_user': user_id,
                    'limit': 20
                }
            )
            if response.status_code != 200:
                logger.error(f"Error loading profiles: {response.status_code} - {response.text}")
                await message.answer("🚫 Произошла ошибка при загрузке анкет. Попробуйте позже!")
                return
                
            profiles = response.json()
            
            if not profiles:
                await message.answer("😔 Пока нет доступных анкет. Попробуйте позже!")
                return
                
            # Проверяем, что в списке нет текущего пользователя и уже просмотренных анкет
            seen_profiles = set()
            filtered_profiles = []
            
            for profile in profiles:
                profile_id = str(profile['telegram_id'])
                if (profile_id != str(user_id) and 
                    profile_id not in seen_profiles):
                    filtered_profiles.append(profile)
                    seen_profiles.add(profile_id)
            
            if not filtered_profiles:
                await message.answer("😔 Пока нет доступных анкет. Попробуйте позже!")
                return
                
            await queue_manager.add_profiles_to_queue(user_id, filtered_profiles)
            
        except Exception as e:
            logger.error(f"Error loading profiles: {str(e)}")
            await message.answer("🚫 Произошла ошибка при загрузке анкет. Попробуйте позже!")
            return

    # Получаем следующий профиль и проверяем, что это не анкета текущего пользователя
    while True:
        profile = await queue_manager.get_next_profile(user_id)
        if not profile:
            await message.answer("😔 Анкеты закончились. Попробуйте позже!")
            return
            
        if str(profile['telegram_id']) != str(user_id):
            break
            
        logger.warning(f"Found own profile in queue for user {user_id}")

    profile_text = (
        f"👤 {profile['name']}, {profile['age']}\n"
        f"🏙 {profile['city']}\n\n"
        f"📝 {profile['bio']}"
    )

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="👍 Нравится", callback_data=f"like_{profile['telegram_id']}"),
            types.InlineKeyboardButton(text="👎 Пропустить", callback_data=f"skip_{profile['telegram_id']}")
        ]
    ])

    try:
        # Получаем изображения пользователя
        response = requests.get(f"{API_URL}/api/images/", params={'telegram_id': profile['telegram_id']})
        if response.status_code != 200:
            logger.error(f"Error loading images: {response.status_code} - {response.text}")
            await message.answer(
                profile_text,
                reply_markup=keyboard
            )
            return
            
        images = response.json()
        if not images:
            await message.answer(
                profile_text,
                reply_markup=keyboard
            )
            return
            
        # Отправляем все изображения
        for i, image in enumerate(images):
            try:
                # Получаем путь к изображению в MinIO
                image_path = image['image'].split(f"{MINIO_BUCKET}/")[-1]
                
                # Скачиваем изображение из MinIO
                input_file = await download_image_from_minio(image_path)
                if not input_file:
                    logger.error(f"Failed to download image {image_path}")
                    continue
                
                if i == 0:
                    # Первое изображение отправляем с текстом профиля
                    await message.answer_photo(
                        photo=input_file,
                        caption=profile_text,
                        reply_markup=keyboard
                    )
                else:
                    # Остальные изображения отправляем без текста
                    await message.answer_photo(
                        photo=input_file
                    )
                        
            except Exception as e:
                logger.error(f"Error sending image {i}: {str(e)}")
                continue
            
    except Exception as e:
        logger.error(f"Error sending profile: {str(e)}")
        await message.answer(
            profile_text,
            reply_markup=keyboard
        )

@dp.callback_query(lambda c: c.data.startswith(('like_', 'skip_')))
async def process_profile_action(callback_query: types.CallbackQuery):
    action, profile_id = callback_query.data.split('_')
    user_id = callback_query.from_user.id
    
    # Проверяем, что пользователь не пытается лайкнуть/пропустить свою анкету
    if str(profile_id) == str(user_id):
        await callback_query.answer("Вы не можете лайкнуть или пропустить свою анкету!")
        return
    
    try:
        # Отправляем запрос на создание лайка
        response = requests.post(
            f"{API_URL}/api/swipe/",
            json={
                'from_user': user_id,
                'to_user': int(profile_id),
                'is_skip': action == 'skip'
            }
        )
        
        if response.status_code != 201:
            logger.error(f"Error processing swipe: {response.status_code} - {response.text}")
            await callback_query.message.answer("Произошла ошибка при обработке действия. Попробуйте позже.")
            return
            
        result = response.json()
        
        # Отправляем событие лайка/пропуска
        event_manager.send_event(
            'interaction_events',
            'like' if action == 'like' else 'skip',
            {
                'from_user': user_id,
                'to_user': int(profile_id),
                'timestamp': datetime.utcnow().isoformat()
            }
        )
        
        if action == 'like' and result.get('match'):
            # Отправляем событие мэтча
            event_manager.send_event(
                'matches',
                'new_match',
                {
                    'user1_id': user_id,
                    'user2_id': int(profile_id),
                    'timestamp': datetime.utcnow().isoformat()
                }
            )
            await callback_query.message.answer(f"🎉 У вас взаимная симпатия с {profile_id}!")
            await bot.send_message(profile_id, f"🎉 У вас взаимная симпатия с {user_id}!")
    
    except Exception as e:
        logger.error(f"Error processing swipe: {str(e)}")
        await callback_query.message.answer("Произошла ошибка при обработке действия. Попробуйте позже.")
    
    await callback_query.message.delete()
    await next_profile(callback_query.message)

async def main():
    await queue_manager.connect()
    event_manager.start()
    try:
        await dp.start_polling(bot)
    finally:
        await queue_manager.disconnect()
        event_manager.stop()

if __name__ == '__main__':
    asyncio.run(main())

