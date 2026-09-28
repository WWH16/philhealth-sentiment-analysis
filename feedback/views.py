import json

from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import FeedbackConfiguration, FeedbackEntry
from .services import analyze_comment_sentiment


@ensure_csrf_cookie
def page(request, template):
    config = FeedbackConfiguration.get_solo()
    return render(request, template, {
        'survey_enabled': config.survey_enabled,
        'offline_message': config.get_survey_offline_message(),
    })


@require_POST
def submit_feedback(request):
    if not FeedbackConfiguration.survey_is_enabled():
        return JsonResponse({
            'ok': False,
            'error': FeedbackConfiguration.get_survey_offline_message(),
            'survey_disabled': True,
        }, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    experience = payload.get('experience')
    if not isinstance(experience, str) or experience not in dict(FeedbackEntry.EXPERIENCE_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Please select your experience.'}, status=400)

    comment = payload.get('comment')
    comment = comment.strip() if isinstance(comment, str) else ''
    if len(comment) > 1000:
        return JsonResponse({'ok': False, 'error': 'Comments must be 1000 characters or fewer.'}, status=400)

    if not comment:
        sentiment = FeedbackEntry.NOT_APPLICABLE
    elif FeedbackConfiguration.auto_analysis_is_enabled():
        sentiment = analyze_comment_sentiment(comment)
    else:
        sentiment = FeedbackEntry.PENDING

    FeedbackEntry.objects.create(experience=experience, comment=comment, sentiment=sentiment)
    return JsonResponse({'ok': True}, status=201)
