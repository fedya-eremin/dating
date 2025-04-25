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
import shortuuid
from .events import event_producer
from django.db.models import Q
from rest_framework import serializers
from rest_framework.generics import CreateAPIView
import logging
from rest_framework.views import APIView
import asyncio

logger = logging.getLogger(__name__)

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
            raise serializers.ValidationError({"image": f"Ошибка сохранения изображения: {str(e)}"})

    def create(self, request, *args, **kwargs):
        try:
            response = super().create(request, *args, **kwargs)
            # Добавляем URL изображения в ответ
            if response.status_code == status.HTTP_201_CREATED:
                response.data['image_url'] = response.data.get('image')
            return response
        except serializers.ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error uploading image: {str(e)}")
            return Response(
                {'error': 'Failed to upload image', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    lookup_field = 'telegram_id'
    permission_classes = [AllowAny]  # Разрешаем доступ без аутентификации

    def get_queryset(self):
        queryset = User.objects.all()
        if self.action == 'list':
            # Фильтрация для поиска подходящих партнеров
            exclude_user = self.request.query_params.get('exclude_user')
            if exclude_user:
                # Исключаем текущего пользователя
                queryset = queryset.exclude(telegram_id=exclude_user)
                
                # Получаем предпочтения текущего пользователя
                current_user = User.objects.filter(telegram_id=exclude_user).first()
                if current_user:
                    # Фильтруем по предпочтениям пола
                    queryset = queryset.filter(
                        gender=current_user.seeking_gender,
                        seeking_gender=current_user.gender
                    )
                    
                    # Исключаем пользователей, с которыми уже есть лайки
                    liked_users = Like.objects.filter(
                        from_user__telegram_id=exclude_user
                    ).values_list('to_user__telegram_id', flat=True)
                    queryset = queryset.exclude(telegram_id__in=liked_users)
                    
                    # Исключаем пользователей, с которыми уже есть мэтчи
                    matched_users = Match.objects.filter(
                        Q(user1__telegram_id=exclude_user) | Q(user2__telegram_id=exclude_user),
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

    def perform_create(self, serializer):
        instance = serializer.save()
        # Отправляем событие создания профиля
        try:
            event_producer.send_event(
                'user_events',
                'profile_created',
                {
                    'user_id': instance.telegram_id,
                    'profile_data': self.get_serializer(instance).data
                }
            )
        except Exception as e:
            logger.error(f"Error sending event: {str(e)}")

    def perform_update(self, serializer):
        instance = serializer.save()
        # Отправляем событие обновления профиля
        try:
            event_producer.send_event(
                'user_events',
                'profile_updated',
                {
                    'user_id': instance.telegram_id,
                    'profile_data': self.get_serializer(instance).data
                }
            )
        except Exception as e:
            logger.error(f"Error sending event: {str(e)}")

class LikeViewSet(viewsets.ModelViewSet):
    queryset = Like.objects.all()
    serializer_class = LikeSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        like = serializer.save(from_user=self.request.user)
        if not like.is_skip:
            # Проверяем на взаимный лайк
            mutual_like = Like.objects.filter(
                from_user=like.to_user,
                to_user=like.from_user,
                is_skip=False
            ).exists()
            if mutual_like:
                # Создаем мэтч
                Match.objects.create(
                    user1=like.from_user,
                    user2=like.to_user
                )

class ReferralViewSet(viewsets.ModelViewSet):
    queryset = Referral.objects.all()
    serializer_class = ReferralSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Referral.objects.filter(referrer=self.request.user)

    @action(detail=True, methods=['POST'])
    def use_referral(self, request, pk=None):
        referrer = self.get_object()
        referred_user = User.objects.get(telegram_id=request.data['telegram_id'])
        
        referral, created = Referral.objects.get_or_create(
            referrer=referrer,
            referred_user=referred_user
        )
        
        return Response({'status': 'referral applied'}, status=status.HTTP_201_CREATED)

class MatchViewSet(viewsets.ModelViewSet):
    queryset = Match.objects.all()
    serializer_class = MatchSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Match.objects.filter(
            Q(user1=self.request.user) | Q(user2=self.request.user),
            is_active=True
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

    def perform_create(self, serializer):
        instance = serializer.save()
        event_producer.send_event(
            'matches',
            'new_match',
            {
                'user1_id': instance.user1.telegram_id,
                'user2_id': instance.user2.telegram_id,
                'timestamp': instance.created_at.isoformat()
            }
        )

class MatchCreateView(CreateAPIView):
    serializer_class = MatchSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        user1 = self.request.user
        user2 = serializer.validated_data['user2']
        
        # Проверяем, не существует ли уже матч между этими пользователями
        if Match.objects.filter(
            (Q(user1=user1, user2=user2) | Q(user1=user2, user2=user1)),
            is_active=True
        ).exists():
            raise serializers.ValidationError("Матч между этими пользователями уже существует")
        
        serializer.save(user1=user1)

class UserImageView(APIView):
    permission_classes = [AllowAny]  # Разрешаем доступ без аутентификации для бота
    
    def post(self, request):
        try:
            telegram_id = request.data.get('telegram_id')
            if not telegram_id:
                return Response(
                    {"error": "telegram_id is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Находим пользователя по telegram_id
            try:
                user = User.objects.get(telegram_id=telegram_id)
            except User.DoesNotExist:
                return Response(
                    {"error": "User not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Проверяем, что файл изображения был отправлен
            if 'image' not in request.FILES:
                return Response(
                    {"error": "No image file provided"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Создаем изображение
            image = UserImage.objects.create(
                user=user,
                image=request.FILES['image']
            )
            
            # Если это первое фото - делаем его главным
            if not UserImage.objects.filter(user=user, is_main=True).exists():
                image.is_main = True
                image.save()
            
            return Response(
                UserImageSerializer(image).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            logger.error(f"Error creating user image: {str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
