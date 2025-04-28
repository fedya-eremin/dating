import io
import os
from io import BytesIO
from typing import Optional
from minio import Minio
from aiogram.types import BufferedInputFile
from bot.logger import logger

# Настройки MinIO
MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'minio:9000')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
MINIO_BUCKET = os.getenv('MINIO_BUCKET', 'media')

# Инициализация клиента MinIO
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False  # Используем HTTP вместо HTTPS
)

async def download_image_from_minio(image_path: str) -> Optional[BufferedInputFile]:
    """Скачивает изображение из MinIO и возвращает BufferedInputFile"""
    try:
        # Validate image path exists
        if not image_path:
            logger.error("Empty image path provided")
            return None

        # Check if object exists first
        try:
            minio_client.stat_object(MINIO_BUCKET, image_path)
        except Exception as e:
            logger.error(f"Image not found in storage: {image_path}")
            return None

        # Use in-memory buffer instead of temp file
        buffer = minio_client.get_object(MINIO_BUCKET, image_path)
        
        return BufferedInputFile(io.BytesIO(buffer.read()).getvalue(), filename=image_path)

    except Exception as e:
        logger.error(f"MinIO error: {str(e)}")
        return None
