import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser
from storages.backends.s3boto3 import S3Boto3Storage
from django.db.models import Avg, Count, F, ExpressionWrapper, FloatField


class User(AbstractBaseUser):
    USERNAME_FIELD = 'telegram_id'
    REQUIRED_FIELDS = ['name', 'age']

    GENDER_CHOICES = (
        ('M', 'Мужской'),
        ('F', 'Женский'),
    )
    
    telegram_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=100, verbose_name="Имя пользователя")
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    age = models.PositiveIntegerField()
    seeking_gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    city = models.CharField(max_length=100)
    bio = models.TextField(blank=True)
    referral_code = models.CharField(max_length=100, unique=True, blank=True, default=uuid.uuid4)
    referrer = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    last_activity = models.DateTimeField(auto_now=True)

    # Поля для рейтинговой системы
    primary_rating = models.FloatField(default=0.0, verbose_name="Первичный рейтинг")
    behavioral_rating = models.FloatField(default=0.0, verbose_name="Поведенческий рейтинг")
    combined_rating = models.FloatField(default=0.0, verbose_name="Комбинированный рейтинг")
    likes_count = models.PositiveIntegerField(default=0, verbose_name="Получено лайков")
    skips_count = models.PositiveIntegerField(default=0, verbose_name="Получено пропусков")
    matches_count = models.PositiveIntegerField(default=0, verbose_name="Количество мэтчей")
    conversations_initiated = models.PositiveIntegerField(default=0, verbose_name="Инициировано диалогов")

    def __str__(self):
        return f"User #{self.telegram_id}"

    def increment_likes(self):
        """Увеличить счетчик лайков"""
        self.likes_count = F('likes_count') + 1
        self.save(update_fields=['likes_count'])
        self.refresh_from_db()

    def increment_skips(self):
        """Увеличить счетчик пропусков"""
        self.skips_count = F('skips_count') + 1
        self.save(update_fields=['skips_count'])
        self.refresh_from_db()

    def increment_matches(self):
        """Увеличить счетчик матчей"""
        self.matches_count = F('matches_count') + 1
        self.save(update_fields=['matches_count'])
        self.refresh_from_db()

    def increment_conversations(self):
        """Увеличить счетчик начатых диалогов"""
        self.conversations_initiated = F('conversations_initiated') + 1
        self.save(update_fields=['conversations_initiated'])
        self.refresh_from_db()

    def calculate_primary_rating(self):
        """Расчет первичного рейтинга"""
        rating = 0.0
        
        # Базовые баллы за заполнение обязательных полей
        rating += 20 if self.name else 0
        rating += 20 if self.age else 0
        rating += 20 if self.gender else 0
        rating += 20 if self.seeking_gender else 0
        rating += 20 if self.city else 0
        
        # Дополнительные баллы за биографию
        if self.bio:
            rating += 10 if len(self.bio) >= 50 else 5
        
        # Баллы за фотографии
        photos_count = self.images.count()
        rating += min(photos_count * 10, 30)  # Максимум 30 баллов за фотографии
        
        # Нормализация до 100 баллов
        self.primary_rating = min(rating, 100)
        self.save(update_fields=['primary_rating'])

    def calculate_behavioral_rating(self):
        """Расчет поведенческого рейтинга"""
        rating = 0.0
        
        # Баллы за лайки и пропуски
        total_interactions = self.likes_count + self.skips_count
        if total_interactions > 0:
            like_ratio = self.likes_count / total_interactions
            rating += like_ratio * 40  # Максимум 40 баллов за соотношение лайков
        
        # Баллы за мэтчи
        if self.likes_count > 0:
            match_ratio = self.matches_count / self.likes_count
            rating += min(match_ratio * 30, 30)  # Максимум 30 баллов за мэтчи
        
        # Баллы за инициирование диалогов
        if self.matches_count > 0:
            conversation_ratio = self.conversations_initiated / self.matches_count
            rating += min(conversation_ratio * 30, 30)  # Максимум 30 баллов за диалоги
        
        self.behavioral_rating = min(rating, 100)
        self.save(update_fields=['behavioral_rating'])

    def calculate_combined_rating(self):
        """Расчет комбинированного рейтинга"""
        # Веса для разных типов рейтинга
        primary_weight = 0.3
        behavioral_weight = 0.7
        
        self.combined_rating = (
            self.primary_rating * primary_weight +
            self.behavioral_rating * behavioral_weight
        )
        self.save(update_fields=['combined_rating'])

    def update_ratings(self):
        """Обновление всех рейтингов"""
        self.calculate_primary_rating()
        self.calculate_behavioral_rating()
        self.calculate_combined_rating()

class UserImage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(
        upload_to='user_images/',
        storage=S3Boto3Storage(),
        max_length=255
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_main = models.BooleanField(default=False)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Image for {self.user.name}"

class Like(models.Model):
    from_user = models.ForeignKey(User, related_name='likes_given', on_delete=models.CASCADE)
    to_user = models.ForeignKey(User, related_name='likes_received', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_skip = models.BooleanField(default=False)

    class Meta:
        unique_together = ('from_user', 'to_user')

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        
        super().save(*args, **kwargs)
        
        if is_new:
            if self.is_skip:
                self.to_user.increment_skips()
            else:
                self.to_user.increment_likes()
                
                # Проверяем на взаимный лайк
                mutual_like = Like.objects.filter(
                    from_user=self.to_user,
                    to_user=self.from_user,
                    is_skip=False
                ).exists()
                
                if mutual_like:
                    # Увеличиваем счетчик мэтчей для обоих пользователей
                    self.from_user.increment_matches()
                    self.to_user.increment_matches()
                    
                    # Обновляем рейтинги обоих пользователей
                    self.from_user.update_ratings()
                    self.to_user.update_ratings()

class Match(models.Model):
    user1 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='matches_as_user1',
        default=None,
        null=True
    )
    user2 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='matches_as_user2',
        default=None,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    first_message_sent = models.BooleanField(default=False)
    first_message_sender = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='initiated_conversations'
    )

    class Meta:
        unique_together = ('user1', 'user2')
        ordering = ['-created_at']

    def __str__(self):
        return f'Match between {self.user1} and {self.user2}'

    def mark_conversation_initiated(self, sender):
        """Отметить начало диалога"""
        if not self.first_message_sent:
            self.first_message_sent = True
            self.first_message_sender = sender
            self.save()
            
            # Увеличиваем счетчик инициированных диалогов
            sender.increment_conversations()
            
            # Обновляем рейтинги
            sender.update_ratings()

class Referral(models.Model):
    referrer = models.ForeignKey(User, related_name='referrals', on_delete=models.CASCADE)
    referred_user = models.OneToOneField(User, related_name='referred_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
