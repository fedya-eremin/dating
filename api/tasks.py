from celery import shared_task
from .models import User
from django.db.models import F
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

@shared_task
def recalculate_primary_ratings():
    """Пересчет первичных рейтингов для всех пользователей"""
    users = User.objects.all()
    updated_count = 0
    
    for user in users:
        try:
            user.calculate_primary_rating()
            updated_count += 1
        except Exception as e:
            logger.error(f"Error calculating primary rating for user {user.telegram_id}: {str(e)}")
    
    logger.info(f"Updated primary ratings for {updated_count} users")
    return updated_count

@shared_task
def recalculate_behavioral_ratings():
    """Пересчет поведенческих рейтингов для всех пользователей"""
    users = User.objects.all()
    updated_count = 0
    
    for user in users:
        try:
            user.calculate_behavioral_rating()
            updated_count += 1
        except Exception as e:
            logger.error(f"Error calculating behavioral rating for user {user.telegram_id}: {str(e)}")
    
    logger.info(f"Updated behavioral ratings for {updated_count} users")
    return updated_count

@shared_task
def recalculate_combined_ratings():
    """Пересчет комбинированных рейтингов для всех пользователей"""
    users = User.objects.all()
    updated_count = 0
    
    for user in users:
        try:
            user.calculate_combined_rating()
            updated_count += 1
        except Exception as e:
            logger.error(f"Error calculating combined rating for user {user.telegram_id}: {str(e)}")
    
    logger.info(f"Updated combined ratings for {updated_count} users")
    return updated_count

@shared_task
def recalculate_all_ratings():
    """Пересчет всех типов рейтингов"""
    primary_count = recalculate_primary_ratings()
    behavioral_count = recalculate_behavioral_ratings()
    combined_count = recalculate_combined_ratings()
    
    return {
        'primary_ratings': primary_count,
        'behavioral_ratings': behavioral_count,
        'combined_ratings': combined_count
    } 