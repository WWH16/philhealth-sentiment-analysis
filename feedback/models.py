import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

DEFAULT_OFFLINE_MESSAGE = 'Ang feedback system ay pansamantalang hindi available. Pakisubukan muli mamaya.'


class FeedbackToken(models.Model):
    """Single-use feedback link that staff hand to one served client as a QR code.

    Office ticket numbers cycle, so the ticket number alone cannot identify a
    visit; the random key can, and it only works once and only until it expires.
    """
    LIFETIME = timedelta(minutes=15)

    key = models.CharField(max_length=32, unique=True)
    ticket_number = models.CharField(max_length=6)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, ticket_number, user):
        return cls.objects.create(
            key=secrets.token_urlsafe(16),
            ticket_number=ticket_number,
            created_by=user,
            expires_at=timezone.now() + cls.LIFETIME,
        )

    @classmethod
    def usable(cls):
        return cls.objects.filter(used_at__isnull=True, expires_at__gt=timezone.now())


class FeedbackEntry(models.Model):
    # Experience Ratings (Service Quality Dimensions - SQD).
    # The client picks a rating; the model reads the comment into
    # comment_sentiment. The final `sentiment` combines both
    # (see services.combine_sentiment), but the rating alone never sets it.
    VERY_SATISFACTORY = 'vsat'
    SATISFACTORY = 'sat'
    UNSATISFACTORY = 'unsat'

    EXPERIENCE_CHOICES = [
        (VERY_SATISFACTORY, 'Very Satisfactory'),
        (SATISFACTORY, 'Satisfactory'),
        (UNSATISFACTORY, 'Unsatisfactory'),
    ]

    NOT_APPLICABLE = 'na'

    # Sentiments (Detected or Manual)
    POSITIVE = 'pos'
    NEUTRAL = 'neu'
    NEGATIVE = 'neg'
    PENDING = 'pending'

    SENTIMENT_CHOICES = [
        (POSITIVE, 'Positive'),
        (NEUTRAL, 'Neutral'),
        (NEGATIVE, 'Negative'),
        (PENDING, 'Pending'),
        (NOT_APPLICABLE, 'N/A'),
    ]

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        ('reviewed', 'Reviewed'),
        ('resolved', 'Resolved'),
    ]

    COMPLAINT = 'complaint'
    SUGGESTION = 'suggestion'
    COMPLIMENT = 'compliment'
    CONCERN = 'concern'

    CATEGORY_CHOICES = [
        (COMPLAINT, 'Complaint'),
        (SUGGESTION, 'Suggestion'),
        (COMPLIMENT, 'Compliment'),
        (CONCERN, 'Concern'),
    ]

    # What the feedback is about, picked by the client on the form (one or
    # more). Separate from `category`, which staff set.
    TOPIC_CHOICES = [
        ('waiting_time', 'Waiting time'),
        ('staff', 'Staff'),
        ('facilities', 'Facilities'),
        ('documents', 'Document requirements'),
        ('other', 'Other'),
    ]
    TOPIC_ICONS = {'waiting_time': 'schedule', 'staff': 'support_agent', 'facilities': 'apartment', 'documents': 'description', 'other': 'more_horiz'}
    experience = models.CharField(max_length=25, choices=EXPERIENCE_CHOICES)
    # Final sentiment: comment_sentiment adjusted by the rating.
    sentiment = models.CharField(max_length=10, choices=SENTIMENT_CHOICES, default=PENDING)
    # What the model read from the comment alone.
    comment_sentiment = models.CharField(max_length=10, choices=SENTIMENT_CHOICES, default=PENDING)
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, blank=True)
    # List of TOPIC_CHOICES values. Counted in Python, so no JSON lookups are needed.
    topics = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    # Queue number from the office; numbers cycle, so it is a label, not an identity.
    ticket_number = models.CharField(max_length=6, blank=True)
    # One response per QR link, enforced by the database.
    token = models.OneToOneField(FeedbackToken, on_delete=models.SET_NULL, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'Feedback #{self.pk} - {self.get_experience_display()}'


class FeedbackConfiguration(models.Model):
    survey_enabled = models.BooleanField(
        default=True,
        help_text='Controls whether citizens can access and submit the public feedback form.'
    )
    survey_offline_message = models.TextField(
        blank=True,
        default=DEFAULT_OFFLINE_MESSAGE,
        help_text='Notice shown to citizens when public feedback submissions are disabled.'
    )
    auto_analysis_enabled = models.BooleanField(default=True)
    daily_summary_enabled = models.BooleanField(default=False)
    notification_email = models.EmailField(blank=True, default='')
    daily_summary_time = models.CharField(max_length=5, default='16:30')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'feedback configuration'
        verbose_name_plural = 'feedback configuration'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        state = 'enabled' if self.auto_analysis_enabled else 'disabled'
        survey_state = 'active' if self.survey_enabled else 'paused'
        return f'Feedback configuration (survey: {survey_state}, auto-analysis: {state})'

    @classmethod
    def get_solo(cls):
        try:
            config, _ = cls.objects.get_or_create(pk=1)
        except (OperationalError, ProgrammingError):
            return cls(pk=1)
        return config

    @classmethod
    def get_survey_offline_message(cls):
        return cls.get_solo().survey_offline_message or DEFAULT_OFFLINE_MESSAGE

