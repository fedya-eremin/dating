import logging
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from .models import User, UserImage, Like, Match, Referral
from .serializers import (
    UserSerializer,
    UserImageSerializer,
    LikeSerializer,
    MatchSerializer,
    ReferralSerializer
)
from django.db.models import Q
from rest_framework import serializers
from rest_framework.generics import CreateAPIView
from rest_framework.views import APIView
import redis
import json
import os

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')


class UserImageViewSet(viewsets.ModelViewSet):
    queryset = UserImage.objects.all()
    serializer_class = UserImageSerializer
    parser_classes = [MultiPartParser]
    permission_classes = [AllowAny]

    def get_queryset(self):
        telegram_id = self.request.query_params.get('telegram_id')
        if telegram_id:
            return UserImage.objects.filter(user__telegram_id=telegram_id)
        return UserImage.objects.none()

    def perform_create(self, serializer):
        telegram_id = self.request.data.get('telegram_id')
        if not telegram_id:
            raise serializers.ValidationError({"telegram_id": "Это поле обязательно"})
        
        try:
            user = User.objects.get(telegram_id=telegram_id)
        except User.DoesNotExist:
            raise serializers.ValidationError({"telegram_id": "Пользователь не найден"})
        
        # Проверяем наличие файла
        image = self.request.FILES.get('image')
        if not image:
            raise serializers.ValidationError({"image": "Файл изображения обязателен"})
        
        # Проверяем тип файла
        if not image.content_type.startswith('image/'):
            raise serializers.ValidationError({"image": "Файл должен быть изображением"})
        
        try:
            # Сохраняем изображение
            instance = serializer.save(user=user)
            
            # Проверяем, что файл действительно сохранился
            if not instance.image:
                raise serializers.ValidationError({"image": "Ошибка сохранения изображения"})
            
            # Проверяем доступность файла
            if not instance.image.storage.exists(instance.image.name):
                raise serializers.ValidationError({"image": "Ошибка сохранения в хранилище"})
            
            # Пересчитываем рейтинг пользователя
            user.calculate_primary_rating()
            
            logger.info(f"Successfully uploaded image for user {telegram_id}: {instance.image.url}")
            
        except Exception as e:
            logger.error(f"Error saving image for user {telegram_id}: {str(e)}")
        
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    lookup_field = 'telegram_id'
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = User.objects.all()
        if self.action == 'list':
            # Фильтрация для поиска подходящих партнеров
            exclude_user = self.request.query_params.get('exclude_user')
            if exclude_user:
                exclude_user = User.objects.get(telegram_id=exclude_user)
                queryset = queryset.exclude(id=exclude_user.id)
                
                # Фильтруем по предпочтениям пола
                queryset = queryset.filter(
                    gender=exclude_user.seeking_gender,
                    seeking_gender=exclude_user.gender
                )
                
                # Исключаем пользователей, с которыми уже есть лайки
                liked_users = Like.objects.filter(
                    from_user__telegram_id=exclude_user.telegram_id
                ).values_list('to_user__telegram_id', flat=True)
                queryset = queryset.exclude(telegram_id__in=liked_users)
                
                # Исключаем пользователей, с которыми уже есть мэтчи
                matched_users = Match.objects.filter(
                    Q(user1__telegram_id=exclude_user.telegram_id) | Q(user2__telegram_id=exclude_user.telegram_id),
                    is_active=True
                ).values_list('user1__telegram_id', 'user2__telegram_id')
                matched_ids = set()
                for user1, user2 in matched_users:
                    matched_ids.add(user1)
                    matched_ids.add(user2)
                queryset = queryset.exclude(telegram_id__in=matched_ids)
                
                # Сортируем по рейтингу
                queryset = queryset.order_by('-combined_rating')
                
                # Ограничиваем количество результатов
                limit = int(self.request.query_params.get('limit', 20))
                queryset = queryset[:limit]

                try:
                    redis_client = redis.from_url(REDIS_URL)
                    profiles_data = [UserSerializer(profile).data for profile in queryset]
                    queue_key = f"profile_queue:{exclude_user.telegram_id}"
                    # Добавляем новые профили
                    if profiles_data:
                        redis_client.rpush(queue_key, *[json.dumps(profile) for profile in profiles_data])
                        logger.info(f"Added {len(profiles_data)} profiles to queue for user {exclude_user.telegram_id}")
                except Exception as e:
                    logger.error(f"Error adding profiles to Redis queue: {str(e)}")
        return queryset

    @action(detail=True, methods=['post'])
    def upload_image(self, request, telegram_id=None):
        try:
            user = self.get_object()
            serializer = UserImageSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(user=user)
                user.calculate_primary_rating()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['get'])
    def ratings(self, request, telegram_id=None):
        user = self.get_object()
        return Response({
            'primary_rating': user.primary_rating,
            'behavioral_rating': user.behavioral_rating,
            'combined_rating': user.combined_rating
        })

    def create(self, request, *args, **kwargs):
        try:
            telegram_id = request.data.get('telegram_id')
            if not telegram_id:
                return Response(
                    {'error': 'telegram_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Проверяем существование пользователя
            user = User.objects.filter(telegram_id=telegram_id).first()
            if user:
                # Обновляем существующего пользователя
                serializer = self.get_serializer(user, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)

            # Создаем нового пользователя
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error creating/updating user: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class LikeViewSet(viewsets.ModelViewSet):
    queryset = Like.objects.all()
    serializer_class = LikeSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        like = serializer.save()
        if not like.is_skip:
            # Проверяем на взаимный лайк
            mutual_like = Like.objects.filter(
                from_user=like.to_user,
                to_user=like.from_user,
                is_skip=False
            ).exists()
            if mutual_like:
                Match.objects.create(
                    user1=like.from_user,
                    user2=like.to_user
                )

class ReferralViewSet(viewsets.ModelViewSet):
    queryset = Referral.objects.all()
    serializer_class = ReferralSerializer
    permission_classes = [AllowAny]


class MatchViewSet(viewsets.ModelViewSet):
    queryset = Match.objects.all()
    serializer_class = MatchSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=['get'])
    def check(self, request):
        """Проверяет, есть ли мэтч между двумя пользователями"""
        user1_id = request.query_params.get('user1')
        user2_id = request.query_params.get('user2')
        
        if not user1_id or not user2_id:
            return Response(
                {'error': 'Требуются оба параметра: user1 и user2'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            user1 = User.objects.get(telegram_id=user1_id)
            user2 = User.objects.get(telegram_id=user2_id)
            
            # Проверяем наличие взаимных лайков
            user1_liked = Like.objects.filter(
                from_user=user1,
                to_user=user2,
                is_skip=False
            ).exists()
            
            user2_liked = Like.objects.filter(
                from_user=user2,
                to_user=user1,
                is_skip=False
            ).exists()
            
            is_match = user1_liked and user2_liked
            
            return Response({'is_match': is_match})
            
        except Exception:
            return Response(
                {'error': 'Один из пользователей не найден'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'])
    def mark_conversation_initiated(self, request, pk=None):
        match = self.get_object()
        if match.user1 == request.user or match.user2 == request.user:
            match.mark_conversation_initiated(request.user)
            return Response({'status': 'conversation marked as initiated'})
        return Response(
            {'error': 'You are not part of this match'},
            status=status.HTTP_403_FORBIDDEN
        )

class MatchCreateView(CreateAPIView):
    serializer_class = MatchSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        # Get telegram IDs from query params
        user1_tg_id = self.request.query_params.get('user1')
        user2_tg_id = self.request.query_params.get('user2')
        
        # Validate required parameters
        if not user1_tg_id or not user2_tg_id:
            raise serializers.ValidationError("Требуются оба параметра: user1 и user2")
            
        try:
            # Get actual User objects by telegram_id
            user1 = User.objects.get(telegram_id=user1_tg_id)
            user2 = User.objects.get(telegram_id=user2_tg_id)
        except User.DoesNotExist:
            raise serializers.ValidationError("Один из пользователей не найден")

        # Check for existing active match using User instances
        if Match.objects.filter(
            (Q(user1=user1, user2=user2) | Q(user1=user2, user2=user1)),
            is_active=True
        ).exists():
            raise serializers.ValidationError("Матч между этими пользователями уже существует")
        
        # Create new match with both User instances
        serializer.save(user1=user1, user2=user2)

class UserImageView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser]
    
    def post(self, request):
        try:
            logger.info(f"Received request data: {request.data}")
            logger.info(f"Received request FILES: {request.FILES}")
            
            # Получаем telegram_id из разных источников
            telegram_id = None
            
            # Сначала пробуем получить из FILES
            telegram_id_file = request.FILES.get('telegram_id')
            if telegram_id_file:
                telegram_id = telegram_id_file.read().decode('utf-8').strip()
            
            # Если не нашли в FILES, пробуем получить из data
            if not telegram_id:
                telegram_id = request.data.get('telegram_id')
            
            logger.info(f"Extracted telegram_id: {telegram_id}")
            
            if not telegram_id:
                return Response(
                    {"error": "telegram_id is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Проверяем наличие пользователя
            try:
                user = User.objects.get(telegram_id=telegram_id)
                logger.info(f"Found user: {user.id}")
            except User.DoesNotExist:
                return Response(
                    {"error": "User not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Проверяем наличие файла
            if 'image' not in request.FILES:
                return Response(
                    {"error": "No image file provided"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            image_file = request.FILES['image']
            logger.info(f"Image file: {image_file}")
            logger.info(f"Image content type: {image_file.content_type}")
            logger.info(f"Image size: {image_file.size}")
            
            # Проверяем тип файла
            if not image_file.content_type.startswith('image/'):
                return Response(
                    {"error": "File must be an image"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Проверяем размер файла (10MB)
            if image_file.size > 10 * 1024 * 1024:
                return Response(
                    {"error": "Image size must be less than 10MB"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Создаем изображение
            image = UserImage.objects.create(
                user=user,
                image=image_file
            )
            
            # Если это первое изображение пользователя, делаем его главным
            if not UserImage.objects.filter(user=user, is_main=True).exists():
                image.is_main = True
                image.save()
            
            # Обновляем рейтинг пользователя
            user.calculate_primary_rating()
            
            # Сериализуем результат
            serializer = UserImageSerializer(image)
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
                
        except Exception as e:
            logger.error(f"Error creating user image: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
