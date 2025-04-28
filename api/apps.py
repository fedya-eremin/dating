from django.apps import AppConfig
from storages.backends.s3boto3 import S3Boto3Storage
import logging

logger = logging.getLogger(__name__)

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        try:
            storage = S3Boto3Storage()
            if not storage.bucket.exists():
                storage.bucket.create()
                logger.info("MinIO bucket 'media' created successfully")
        except Exception as e:
            logger.error(f"Error creating MinIO bucket: {str(e)}")
