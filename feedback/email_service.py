import os
import logging
from datetime import datetime, time, timedelta
from django.utils import timezone
from django.db.models import Q, Count
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

from .models import FeedbackEntry, FeedbackConfiguration

logger = logging.getLogger(__name__)


def get_daily_summary_metrics(target_date=None):
    """
    Computes daily feedback volume, satisfaction metrics, sentiment distribution,
    and flagged items for a given date in the local timezone.
    """
    if target_date is None:
        target_date = timezone.localtime().date()
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()

    # Time boundaries in local timezone
    start_dt = timezone.make_aware(datetime.combine(target_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(target_date, time.max))

    # Yesterday's boundaries for trend analysis
    yesterday = target_date - timedelta(days=1)
    yest_start = timezone.make_aware(datetime.combine(yesterday, time.min))
    yest_end = timezone.make_aware(datetime.combine(yesterday, time.max))

    qs = FeedbackEntry.objects.filter(created_at__range=(start_dt, end_dt))
    yest_qs = FeedbackEntry.objects.filter(created_at__range=(yest_start, yest_end))

    total_count = qs.count()

    # SQD0 Overall Satisfaction counts:
    # 5: Strongly Agree, 4: Agree, 3: Neither, 2: Disagree, 1: Strongly Disagree
    sqd0_res = qs.aggregate(
        sa=Count('id', filter=Q(sqd0=5)),
        a=Count('id', filter=Q(sqd0=4)),
        neither=Count('id', filter=Q(sqd0=3)),
        d=Count('id', filter=Q(sqd0=2)),
        sd=Count('id', filter=Q(sqd0=1)),
    )
    sa = sqd0_res['sa'] or 0
    a = sqd0_res['a'] or 0
    neither = sqd0_res['neither'] or 0
    d = sqd0_res['d'] or 0
    sd = sqd0_res['sd'] or 0

    satisfaction_rate = round(((sa + a) / total_count) * 100) if total_count > 0 else 0

    # Yesterday satisfaction rate
    yest_total = yest_qs.count()
    yest_sqd = yest_qs.aggregate(
        sa=Count('id', filter=Q(sqd0=5)),
        a=Count('id', filter=Q(sqd0=4))
    )
    yest_sat = round(((yest_sqd['sa'] or 0) + (yest_sqd['a'] or 0)) / yest_total * 100) if yest_total > 0 else 0

    trend_diff = satisfaction_rate - yest_sat if (total_count > 0 and yest_total > 0) else None

    # Sentiments
    sent_res = qs.aggregate(
        pos=Count('id', filter=Q(sentiment=FeedbackEntry.POSITIVE)),
        neu=Count('id', filter=Q(sentiment=FeedbackEntry.NEUTRAL)),
        neg=Count('id', filter=Q(sentiment=FeedbackEntry.NEGATIVE)),
        pending=Count('id', filter=Q(sentiment=FeedbackEntry.PENDING)),
    )
    pos_count = sent_res['pos'] or 0
    neu_count = sent_res['neu'] or 0
    neg_count = sent_res['neg'] or 0
    pending_count = sent_res['pending'] or 0

    pos_pct = round((pos_count / total_count) * 100) if total_count > 0 else 0
    neu_pct = round((neu_count / total_count) * 100) if total_count > 0 else 0
    neg_pct = round((neg_count / total_count) * 100) if total_count > 0 else 0

    # Categories
    cat_res = qs.aggregate(
        compliments=Count('id', filter=Q(category=FeedbackEntry.COMPLIMENT)),
        suggestions=Count('id', filter=Q(category=FeedbackEntry.SUGGESTION)),
        complaints=Count('id', filter=Q(category=FeedbackEntry.COMPLAINT)),
        concerns=Count('id', filter=Q(category=FeedbackEntry.CONCERN)),
    )

    # Flagged submissions requiring supervisor review
    # Criteria: Negative sentiment, or Complaint category, or Disagree/Strongly Disagree on SQD0
    flagged_qs = qs.filter(
        Q(sentiment=FeedbackEntry.NEGATIVE) |
        Q(category=FeedbackEntry.COMPLAINT) |
        Q(sqd0__in=[1, 2])
    ).order_by('-created_at')[:5]

    flagged_items = []
    for entry in flagged_qs:
        comment_text = (
            entry.comment or
            entry.comments_suggestions or
            entry.commendation or
            'No additional written remarks provided.'
        ).strip()
        if len(comment_text) > 160:
            comment_text = comment_text[:157] + '...'

        flagged_items.append({
            'id': entry.pk,
            'time': timezone.localtime(entry.created_at).strftime('%I:%M %p'),
            'client': entry.name_of_client or entry.client_type or 'Anonymous Citizen',
            'client_type': entry.client_type or 'General Public',
            'category': entry.get_category_display() if entry.category else 'Uncategorized',
            'sentiment': entry.get_sentiment_display(),
            'sqd0_score': entry.sqd0,
            'comment': comment_text,
            'status': entry.get_status_display(),
        })

    return {
        'target_date': target_date,
        'date_str': target_date.strftime('%A, %B %d, %Y'),
        'short_date': target_date.strftime('%Y-%m-%d'),
        'total_count': total_count,
        'satisfaction_rate': satisfaction_rate,
        'trend_diff': trend_diff,
        'yest_total': yest_total,
        'sa_count': sa,
        'a_count': a,
        'neither_count': neither,
        'd_count': d,
        'sd_count': sd,
        'pos_count': pos_count,
        'neu_count': neu_count,
        'neg_count': neg_count,
        'pending_count': pending_count,
        'pos_pct': pos_pct,
        'neu_pct': neu_pct,
        'neg_pct': neg_pct,
        'categories': {
            'compliments': cat_res['compliments'] or 0,
            'suggestions': cat_res['suggestions'] or 0,
            'complaints': cat_res['complaints'] or 0,
            'concerns': cat_res['concerns'] or 0,
        },
        'flagged_items': flagged_items,
        'flagged_count': len(flagged_items),
        'total_flagged_count': qs.filter(
            Q(sentiment=FeedbackEntry.NEGATIVE) |
            Q(category=FeedbackEntry.COMPLAINT) |
            Q(sqd0__in=[1, 2])
        ).count(),
        'additional_flagged_count': max(0, qs.filter(
            Q(sentiment=FeedbackEntry.NEGATIVE) |
            Q(category=FeedbackEntry.COMPLAINT) |
            Q(sqd0__in=[1, 2])
        ).count() - len(flagged_items)),
    }


def send_daily_summary_email(target_date=None, recipient_email=None, force=False, is_test=False, base_url=None):
    """
    Dispatches the official daily feedback summary email.
    Supports localhost, Vercel preview/production deployments, and custom domains.
    """
    config = FeedbackConfiguration.get_solo()

    if not force and not is_test and not config.daily_summary_enabled:
        return {
            'ok': False,
            'reason': 'disabled',
            'message': 'Daily summary emails are disabled in Admin Settings.'
        }

    metrics = get_daily_summary_metrics(target_date)

    # Resolve recipient
    recipient = (recipient_email or config.notification_email or '').strip()
    if not recipient:
        return {
            'ok': False,
            'reason': 'no_recipient',
            'message': 'No notification email address configured. Please set an address in Admin Settings.'
        }

    # Dynamically resolve base_url across environments (Vercel, custom domain, or local development)
    if not base_url or '127.0.0.1' in base_url or 'localhost' in base_url:
        env_base = os.environ.get('BASE_URL') or os.environ.get('SITE_URL')
        if env_base:
            base_url = env_base.rstrip('/')
        elif os.environ.get('VERCEL_URL'):
            base_url = f"https://{os.environ['VERCEL_URL'].rstrip('/')}"
        elif not base_url:
            base_url = 'http://127.0.0.1:8000'

    resolved_base_url = (base_url or 'http://127.0.0.1:8000').rstrip('/')

    context = {
        'metrics': metrics,
        'is_test': is_test,
        'base_url': resolved_base_url,
        'dashboard_url': f"{resolved_base_url}/dashboard/",
        'responses_url': f"{resolved_base_url}/dashboard/responses/?date={metrics['short_date']}",
    }

    # Render HTML and Text templates
    html_content = render_to_string('emails/daily_summary.html', context)
    text_content = render_to_string('emails/daily_summary.txt', context)

    subject_prefix = '[TEST] ' if is_test else ''
    subject = f"{subject_prefix}[PhilHealth LHIO Cauayan] Daily Feedback Summary — {metrics['date_str']} ({metrics['total_count']} responses · {metrics['satisfaction_rate']}% satisfaction)"
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'PhilHealth LHIO Cauayan <noreply@philhealth.gov.ph>')

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[recipient],
    )
    msg.attach_alternative(html_content, 'text/html')

    try:
        msg.send()
    except Exception as exc:
        logger.exception("Failed to deliver daily summary email to %s: %s", recipient, exc)
        return {
            'ok': False,
            'reason': 'smtp_error',
            'recipient': recipient,
            'metrics': metrics,
            'message': f"Failed to deliver email to {recipient}: {str(exc)}"
        }

    return {
        'ok': True,
        'recipient': recipient,
        'metrics': metrics,
        'message': f"Daily summary email successfully dispatched to {recipient}."
    }

