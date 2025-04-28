from typing import Optional
import json
import os
import redis.asyncio as redis
from bot.logger import logger

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')


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

    async def add_profiles_to_queue(self, user_id: int, profiles: list) -> None:
        if not self.connected:
            await self.connect()
            
        queue_key = self.get_queue_key(user_id)
        try:
            # Добавляем профили в очередь
            for profile in profiles:
                await self.redis.rpush(queue_key, json.dumps(profile))
            logger.info(f"Added {len(profiles)} profiles to queue for user {user_id}")
        except Exception as e:
            logger.error(f"Error adding profiles to queue: {str(e)}")
            raise


# Инициализация менеджера очереди
queue_manager = ProfileQueueManager(REDIS_URL)
