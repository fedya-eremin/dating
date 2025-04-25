from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Like, User, UserImage, Match
from .serializers import UserImageSerializer
from django.db.models import Q
import logging
from .views import (
    UserViewSet, LikeViewSet, MatchViewSet, 
    ReferralViewSet, UserImageViewSet
)

logger = logging.getLogger(__name__)

class SwipeView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        try:
            from_user_id = request.data.get('from_user')
            to_user_id = request.data.get('to_user')
            is_skip = request.data.get('is_skip', False)
            
            if not from_user_id or not to_user_id:
                return Response(
                    {'error': 'from_user and to_user are required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Получаем пользователей
            from_user = User.objects.filter(telegram_id=from_user_id).first()
            to_user = User.objects.filter(telegram_id=to_user_id).first()
            
            if not from_user or not to_user:
                return Response(
                    {'error': 'User not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Создаем лайк
            like = Like.objects.create(
                from_user=from_user,
                to_user=to_user,
                is_skip=is_skip
            )
            
            result = {'success': True}
            
            # Проверяем на взаимный лайк
            if not is_skip:
                mutual_like = Like.objects.filter(
                    from_user=to_user,
                    to_user=from_user,
                    is_skip=False
                ).exists()
                
                if mutual_like:
                    # Создаем мэтч
                    match = Match.objects.create(
                        user1=from_user,
                        user2=to_user
                    )
                    result['match'] = True
            
            return Response(result, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error processing swipe: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class UserImagesView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        telegram_id = request.query_params.get('telegram_id')
        if not telegram_id:
            return Response(
                {'error': 'telegram_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            images = UserImage.objects.filter(user__telegram_id=telegram_id)
            serializer = UserImageSerializer(images, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error getting images: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'users/(?P<user_id>\d+)/images', UserImageViewSet, basename='user-images')
router.register(r'likes', LikeViewSet)
router.register(r'matches', MatchViewSet)
router.register(r'referrals', ReferralViewSet)
router.register(r'images', UserImageViewSet, basename='images')

urlpatterns = [
    path('', include(router.urls)),
    # Добавляем специальные эндпоинты для бота
    path('swipe/', SwipeView.as_view(), name='swipe'),
    path('images/', UserImagesView.as_view(), name='user-images'),
    path('referrals/use/', ReferralViewSet.as_view({'post': 'use_referral'}), name='use-referral'),
] 