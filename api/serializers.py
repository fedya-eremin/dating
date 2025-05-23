import logging
from rest_framework import serializers
from .models import User, UserImage, Like, Match, Referral
from django.conf import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class UserImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    
    class Meta:
        model = UserImage
        fields = ['id', 'image', 'image_url', 'created_at', 'is_main']
        
    def get_image_url(self, obj):
        if isinstance(obj, dict):
            return None
        if obj.image:
            return obj.image.url
        return None

    def validate_image(self, value):
        logger.info(f"Validating image: {value}")
        logger.info(f"Image content type: {value.content_type}")
        logger.info(f"Image size: {value.size}")
        
        if not value:
            raise serializers.ValidationError("Изображение обязательно")
        
        # Проверяем размер файла (максимум 10MB)
        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Размер файла не должен превышать 10MB")
        
        # Проверяем тип файла
        if not value.content_type.startswith('image/'):
            raise serializers.ValidationError("Файл должен быть изображением")
        
        return value

    def validate(self, data):
        logger.info(f"Validating data: {data}")
        return data

    def create(self, validated_data):
        logger.info(f"Creating image with data: {validated_data}")
        try:
            # Проверяем наличие всех необходимых полей
            if 'user' not in validated_data:
                raise serializers.ValidationError("User is required")
            if 'image' not in validated_data:
                raise serializers.ValidationError("Image is required")
            
            instance = super().create(validated_data)
            
            # Если это первое изображение пользователя, делаем его главным
            if instance.user.images.count() == 1:
                instance.is_main = True
                instance.save()
            
            return instance
        except Exception as e:
            logger.error(f"Error creating image: {str(e)}")
            raise serializers.ValidationError(f"Ошибка создания изображения: {str(e)}")

class UserSerializer(serializers.ModelSerializer):
    images = UserImageSerializer(many=True, read_only=True)
    primary_rating = serializers.FloatField(read_only=True)
    behavioral_rating = serializers.FloatField(read_only=True)
    combined_rating = serializers.FloatField(read_only=True)
    likes_count = serializers.IntegerField(read_only=True)
    skips_count = serializers.IntegerField(read_only=True)
    matches_count = serializers.IntegerField(read_only=True)
    conversations_initiated = serializers.IntegerField(read_only=True)
    telegram_id = serializers.IntegerField(required=True)

    class Meta:
        model = User
        fields = [
            'telegram_id', 'name', 'gender', 'age', 'seeking_gender',
            'city', 'bio', 'referral_code', 'referrer', 'last_activity',
            'primary_rating', 'behavioral_rating', 'combined_rating',
            'likes_count', 'skips_count', 'matches_count', 'conversations_initiated',
            'images'
        ]
        read_only_fields = [
            'referral_code', 'last_activity',
            'primary_rating', 'behavioral_rating', 'combined_rating',
            'likes_count', 'skips_count', 'matches_count', 'conversations_initiated'
        ]

class LikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Like
        fields = ['id', 'from_user', 'to_user', 'is_skip', 'created_at']
        read_only_fields = ['created_at']

class MatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Match
        fields = [
            'id', 'user1', 'user2', 'is_active', 'created_at'
        ]
        read_only_fields = ['created_at']

class ReferralSerializer(serializers.ModelSerializer):
    referrer = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    referred_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = Referral
        fields = ['id', 'referrer', 'referred_user', 'created_at']
        read_only_fields = ['created_at']
