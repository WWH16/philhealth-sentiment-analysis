import json

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import FeedbackConfiguration, FeedbackEntry, FeedbackToken
from .services import analyze_comment_sentiment, combine_sentiment

TOKEN_INVALID_MESSAGE = 'This feedback link has expired or was already used. Ask the staff for a new QR code.'


def index(request):
    key = request.GET.get('t', '')
    token = FeedbackToken.usable().filter(key=key).first() if key else None
    return render(request, 'feedback/index.html', {
        'token': token,
        'token_invalid': bool(key) and token is None,
        'topics': [(v, label, FeedbackEntry.TOPIC_ICONS[v]) for v, label in FeedbackEntry.TOPIC_CHOICES],
    })


@require_POST
def submit_feedback(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    key = payload.get('token')
    if not isinstance(key, str) or not key:
        return JsonResponse({'ok': False, 'error': TOKEN_INVALID_MESSAGE}, status=400)

    experience = payload.get('experience')
    if not isinstance(experience, str) or experience not in dict(FeedbackEntry.EXPERIENCE_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Please select your experience.'}, status=400)

    comment = payload.get('comment')
    comment = comment.strip() if isinstance(comment, str) else ''
    if not comment:
        return JsonResponse({'ok': False, 'error': 'Please write a comment.'}, status=400)
    if len(comment) > 1000:
        return JsonResponse({'ok': False, 'error': 'Comments must be 1000 characters or fewer.'}, status=400)

    topics = payload.get('topics')
    known_topics = dict(FeedbackEntry.TOPIC_CHOICES)
    if not isinstance(topics, list) or not topics:
        return JsonResponse({'ok': False, 'error': 'Please select at least one topic.'}, status=400)
    if not all(isinstance(t, str) and t in known_topics for t in topics):
        return JsonResponse({'ok': False, 'error': 'Unknown topic selected.'}, status=400)
    topics = list(dict.fromkeys(topics))  # drop duplicates, keep order

    comment_sentiment = analyze_comment_sentiment(comment) if FeedbackConfiguration.get_solo().auto_analysis_enabled else FeedbackEntry.PENDING
    sentiment = combine_sentiment(experience, comment_sentiment)

    with transaction.atomic():
        # Claim the link in one UPDATE so two submits of the same link cannot both pass.
        if not FeedbackToken.usable().filter(key=key).update(used_at=timezone.now()):
            return JsonResponse({'ok': False, 'error': TOKEN_INVALID_MESSAGE}, status=410)
        token = FeedbackToken.objects.get(key=key)
        FeedbackEntry.objects.create(
            token=token,
            ticket_number=token.ticket_number,
            experience=experience,
            comment=comment,
            topics=topics,
            comment_sentiment=comment_sentiment,
            sentiment=sentiment,
        )
    return JsonResponse({'ok': True}, status=201)
