import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dating.settings')

app = Celery('dating')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Настройка периодических задач
app.conf.beat_schedule = {
    'recalculate-primary-ratings': {
        'task': 'api.tasks.recalculate_primary_ratings',
        'schedule': crontab(hour='*/6'),  # Каждые 6 часов
    },
    'recalculate-behavioral-ratings': {
        'task': 'api.tasks.recalculate_behavioral_ratings',
        'schedule': crontab(hour='*/3'),  # Каждые 3 часа
    },
    'recalculate-combined-ratings': {
        'task': 'api.tasks.recalculate_combined_ratings',
        'schedule': crontab(hour='*/3'),  # Каждые 3 часа
    },
    'recalculate-all-ratings': {
        'task': 'api.tasks.recalculate_all_ratings',
        'schedule': crontab(hour=0, minute=0),  # Раз в сутки в полночь
    },
} 