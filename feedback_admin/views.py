import os
import json
import re
from datetime import datetime, time, timedelta
from collections import defaultdict
from functools import wraps


from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.hashers import make_password
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.db.models.functions import ExtractHour, TruncDate, TruncMonth, TruncWeek
import segno

from feedback.models import FeedbackConfiguration, FeedbackEntry, FeedbackToken
from feedback.email_service import send_daily_summary_email

from django.http import FileResponse, Http404
from django.core.exceptions import SuspiciousFileOperation

from feedback_admin.backup_utils import (
    create_backup, list_backups, resolve_backup_path, delete_backup, restore_backup,
)

# ── Activity log helpers (built on Django's built-in django_admin_log table) ──

def _feedback_content_type():
    return ContentType.objects.get_for_model(FeedbackEntry)


def log_admin_event(user, obj, action_flag, change_message):
    """Writes a generic built-in admin log entry for any tracked object."""
    LogEntry.objects.log_actions(
        user_id=user.id,
        queryset=[obj],
        action_flag=action_flag,
        change_message=change_message,
        single_object=True,
    )


def _build_feedback_activity_map(entry_ids):
    """Builds feedback activity for many entries in one query."""
    if not entry_ids:
        return {}

    logs = (LogEntry.objects
            .filter(content_type=_feedback_content_type(), object_id__in=[str(pk) for pk in entry_ids])
            .select_related('user')
            .order_by('object_id', 'action_time'))

    status_display = dict(FeedbackEntry.STATUS_CHOICES)
    activity = {}

    for log in logs:
        if not log.object_id or not log.object_id.isdigit():
            continue

        entry_id = int(log.object_id)
        bucket = activity.setdefault(entry_id, {'notes': [], 'status_history': []})
        author = (log.user.get_full_name() or log.user.username) if log.user else 'System'
        at = timezone.localtime(log.action_time).strftime('%b %d, %Y %I:%M %p')

        if log.action_flag == CHANGE and '|' in log.change_message:
            old_raw, _, new_raw = log.change_message.partition('|')
            bucket['status_history'].append({
                'old': status_display.get(old_raw, old_raw),
                'new': status_display.get(new_raw, new_raw),
                'by': author,
                'at': at,
            })
        else:
            bucket['notes'].append({
                'author': author,
                'body': log.change_message,
                'created_at': at,
            })

    return activity


_GROUP_ACTIONS = {
    ADDITION: ('Group Created', 'create'),
    CHANGE: ('Group Updated', 'update'),
    DELETION: ('Group Deleted', 'delete'),
}
# Settings-page events, matched by the start of the log message.
_SETTINGS_EVENTS = [
    ('Auto-analysis', 'Auto-Analysis Toggle', 'settings'),
    ('Batch re-analysis', 'Batch Re-analyze', 'settings'),
    ('Created backup', 'Backup Created', 'backup'),
    ('Deleted backup', 'Backup Deleted', 'backup'),
    ('Restored database', 'Database Restored', 'backup'),
]


def _format_audit_row(log, entry=None):
    local_time = timezone.localtime(log.action_time)
    username = log.user.username if log.user else 'System'
    full_name = log.user.get_full_name() if log.user else ''
    ctype = log.content_type.model if log.content_type_id else ''
    message = log.change_message or ''
    action_label = 'Event'
    summary = message
    action_type = 'event'
    search_parts = [
        username,
        full_name,
        message,
        ctype,
        log.object_repr or '',
        f'Feedback #{entry.pk}' if entry else '',
        getattr(entry, 'username', ''),
        getattr(entry, 'name', ''),
    ]
    old_status = ''
    new_status = ''

    if ctype == 'feedbackentry':
        label = f"Feedback #{entry.pk}" if entry else log.object_repr
        if log.action_flag == CHANGE and '|' in message:
            old_raw, _, new_raw = message.partition('|')
            action_label = 'Status Update'
            action_type = 'status'
            old_status = dict(FeedbackEntry.STATUS_CHOICES).get(old_raw, old_raw)
            new_status = dict(FeedbackEntry.STATUS_CHOICES).get(new_raw, new_raw)
            summary = f"{label}: {old_status} -> {new_status}"
        elif log.action_flag == CHANGE and message.startswith('category:'):
            _, _, payload = message.partition(':')
            old_raw, _, new_raw = payload.partition('|')
            action_label = 'Category Update'
            action_type = 'category'
            summary = f"{label}: {dict(FeedbackEntry.CATEGORY_CHOICES).get(old_raw, old_raw or 'Uncategorized')} -> {dict(FeedbackEntry.CATEGORY_CHOICES).get(new_raw, new_raw or 'Uncategorized')}"
        elif log.action_flag == DELETION:
            action_label = 'Feedback Deleted'
            action_type = 'delete'
            summary = f"{label} deleted"
        elif log.action_flag == ADDITION:
            action_label = 'Note / Reply'
            action_type = 'note'
            summary = f"{label}: {message}"
        else:
            summary = f"{label}: {message}"
    elif ctype == 'user':
        if log.action_flag == ADDITION and message == 'Logged in':
            action_label = 'Login'
            action_type = 'login'
            summary = f"{log.object_repr} logged in"
        elif log.action_flag == CHANGE and message == 'Logged out':
            action_label = 'Logout'
            action_type = 'logout'
            summary = f"{log.object_repr} logged out"
        elif log.action_flag == ADDITION:
            action_label = 'User Created'
            action_type = 'create'
            summary = message or f'{log.object_repr} created'
        elif log.action_flag == CHANGE and message == 'Password updated':
            action_label = 'Password Updated'
            action_type = 'profile'
            summary = f"{log.object_repr}: password updated"
        elif log.action_flag == CHANGE:
            action_label = 'Profile Updated'
            action_type = 'profile'
            summary = message or f'{log.object_repr} updated'
        elif log.action_flag == DELETION:
            action_label = 'User Deleted'
            action_type = 'delete'
            summary = message or f'{log.object_repr} deleted'
    elif ctype == 'group':
        action_label, action_type = _GROUP_ACTIONS.get(log.action_flag, (action_label, action_type))
        summary = message or log.object_repr
    elif ctype == 'feedbackconfiguration':
        action_label, action_type = next(
            ((label, kind) for prefix, label, kind in _SETTINGS_EVENTS if message.startswith(prefix)),
            ('Settings Update', 'settings'),
        )
        summary = message

    return {
        'id': log.pk,
        'date': local_time.strftime('%Y-%m-%d'),
        'time': local_time.strftime('%H:%M'),
        'admin': username,
        'username': username,
        'full_name': full_name,
        'action_type': action_type,
        'action_label': action_label,
        'summary': summary,
        'old_status': old_status,
        'new_status': new_status,
        'search_text': ' '.join(str(part) for part in search_parts if part),
    }


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def staff_required(view_func):
    """Requires user to be authenticated and have staff or superuser status."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('admin_login')
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, 'Access denied. Staff privileges are required.')
            return redirect('admin_login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def superuser_required(view_func):
    """Requires user to be authenticated and have superuser status."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('admin_login')
        if not request.user.is_superuser:
            if _is_ajax(request):
                return JsonResponse({'ok': False, 'error': 'Access restricted to administrators.'}, status=403)
            messages.error(request, 'Access restricted to administrators.')
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


@staff_required
@require_POST
def response_note_add(request, entry_id):
    entry = get_object_or_404(FeedbackEntry, pk=entry_id)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    body = (payload.get('body') or '').strip()
    if not body:
        return JsonResponse({'ok': False, 'error': 'Note body cannot be empty.'}, status=400)

    log_admin_event(request.user, entry, ADDITION, body)

    return JsonResponse({
        'ok': True,
        'note': {
            'author': request.user.get_full_name() or request.user.username,
            'body': body,
            'created_at': timezone.localtime(timezone.now()).strftime('%b %d, %Y %I:%M %p'),
        }
    })


@staff_required
def dashboard(request):
    """Comment sentiment first; the client's rating is shown as secondary."""
    entries = FeedbackEntry.objects.all()
    now = timezone.localtime(timezone.now())
    today = now.date()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    today_range = (
        timezone.make_aware(datetime.combine(today, time.min)),
        timezone.make_aware(datetime.combine(today, time.max)),
    )

    filter_data = _multi_period_sentiment_counts(entries, today_range, week_start, month_start)
    all_counts = filter_data['all']

    trend_counts = (
        entries.exclude(sentiment=FeedbackEntry.PENDING)
        .annotate(local_date=TruncDate('created_at'))
        .values('local_date', 'sentiment')
        .annotate(count=Count('id'))
    )
    trend_data_map = defaultdict(lambda: defaultdict(int))
    for row in trend_counts:
        if row['local_date']:
            trend_data_map[row['local_date']][row['sentiment']] = row['count']

    if trend_data_map:
        start_date = min(min(trend_data_map.keys()), today - timedelta(days=6))
        trend_dates = [start_date + timedelta(days=i) for i in range((today - start_date).days + 1)]
    else:
        trend_dates = [today - timedelta(days=i) for i in range(6, -1, -1)]

    def trend_for(sentiment):
        return [trend_data_map[day][sentiment] for day in trend_dates]

    recent_entries = list(entries.order_by('-created_at')[:5])
    recent_activity = _build_feedback_activity_map([entry.pk for entry in recent_entries])

    context = {
        'total': all_counts['total'],
        'positive': all_counts['positive'],
        'neutral': all_counts['neutral'],
        'negative': all_counts['negative'],
        'positive_pct': all_counts['positive_pct'],
        'neutral_pct': all_counts['neutral_pct'],
        'negative_pct': all_counts['negative_pct'],
        'filter_data': filter_data,
        'rating_data': _multi_period_experience_counts(entries, today_range, week_start, month_start),
        'topic_data': {
            'all': topic_counts(entries),
            'today': topic_counts(entries.filter(created_at__range=today_range)),
            'week': topic_counts(entries.filter(created_at__gte=week_start)),
            'month': topic_counts(entries.filter(created_at__gte=month_start)),
        },
        'needs_attention': _open_negative_comments(entries, today_range, week_start, month_start),
        'word_cloud': _cached_word_cloud(entries, today_range[0], week_start, month_start),
        'recent_entries_data': [_entry_to_row(entry, recent_activity) for entry in recent_entries],
        'trend_labels': [f'{day:%b} {day.day}' for day in trend_dates],
        'trend_dates': [day.isoformat() for day in trend_dates],
        'trend_positive': trend_for(FeedbackEntry.POSITIVE),
        'trend_neutral': trend_for(FeedbackEntry.NEUTRAL),
        'trend_negative': trend_for(FeedbackEntry.NEGATIVE),
    }
    return render(request, 'feedback_admin/dashboard.html', context)


@staff_required
def responses(request):
    entries = FeedbackEntry.objects.order_by('-created_at')
    entries_data = list(entries)
    activity_map = _build_feedback_activity_map([entry.pk for entry in entries_data])

    context = {
        **_experience_counts(entries),
        'entries_data': [_entry_to_row(entry, activity_map) for entry in entries_data],
        'topic_choices': FeedbackEntry.TOPIC_CHOICES,
    }
    return render(request, 'feedback_admin/responses.html', context)


@staff_required
def client_qr(request):
    """Staff issue a single-use feedback QR for the client they just served."""
    if request.method == 'POST':
        ticket = request.POST.get('ticket_number', '').strip()
        if not re.fullmatch(r'[0-9]{1,6}', ticket):
            messages.error(request, 'Enter the ticket number using digits only (up to 6).')
            return redirect('client_qr')
        token = FeedbackToken.issue(str(int(ticket)), request.user)
        return redirect(f"{reverse('client_qr')}?k={token.key}")

    context = {
        'lifetime_minutes': int(FeedbackToken.LIFETIME.total_seconds() // 60),
        # A QR pointing at localhost only opens on this PC, never on the client's phone.
        'local_only': request.get_host().split(':')[0] in ('127.0.0.1', 'localhost'),
    }
    token = FeedbackToken.objects.filter(key=request.GET.get('k', '')).first()
    if token:
        link = request.build_absolute_uri(f"{reverse('feedback-index')}?t={token.key}")
        context.update(
            token=token,
            link=link,
            qr_svg=segno.make(link, error='m').svg_inline(scale=8, border=4, light='#fff', omitsize=True),
            expired=token.used_at is None and token.expires_at <= timezone.now(),
        )
    return render(request, 'feedback_admin/client_qr.html', context)


_EXP_DISPLAY = dict(FeedbackEntry.EXPERIENCE_CHOICES)
_CAT_DISPLAY = dict(FeedbackEntry.CATEGORY_CHOICES)
_STATUS_DISPLAY = dict(FeedbackEntry.STATUS_CHOICES)
_SENT_DISPLAY = dict(FeedbackEntry.SENTIMENT_CHOICES)
_TOPIC_DISPLAY = dict(FeedbackEntry.TOPIC_CHOICES)


def _entry_to_row(entry, activity=None):
    local_created = timezone.localtime(entry.created_at)
    if activity is None:
        activity = _build_feedback_activity_map([entry.pk])
    entry_activity = activity.get(entry.pk, {'notes': [], 'status_history': []})
    notes = entry_activity['notes']
    status_history = entry_activity['status_history']
    has_comment = bool(entry.comment and entry.comment.strip())
    if not has_comment or entry.sentiment == FeedbackEntry.NOT_APPLICABLE:
        sentiment_display = 'N/A'
        sentiment_value = FeedbackEntry.NOT_APPLICABLE
    else:
        sentiment_display = _SENT_DISPLAY.get(entry.sentiment, entry.sentiment)
        sentiment_value = entry.sentiment

    exp_display = _EXP_DISPLAY.get(entry.experience, entry.experience)
    return {
        'id': entry.id,
        'ticket': entry.ticket_number,
        'date': local_created.strftime('%Y-%m-%d'),
        'time': local_created.strftime('%H:%M'),
        'rating': exp_display,
        'category': _CAT_DISPLAY.get(entry.category, entry.category),
        'category_value': entry.category,
        'topics': [_TOPIC_DISPLAY.get(t, t) for t in entry.topics or []],
        'topic_values': list(entry.topics or []),
        'sentiment': sentiment_display,
        'sentiment_value': sentiment_value,
        # What the comment alone read as, for the CSV export.
        'comment_sentiment_label': (
            _SENT_DISPLAY.get(entry.comment_sentiment, entry.comment_sentiment) if has_comment else 'N/A'
        ),
        # Shown only when the rating changed what the comment alone read as.
        'comment_sentiment': (
            _SENT_DISPLAY.get(entry.comment_sentiment, entry.comment_sentiment)
            if has_comment and entry.comment_sentiment != entry.sentiment else None
        ),
        'status': _STATUS_DISPLAY.get(entry.status, entry.status),
        'status_value': entry.status,
        'comment': entry.comment,
        'notes': notes,
        'status_history': status_history,
    }


def _experience_counts(qs):
    res = qs.aggregate(
        total=Count('id'),
        vs=Count('id', filter=Q(experience=FeedbackEntry.VERY_SATISFACTORY)),
        sat=Count('id', filter=Q(experience=FeedbackEntry.SATISFACTORY)),
        unsat=Count('id', filter=Q(experience=FeedbackEntry.UNSATISFACTORY)),
    )
    return {
        'total': res['total'] or 0,
        'very_satisfactory': res['vs'] or 0,
        'satisfactory': res['sat'] or 0,
        'unsatisfactory': res['unsat'] or 0,
    }


def _multi_period_experience_counts(qs, today_range, week_start, month_start):
    """Consolidates experience counts across all 4 timeframes into a single DB query."""
    def _cond(period_q, exp_q):
        return Count('id', filter=(period_q & exp_q) if period_q is not None else exp_q)

    def _period_aggs(prefix, period_q):
        tot_q = Count('id', filter=period_q) if period_q is not None else Count('id')
        return {
            f'{prefix}_total': tot_q,
            f'{prefix}_vs': _cond(period_q, Q(experience=FeedbackEntry.VERY_SATISFACTORY)),
            f'{prefix}_sat': _cond(period_q, Q(experience=FeedbackEntry.SATISFACTORY)),
            f'{prefix}_unsat': _cond(period_q, Q(experience=FeedbackEntry.UNSATISFACTORY)),
        }

    aggs = {}
    aggs.update(_period_aggs('all', None))
    aggs.update(_period_aggs('today', Q(created_at__range=today_range)))
    aggs.update(_period_aggs('week', Q(created_at__gte=week_start)))
    aggs.update(_period_aggs('month', Q(created_at__gte=month_start)))

    res = qs.aggregate(**aggs)

    def _extract(prefix):
        return {
            'total': res[f'{prefix}_total'] or 0,
            'very_satisfactory': res[f'{prefix}_vs'] or 0,
            'satisfactory': res[f'{prefix}_sat'] or 0,
            'unsatisfactory': res[f'{prefix}_unsat'] or 0,
        }

    return {
        'all': _extract('all'),
        'today': _extract('today'),
        'week': _extract('week'),
        'month': _extract('month'),
    }


def _open_negative_comments(qs, today_range, week_start, month_start, limit=5):
    """Negative comments not yet marked resolved, per period: count plus the newest few."""
    open_negative = qs.filter(sentiment=FeedbackEntry.NEGATIVE).exclude(status='resolved')
    periods = {
        'all': Q(),
        'today': Q(created_at__range=today_range),
        'week': Q(created_at__gte=week_start),
        'month': Q(created_at__gte=month_start),
    }
    result = {}
    for name, period_q in periods.items():
        period_qs = open_negative.filter(period_q)
        items = []
        for entry in period_qs.order_by('-created_at')[:limit]:
            local = timezone.localtime(entry.created_at)
            items.append({
                'date': f'{local:%b} {local.day}, {local:%Y}',
                'time': local.strftime('%H:%M'),
                'rating': entry.get_experience_display(),
                'status': entry.get_status_display(),
                'comment': entry.comment,
            })
        result[name] = {'count': period_qs.count(), 'items': items}
    return result


def _multi_period_sentiment_counts(qs, today_range, week_start, month_start):
    """Consolidates sentiment counts across all 4 timeframes into a single DB query."""
    def _cond(period_q, sent_val):
        sent_q = Q(sentiment=sent_val)
        return Count('id', filter=(period_q & sent_q) if period_q is not None else sent_q)

    def _period_aggs(prefix, period_q):
        return {
            f'{prefix}_pos': _cond(period_q, FeedbackEntry.POSITIVE),
            f'{prefix}_neu': _cond(period_q, FeedbackEntry.NEUTRAL),
            f'{prefix}_neg': _cond(period_q, FeedbackEntry.NEGATIVE),
        }

    aggs = {}
    aggs.update(_period_aggs('all', None))
    aggs.update(_period_aggs('today', Q(created_at__range=today_range)))
    aggs.update(_period_aggs('week', Q(created_at__gte=week_start)))
    aggs.update(_period_aggs('month', Q(created_at__gte=month_start)))

    res = qs.aggregate(**aggs)

    def _format(prefix):
        pos = res[f'{prefix}_pos'] or 0
        neu = res[f'{prefix}_neu'] or 0
        neg = res[f'{prefix}_neg'] or 0
        tot = pos + neu + neg
        def _pct(n):
            return round((n / tot) * 100) if tot else 0
        return {
            'total': tot,
            'positive': pos,
            'neutral': neu,
            'negative': neg,
            'positive_pct': _pct(pos),
            'neutral_pct': _pct(neu),
            'negative_pct': _pct(neg),
        }

    return {
        'all': _format('all'),
        'today': _format('today'),
        'week': _format('week'),
        'month': _format('month'),
    }


@staff_required
@require_POST
def response_status_update(request, entry_id):
    entry = get_object_or_404(FeedbackEntry, pk=entry_id)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    status = payload.get('status')
    if status not in _STATUS_DISPLAY:
        return JsonResponse({'ok': False, 'error': 'Invalid status.'}, status=400)

    old_status = entry.status
    history_entry = None

    if status != old_status:
        entry.status = status
        entry.save(update_fields=['status', 'updated_at'])
        log_admin_event(request.user, entry, CHANGE, f'{old_status}|{status}')
        history_entry = {
            'old': _STATUS_DISPLAY.get(old_status, old_status),
            'new': _STATUS_DISPLAY.get(status, status),
            'by': request.user.get_full_name() or request.user.username,
            'at': timezone.localtime(timezone.now()).strftime('%b %d, %Y %I:%M %p'),
        }

    return JsonResponse({
        'ok': True,
        'status': entry.get_status_display(),
        'status_value': entry.status,
        'history_entry': history_entry,
    })


@staff_required
@require_POST
def response_category_update(request, entry_id):
    entry = get_object_or_404(FeedbackEntry, pk=entry_id)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    category = payload.get('category')
    valid_categories = {choice[0] for choice in FeedbackEntry.CATEGORY_CHOICES}
    if category and category not in valid_categories:
        return JsonResponse({'ok': False, 'error': 'Invalid category.'}, status=400)

    old_category = entry.category
    entry.category = category or ''
    if entry.category != old_category:
        entry.save(update_fields=['category', 'updated_at'])
        log_admin_event(
            request.user,
            entry,
            CHANGE,
            f'category:{old_category or ""}|{entry.category or ""}',
        )

    return JsonResponse({
        'ok': True,
        'category': entry.get_category_display(),
        'category_value': entry.category,
    })


@staff_required
def responses_count(request):
    return JsonResponse({
        'ok': True,
        'count': FeedbackEntry.objects.count(),
    })


@staff_required
@require_POST
def responses_delete(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid request payload.'}, status=400)

    raw_ids = payload.get('ids', [])
    if not isinstance(raw_ids, list) or not raw_ids:
        return JsonResponse({'ok': False, 'error': 'No response IDs provided.'}, status=400)

    clean_ids = []
    for item in raw_ids:
        try:
            clean_ids.append(int(item))
        except (ValueError, TypeError):
            continue

    if not clean_ids:
        return JsonResponse({'ok': False, 'error': 'No valid response IDs provided.'}, status=400)

    entries = list(FeedbackEntry.objects.filter(id__in=clean_ids))
    if not entries:
        return JsonResponse({'ok': False, 'error': 'Selected responses not found or already deleted.'}, status=404)

    deleted_count = len(entries)
    deleted_ids = [entry.id for entry in entries]

    with transaction.atomic():
        for entry in entries:
            log_admin_event(
                request.user,
                entry,
                DELETION,
                f'Deleted feedback response #{entry.id}',
            )
        FeedbackEntry.objects.filter(id__in=deleted_ids).delete()

    counts = _experience_counts(FeedbackEntry.objects.all())
    noun = 'response' if deleted_count == 1 else 'responses'

    return JsonResponse({
        'ok': True,
        'message': f'Successfully deleted {deleted_count} {noun}.',
        'deleted_ids': deleted_ids,
        'deleted_count': deleted_count,
        'counts': counts,
    })


# Filipino and Taglish function words, plus form and agency words that appear
# in almost every comment and would crowd out the words people actually chose.
# The last line adds the negation/intensity words the model keeps on purpose.
_WORD_CLOUD_EXTRA_STOPWORDS = frozenset('''
    ang ng sa na mga at si ni kay ay po opo ko ako ikaw ka mo niya siya kami
    kayo sila namin natin nila ito iyan iyon yan yun dito diyan doon nga pa din
    rin lang lamang naman kasi dahil para pag kung kapag kahit pero ngunit may
    mayroon meron wala hindi di ba nang nung noong nasa yung iyong aking ating
    kanilang lahat isang isa dapat sana talaga nag mag kaya hanggang
    comments commendation suggestions philhealth lhio cauayan office also
    would could get got one
    not no nor never very too but cannot nothing none against
'''.split())
_WORD_RE = re.compile(r"[a-zñ]+(?:'[a-z]+)?")
_WORD_CLOUD_LIMIT = 60


def _cached_word_cloud(entries, today_start, week_start, month_start):
    """Word cloud reuse: the key changes whenever an entry is added, edited,
    or deleted, or the day rolls over, so a cached cloud is never stale."""
    stamp = entries.aggregate(n=Count('id'), last=Max('updated_at'))
    key = f"dashboard-word-cloud:{stamp['n']}:{stamp['last']}:{today_start.date()}"
    return cache.get_or_set(
        key, lambda: _sentiment_word_cloud(entries, today_start, week_start, month_start), 600,
    )


def _sentiment_word_cloud(entries, today_start, week_start, month_start):
    """Most frequent comment words per period, with the sentiment of the
    comments each word came from. Only analyzed comments (positive, neutral,
    negative) are counted, so every word has a sentiment to show. Words take
    the comment's own sentiment, not the rating-adjusted one."""
    from feedback.services import _STOP_WORDS

    stopwords = _STOP_WORDS | _WORD_CLOUD_EXTRA_STOPWORDS
    sentiment_keys = {
        FeedbackEntry.POSITIVE: 'pos',
        FeedbackEntry.NEUTRAL: 'neu',
        FeedbackEntry.NEGATIVE: 'neg',
    }
    periods = ('all', 'month', 'week', 'today')
    words = {period: defaultdict(lambda: {'pos': 0, 'neu': 0, 'neg': 0}) for period in periods}
    comment_counts = dict.fromkeys(periods, 0)

    rows = (
        entries.filter(comment_sentiment__in=sentiment_keys.keys())
        .exclude(comment='')
        .values_list('comment', 'comment_sentiment', 'created_at')
    )
    for comment, sentiment, created_at in rows.iterator(chunk_size=500):
        text = re.sub(r'(Comments|Commendation|Comments & Suggestions):', ' ', comment, flags=re.IGNORECASE)
        # Count each word once per comment so one long, repetitive comment cannot dominate.
        tokens = {
            token for token in _WORD_RE.findall(text.lower())
            if len(token) > 2 and token not in stopwords
        }
        if not tokens:
            continue
        key = sentiment_keys[sentiment]
        in_periods = ['all']
        if created_at >= month_start:
            in_periods.append('month')
        if created_at >= week_start:
            in_periods.append('week')
        if created_at >= today_start:
            in_periods.append('today')
        for period in in_periods:
            comment_counts[period] += 1
            bucket = words[period]
            for token in tokens:
                bucket[token][key] += 1

    cloud = {}
    for period in periods:
        entries_list = [
            {'t': token, **counts, 'n': sum(counts.values())}
            for token, counts in words[period].items()
        ]
        # Keep the top words overall and the top words of each sentiment, so
        # the Positive/Neutral/Negative views are not limited to overall leaders.
        kept = {}
        for key in ('n', 'pos', 'neu', 'neg'):
            ranked = sorted(
                (word for word in entries_list if word[key] > 0),
                key=lambda word: (-word[key], word['t']),
            )
            for word in ranked[:_WORD_CLOUD_LIMIT]:
                kept[word['t']] = word
        cloud[period] = {
            'comments': comment_counts[period],
            'words': sorted(kept.values(), key=lambda word: (-word['n'], word['t'])),
        }
    return cloud


# Report tabs, in order: range key and tab label.
REPORT_TABS = [('today', 'Daily'), ('week', 'Weekly'), ('month', 'Monthly'),
               ('quarter', 'Quarterly'), ('year', 'Annual')]


def _report_ranges(now):
    """Label and start of each report period. Shared by the Reports page,
    its Excel export, and the Responses page date filter keys."""
    today_start = timezone.make_aware(datetime.combine(now.date(), time.min))
    month_start = today_start.replace(day=1)
    return {
        'today': ('Today', today_start),
        'week': ('Last 7 Days', now - timedelta(days=7)),
        'month': ('This Month', month_start),
        'quarter': ('This Quarter', month_start.replace(month=(month_start.month - 1) // 3 * 3 + 1)),
        'year': ('This Year', month_start.replace(month=1)),
        'all': ('All Time', None),
    }


def _resolve_report_period(request, now, default):
    """(key, label, start, end, month) from `?month=YYYY-MM` or `?range=`.
    A chosen month has an exclusive `end`; bad values fall back to `default`."""
    try:
        chosen = datetime.strptime(request.GET.get('month', ''), '%Y-%m').date()
    except ValueError:
        chosen = None
    if chosen and chosen <= now.date():
        start = timezone.make_aware(datetime.combine(chosen, time.min))
        end = timezone.make_aware(datetime.combine((chosen + timedelta(days=32)).replace(day=1), time.min))
        return 'month', f'{start:%B %Y}', start, end, f'{chosen:%Y-%m}'
    ranges = _report_ranges(now)
    period = request.GET.get('range', default)
    if period not in ranges:
        period = default
    label, start = ranges[period]
    return period, label, start, None, ''


def _report_queryset(start, end=None):
    qs = FeedbackEntry.objects.all()
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lt=end)
    return qs


def _report_month_options(now):
    """Every month from the first response to now, newest first."""
    first = FeedbackEntry.objects.order_by('created_at').values_list('created_at', flat=True).first()
    cursor = (timezone.localtime(first) if first else now).date().replace(day=1)
    months = []
    while cursor <= now.date():
        months.append((f'{cursor:%Y-%m}', f'{cursor:%B %Y}'))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months[::-1]


def _report_counts(qs):
    """Sentiment, rating, and category counts for one period, in one query."""
    F = FeedbackEntry
    c = qs.aggregate(
        total=Count('id'),
        **{f'exp_{v}': Count('id', filter=Q(experience=v)) for v, _ in F.EXPERIENCE_CHOICES},
        **{f'sent_{v}': Count('id', filter=Q(sentiment=v)) for v in (F.POSITIVE, F.NEUTRAL, F.NEGATIVE, F.PENDING)},
        **{f'cat_{v}': Count('id', filter=Q(category=v)) for v, _ in F.CATEGORY_CHOICES},
    )
    sentiment = {'pos': c[f'sent_{F.POSITIVE}'], 'neu': c[f'sent_{F.NEUTRAL}'], 'neg': c[f'sent_{F.NEGATIVE}']}
    analyzed = sum(sentiment.values())
    satisfied = c[f'exp_{F.VERY_SATISFACTORY}'] + c[f'exp_{F.SATISFACTORY}']
    categories = {v: c[f'cat_{v}'] for v, _ in F.CATEGORY_CHOICES}
    return {
        'total': c['total'],
        'analyzed': analyzed,
        'pending': c[f'sent_{F.PENDING}'],
        'sentiment': sentiment,
        'sentiment_pct': {k: round(n / analyzed * 100) if analyzed else 0 for k, n in sentiment.items()},
        'ratings': {v: c[f'exp_{v}'] for v, _ in F.EXPERIENCE_CHOICES},
        'satisfaction': round(satisfied / c['total'] * 100) if c['total'] else 0,
        'categories': categories,
        'categorized': sum(categories.values()),
        'topics': topic_counts(qs),
    }


def _report_trend(qs, period, start, now, end=None):
    """Sentiment counts per hour (today), day (week), week (month), or month."""
    keys = {FeedbackEntry.POSITIVE: 'pos', FeedbackEntry.NEUTRAL: 'neu', FeedbackEntry.NEGATIVE: 'neg'}
    last_day = (end - timedelta(days=1)).date() if end else now.date()

    if period == 'today':
        trunc = ExtractHour
        buckets = list(range(now.hour + 1))
        label = lambda h: datetime(2000, 1, 1, h).strftime('%I %p').lstrip('0')
        caption = 'By hour'
    elif period == 'week':
        trunc = TruncDate
        buckets = [start.date() + timedelta(days=i) for i in range((last_day - start.date()).days + 1)]
        label = lambda d: f'{d:%b} {d.day}'
        caption = 'By day'
    elif period == 'month':
        trunc = TruncWeek
        first_day = start.date()
        cursor, buckets = first_day - timedelta(days=first_day.weekday()), []
        while cursor <= last_day:
            buckets.append(cursor)
            cursor += timedelta(days=7)

        def label(d):
            a, b = max(d, first_day), min(d + timedelta(days=6), last_day)
            return f'{a:%b} {a.day}' + ('' if a == b else f' to {b:%b} {b.day}')
        caption = 'By week'
    else:
        trunc = TruncMonth
        cursor, buckets = start.date().replace(day=1), []
        while cursor <= last_day:
            buckets.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        label = lambda d: f'{d:%b %Y}'
        caption = 'By month'

    data = defaultdict(lambda: {'pos': 0, 'neu': 0, 'neg': 0})
    for row in qs.annotate(bucket=trunc('created_at')).values('bucket', 'sentiment').annotate(n=Count('id')):
        bucket = row['bucket']
        bucket = bucket.date() if isinstance(bucket, datetime) else bucket
        if row['sentiment'] in keys:
            data[bucket][keys[row['sentiment']]] += row['n']
    return {
        'caption': caption,
        'labels': [label(b) for b in buckets],
        **{key: [data[b][key] for b in buckets] for key in ('pos', 'neu', 'neg')},
    }


def _report_dates(start, end, now):
    if not start:
        return 'All responses'
    last = (end - timedelta(days=1)) if end else now
    return f'{start:%b} {start.day}, {start.year} to {last:%b} {last.day}, {last.year}'


@staff_required
def reports(request):
    """Sentiment report per period: tabs for Daily to Annual, or one chosen month."""
    now = timezone.localtime(timezone.now())
    ranges = _report_ranges(now)
    periods = {key: (tab, ranges[key][0], ranges[key][1], None, f'range={key}') for key, tab in REPORT_TABS}
    initial = 'month'
    _, month_label, month_start, month_end, month = _resolve_report_period(request, now, default='month')
    if month:
        periods['custom'] = (month_label, month_label, month_start, month_end, f'month={month}')
        initial = 'custom'

    report = {}
    for key, (tab, label, start, end, excel_query) in periods.items():
        qs = _report_queryset(start, end)
        report[key] = {
            'tab': tab,
            'label': label,
            'dates': _report_dates(start, end, now),
            'excel': excel_query,
            **_report_counts(qs),
            'trend': _report_trend(qs, 'month' if key == 'custom' else key, start, now, end),
        }
    return render(request, 'feedback_admin/reports.html', {
        'report_json': report,
        'initial_period': initial,
        'tabs': [(key, report[key]['tab']) for key in report],
        'month': month,
        'month_options': _report_month_options(now),
    })


@staff_required
def export_report_excel(request):
    """Excel file (summary + responses) for one report period or month."""
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    now = timezone.localtime(timezone.now())
    period, period_label, start, end, _ = _resolve_report_period(request, now, default='all')
    qs = _report_queryset(start, end)
    counts = _report_counts(qs)
    F = FeedbackEntry

    # ── Styles ────────────────────────────────────────────────────────────
    header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill(start_color='0F5A2B', end_color='0F5A2B', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1'),
    )
    label_font = Font(name='Calibri', bold=True, size=11)
    section_font = Font(name='Calibri', bold=True, size=11, color='0F5A2B')
    value_font = Font(name='Calibri', size=11)
    title_font = Font(name='Calibri', bold=True, size=14, color='0F5A2B')

    wb = openpyxl.Workbook()

    # ── Sheet 1: Summary ──────────────────────────────────────────────────
    ws = wb.active
    ws.title = 'Summary'
    ws.sheet_properties.tabColor = '169651'

    # Title
    ws.merge_cells('A1:B1')
    ws['A1'] = f'CSAS Feedback Report — {period_label}'
    ws['A1'].font = title_font
    ws['A1'].alignment = Alignment(vertical='center')
    ws['A2'] = f'Generated: {now.strftime("%B %d, %Y at %I:%M %p")}'
    ws['A2'].font = Font(name='Calibri', size=10, italic=True, color='475569')
    ws.append([])  # blank row

    # Overview metrics
    overview_rows = [
        ('Total Responses', counts['total']),
        ('Satisfaction Rate', f"{counts['satisfaction']}%"),
        ('', ''),
        ('Sentiment', ''),
        ('Positive', counts['sentiment']['pos']),
        ('Neutral', counts['sentiment']['neu']),
        ('Negative', counts['sentiment']['neg']),
        ('Pending analysis', counts['pending']),
        ('', ''),
        ('Rating Distribution', ''),
        *[(label, counts['ratings'][v]) for v, label in F.EXPERIENCE_CHOICES],
        ('', ''),
        ('Feedback Categories', ''),
        ('Compliments', counts['categories'][F.COMPLIMENT]),
        ('Suggestions', counts['categories'][F.SUGGESTION]),
        ('Complaints', counts['categories'][F.COMPLAINT]),
        ('Service Concerns', counts['categories'][F.CONCERN]),
        ('Total Categorized', counts['categorized']),
    ]
    for label, value in overview_rows:
        ws.append([label, value])
        row_num = ws.max_row
        ws.cell(row=row_num, column=1).font = section_font if label and value == '' else (label_font if label else value_font)
        ws.cell(row=row_num, column=2).font = value_font
        for col in (1, 2):
            ws.cell(row=row_num, column=col).border = thin_border

    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 18

    # Per topic, with the sentiment split. One entry can name several topics.
    ws.append([])
    ws.append(['Feedback by Topic'])
    ws.cell(row=ws.max_row, column=1).font = section_font
    for i, row in enumerate([('Topic', 'Total', 'Positive', 'Neutral', 'Negative'),
                             *[(t['label'], t['total'], t['pos'], t['neu'], t['neg']) for t in counts['topics']]]):
        ws.append(list(row))
        for col in range(1, 6):
            cell = ws.cell(row=ws.max_row, column=col)
            cell.font = label_font if i == 0 or col == 1 else value_font
            cell.border = thin_border
    ws.append(['One feedback can mention more than one topic.'])
    ws.cell(row=ws.max_row, column=1).font = Font(name='Calibri', size=10, italic=True, color='475569')
    for col_letter in ('C', 'D', 'E'):
        ws.column_dimensions[col_letter].width = 12

    # ── Sheet 2: Responses ────────────────────────────────────────────────
    ws2 = wb.create_sheet('Responses')
    ws2.sheet_properties.tabColor = '23A455'

    response_headers = ['ID', 'Ticket', 'Date', 'Time', 'Experience', 'Category', 'Topic', 'Sentiment', 'Comment Sentiment', 'Status', 'Comment']

    # Write header row
    for col_idx, header_text in enumerate(response_headers, 1):
        cell = ws2.cell(row=1, column=col_idx, value=header_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Write data rows
    EXPERIENCE_MAP = dict(FeedbackEntry.EXPERIENCE_CHOICES)
    SENTIMENT_MAP = dict(FeedbackEntry.SENTIMENT_CHOICES)
    CATEGORY_MAP = dict(FeedbackEntry.CATEGORY_CHOICES)
    STATUS_MAP = dict(FeedbackEntry.STATUS_CHOICES)

    entries = qs.order_by('-created_at')
    for row_idx, entry in enumerate(entries, 2):
        local_dt = timezone.localtime(entry.created_at) if entry.created_at else None
        row_data = [
            entry.pk,
            entry.ticket_number,
            local_dt.strftime('%Y-%m-%d') if local_dt else '',
            local_dt.strftime('%I:%M %p') if local_dt else '',
            EXPERIENCE_MAP.get(entry.experience, entry.experience),
            CATEGORY_MAP.get(entry.category, entry.category),
            ', '.join(_TOPIC_DISPLAY.get(t, t) for t in entry.topics or []),
            SENTIMENT_MAP.get(entry.sentiment, entry.sentiment),
            SENTIMENT_MAP.get(entry.comment_sentiment, entry.comment_sentiment),
            STATUS_MAP.get(entry.status, entry.status),
            entry.comment,
        ]
        for col_idx, value in enumerate(row_data, 1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=value)
            cell.font = value_font
            cell.border = thin_border

    # Auto-size key columns (approximate widths)
    col_widths = {'A': 8, 'B': 8, 'C': 12, 'D': 10, 'E': 18, 'F': 14, 'G': 30, 'H': 12, 'I': 20, 'J': 12, 'K': 60}
    for col_letter, width in col_widths.items():
        ws2.column_dimensions[col_letter].width = width

    # Freeze the header row
    ws2.freeze_panes = 'A2'

    # ── Return the file ───────────────────────────────────────────────────
    filename = f'CSAS-Report-{period_label.replace(" ", "-")}-{now.date().isoformat()}.xlsx'

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@superuser_required
def activity_log(request):
    """
    Government-facing audit trail sourced entirely from Django's built-in
    django_admin_log table.
    """
    logs = list(
        LogEntry.objects
        .select_related('user', 'content_type')
        .order_by('-action_time')
    )

    feedback_ct = _feedback_content_type()
    feedback_ct_id = feedback_ct.pk
    valid_pks = [
        int(log.object_id)
        for log in logs
        if log.content_type_id == feedback_ct_id and log.object_id and log.object_id.isdigit()
    ]
    feedback_entries = {e.pk: e for e in FeedbackEntry.objects.filter(pk__in=valid_pks)}

    rows = []
    for log in logs:
        is_fb = log.content_type_id == feedback_ct_id
        entry = feedback_entries.get(int(log.object_id)) if is_fb and log.object_id and log.object_id.isdigit() else None
        rows.append(_format_audit_row(log, entry))

    context = {
        'logs_data': rows,
        'total_actions': len(rows),
    }
    return render(request, 'feedback_admin/activity_log.html', context)


# ── user management ───────────────────────────────────────────────────

@superuser_required
def users(request):
    all_users = User.objects.all().order_by('-date_joined').prefetch_related('groups', 'user_permissions')
    all_groups = Group.objects.annotate(member_count=Count('user')).prefetch_related('permissions')
    all_perms = Permission.objects.select_related('content_type').order_by('content_type__app_label', 'codename')

    user_counts = User.objects.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        staff=Count('id', filter=Q(is_staff=True)),
    )
    evaluated_groups = list(all_groups)

    context = {
        'users': all_users,
        'groups': evaluated_groups,
        'permissions': all_perms,
        'total_users': user_counts['total'] or 0,
        'active_users': user_counts['active'] or 0,
        'staff_users': user_counts['staff'] or 0,
        'total_groups': len(evaluated_groups),
    }
    return render(request, 'feedback_admin/users.html', context)


@superuser_required
@require_POST
def user_add(request):
    def err(msg):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg})
        messages.error(request, msg)
        return redirect('users')

    username = request.POST.get('username', '').strip()
    if not username:
        return err('Username is required.')
    if User.objects.filter(username=username).exists():
        return err(f'Username "{username}" is already taken.')

    pw1 = request.POST.get('password1', '')
    pw2 = request.POST.get('password2', '')
    if pw1 != pw2:
        return err('Passwords do not match.')

    tmp_user = User(
        username=username,
        email=request.POST.get('email', '').strip(),
        first_name=request.POST.get('first_name', '').strip(),
        last_name=request.POST.get('last_name', '').strip(),
    )
    try:
        validate_password(pw1, user=tmp_user)
    except ValidationError as e:
        return err(' '.join(e.messages))

    has_usable_password = request.POST.get('has_usable_password', 'on') == 'on'

    user = User.objects.create(
        username=username,
        email=tmp_user.email,
        first_name=tmp_user.first_name,
        last_name=tmp_user.last_name,
        is_active='is_active' in request.POST,
        is_staff='is_staff' in request.POST,
        is_superuser='is_superuser' in request.POST,
    )
    if has_usable_password:
        user.set_password(pw1)
    else:
        user.set_unusable_password()
    user.save()

    group_ids = request.POST.getlist('groups')
    if group_ids:
        user.groups.set(Group.objects.filter(id__in=group_ids))
    perm_ids = request.POST.getlist('user_permissions')
    if perm_ids:
        user.user_permissions.set(Permission.objects.filter(id__in=perm_ids))

    log_admin_event(request.user, user, ADDITION, f'Created user "{username}"')

    msg = f'User "{username}" created successfully.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


@staff_required
@require_POST
def user_edit(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if not (request.user.is_superuser or request.user == user):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'Access restricted to administrators.'}, status=403)
        messages.error(request, 'Access restricted to administrators.')
        return redirect('dashboard')

    original = {
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_active': user.is_active,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'groups': list(user.groups.values_list('id', flat=True)),
        'perms': list(user.user_permissions.values_list('id', flat=True)),
        'has_password': user.has_usable_password(),
    }

    def err(msg):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg})
        messages.error(request, msg)
        return redirect('users')

    username = request.POST.get('username', '').strip()
    if not username:
        return err('Username is required.')
    if User.objects.filter(username=username).exclude(pk=user_id).exists():
        return err(f'Username "{username}" is already taken.')

    user.username = username
    user.email = request.POST.get('email', '').strip()
    user.first_name = request.POST.get('first_name', '').strip()
    user.last_name = request.POST.get('last_name', '').strip()

    if request.user.is_superuser:
        if user == request.user:
            user.is_active = True
        else:
            user.is_active = 'is_active' in request.POST

        user.is_staff = 'is_staff' in request.POST
        user.is_superuser = 'is_superuser' in request.POST

    pw1 = request.POST.get('password1', '')
    pw2 = request.POST.get('password2', '')
    if pw1:
        if user == request.user:
            current_pw = request.POST.get('current_password', '')
            if not current_pw:
                return err('Current password is required to change your password.')
            if not user.check_password(current_pw):
                return err('Current password is incorrect.')

        if pw1 != pw2:
            return err('Passwords do not match.')
        try:
            validate_password(pw1, user=user)
        except ValidationError as e:
            return err(' '.join(e.messages))
        user.set_password(pw1)
        if user == request.user:
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, user)

    has_usable_password = request.POST.get('has_usable_password', 'on') == 'on'
    if not pw1:
        if has_usable_password and not user.has_usable_password():
            pass
        elif not has_usable_password:
            user.set_unusable_password()

    user.save()
    if request.user.is_superuser:
        user.groups.set(Group.objects.filter(id__in=request.POST.getlist('groups')))
        user.user_permissions.set(Permission.objects.filter(id__in=request.POST.getlist('user_permissions')))

    changed_fields = []
    for key, label in [
        ('username', 'username'),
        ('email', 'email'),
        ('first_name', 'first name'),
        ('last_name', 'last name'),
    ]:
        if original[key] != getattr(user, key):
            changed_fields.append(label)
    if original['is_active'] != user.is_active:
        changed_fields.append('status')
    if original['is_staff'] != user.is_staff:
        changed_fields.append('staff role')
    if original['is_superuser'] != user.is_superuser:
        changed_fields.append('superuser role')
    if original['groups'] != list(user.groups.values_list('id', flat=True)):
        changed_fields.append('groups')
    if original['perms'] != list(user.user_permissions.values_list('id', flat=True)):
        changed_fields.append('permissions')
    if original['has_password'] != user.has_usable_password() or pw1:
        changed_fields.append('password')

    if changed_fields:
        target = 'admin profile' if user == request.user else f'user "{user.username}"'
        log_admin_event(
            request.user,
            user,
            CHANGE,
            f'Updated {target}: {", ".join(changed_fields)}',
        )

    msg = f'User "{user.username}" updated successfully.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


@superuser_required
@require_POST
def user_delete(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': 'You cannot delete your own account.'})
        messages.error(request, "You cannot delete your own account.")
        return redirect('users')
    username = user.username
    log_admin_event(request.user, user, DELETION, f'Deleted user "{username}"')
    user.delete()

    msg = f'User "{username}" deleted.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


@superuser_required
@require_POST
def user_toggle_active(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect('users')
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    state = 'activated' if user.is_active else 'deactivated'
    log_admin_event(request.user, user, CHANGE, f'User "{user.username}" {state}')
    messages.success(request, f'User "{user.username}" {state}.')
    return redirect('users')


@superuser_required
@require_POST
def group_add(request):
    def err(msg):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg})
        messages.error(request, msg)
        return redirect('users')

    name = request.POST.get('name', '').strip()
    if not name:
        return err('Group name is required.')
    if Group.objects.filter(name=name).exists():
        return err(f'Group "{name}" already exists.')

    group = Group.objects.create(name=name)
    perm_ids = request.POST.getlist('permissions')
    if perm_ids:
        group.permissions.set(Permission.objects.filter(id__in=perm_ids))

    log_admin_event(request.user, group, ADDITION, f'Created group "{name}"')

    msg = f'Group "{name}" created.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


@superuser_required
@require_POST
def group_edit(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    original_name = group.name
    original_perms = list(group.permissions.values_list('id', flat=True))

    def err(msg):
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': msg})
        messages.error(request, msg)
        return redirect('users')

    name = request.POST.get('name', '').strip()
    if not name:
        return err('Group name is required.')
    if Group.objects.filter(name=name).exclude(pk=group_id).exists():
        return err(f'Group "{name}" already exists.')

    group.name = name
    group.save()
    group.permissions.set(Permission.objects.filter(id__in=request.POST.getlist('permissions')))

    if original_name != group.name or original_perms != list(group.permissions.values_list('id', flat=True)):
        log_admin_event(request.user, group, CHANGE, f'Updated group "{group.name}"')

    msg = f'Group "{name}" updated.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


@superuser_required
@require_POST
def group_delete(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    name = group.name
    log_admin_event(request.user, group, DELETION, f'Deleted group "{name}"')
    group.delete()

    msg = f'Group "{name}" deleted.'
    if _is_ajax(request):
        return JsonResponse({'ok': True, 'message': msg})
    messages.success(request, msg)
    return redirect('users')


# ── LOGIN ─────────────────────────────────────────────────────────────
def admin_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('dashboard')
        logout(request)

    form = AuthenticationForm(request, data=request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            if not (user.is_staff or user.is_superuser):
                messages.error(request, 'Access denied. Staff privileges are required.')
                return render(request, 'feedback_admin/login.html', {'form': form})
            login(request, user)
            log_admin_event(user, user, ADDITION, 'Logged in')
            return redirect('dashboard')
        else:
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '')
            try:
                potential_user = User.objects.get(username=username)
                if potential_user.check_password(password):
                    if not potential_user.is_active:
                        messages.error(request, 'This account has been deactivated. Please contact your system administrator.')
                    elif not (potential_user.is_staff or potential_user.is_superuser):
                        messages.error(request, 'Access denied. Staff privileges are required.')
            except User.DoesNotExist:
                pass

    return render(request, 'feedback_admin/login.html', {'form': form})

# ── LOGOUT ────────────────────────────────────────────────────────────
@require_POST
def admin_logout(request):
    if request.user.is_authenticated:
        log_admin_event(request.user, request.user, CHANGE, 'Logged out')
    logout(request)
    messages.success(request, 'You have been signed out.')
    return redirect('admin_login')

@superuser_required
def settings_page(request):
    try:
        backups = list_backups()
        backup_error = None
    except Exception as e:
        backups = []
        backup_error = str(e)

    context = {
        'feedback_config': FeedbackConfiguration.get_solo(),
        'backups': backups,
        'backup_error': backup_error,
    }
    return render(request, 'feedback_admin/settings.html', context)


from feedback.services import reanalyze_pending_entries, topic_counts


@superuser_required
@require_POST
def update_sentiment_settings(request):
    old_value = FeedbackConfiguration.get_solo().auto_analysis_enabled
    auto_analysis_enabled = request.POST.get('auto_analysis_enabled') == 'on'
    config = FeedbackConfiguration.get_solo()
    config.auto_analysis_enabled = auto_analysis_enabled
    config.save(update_fields=['auto_analysis_enabled', 'updated_at'])

    if old_value != auto_analysis_enabled:
        log_admin_event(
            request.user,
            config,
            CHANGE,
            f'Auto-analysis {"enabled" if auto_analysis_enabled else "disabled"}',
        )

    state = 'enabled' if auto_analysis_enabled else 'disabled'
    return JsonResponse({
        'ok': True,
        'message': f'Auto-analysis {state}. New feedback will {"be analyzed for sentiment immediately" if auto_analysis_enabled else "stay pending until you re-enable auto-analysis or run re-analysis"}.',
        'auto_analysis_enabled': auto_analysis_enabled,
    })


@superuser_required
@require_POST
def reanalyze_sentiment_view(request):
    total, processed = reanalyze_pending_entries(force=False)
    config = FeedbackConfiguration.get_solo()
    log_admin_event(
        request.user,
        config,
        CHANGE,
        f'Batch re-analysis: scanned {total}, updated {processed}',
    )
    if total == 0:
        message = 'Nothing to process — no pending entries with comments found.'
    else:
        message = f'Re-analysis complete: {processed} of {total} pending entries categorized.'
    return JsonResponse({'ok': True, 'message': message, 'processed': processed, 'total': total})


@superuser_required
@require_POST
def update_notification_settings(request):
    config = FeedbackConfiguration.get_solo()

    daily_summary_enabled = request.POST.get('daily_summary_enabled') == 'on'
    notification_email = request.POST.get('notification_email', '').strip()
    daily_summary_time = request.POST.get('daily_summary_time', '17:30').strip()

    if notification_email:
        try:
            validate_email(notification_email)
        except ValidationError:
            return JsonResponse({
                'ok': False,
                'error': 'Please enter a valid notification email address.'
            }, status=400)

    config.daily_summary_enabled = daily_summary_enabled
    config.notification_email = notification_email
    config.daily_summary_time = daily_summary_time or '17:30'
    config.save(update_fields=[
        'daily_summary_enabled',
        'notification_email',
        'daily_summary_time',
        'updated_at',
    ])

    log_admin_event(
        request.user,
        config,
        CHANGE,
        f'Notification settings updated: daily summary {"enabled" if daily_summary_enabled else "disabled"}, email="{notification_email}"',
    )

    return JsonResponse({
        'ok': True,
        'message': 'Notification settings saved successfully.',
        'daily_summary_enabled': daily_summary_enabled,
        'notification_email': notification_email,
        'daily_summary_time': config.daily_summary_time,
    })


@superuser_required
@require_POST
def send_summary_now(request):
    config = FeedbackConfiguration.get_solo()
    target_email = request.POST.get('email', '').strip() or config.notification_email or request.user.email
    is_test = request.POST.get('is_test') == 'true'

    if not target_email:
        return JsonResponse({
            'ok': False,
            'error': 'No notification email address found. Please specify an email address first.'
        }, status=400)

    try:
        validate_email(target_email)
    except ValidationError:
        return JsonResponse({
            'ok': False,
            'error': f'"{target_email}" is not a valid email address.'
        }, status=400)

    base_url = request.build_absolute_uri('/')
    result = send_daily_summary_email(
        recipient_email=target_email,
        force=True,
        is_test=is_test,
        base_url=base_url
    )

    if result.get('ok'):
        action_desc = 'Test daily summary' if is_test else 'Daily summary'
        log_admin_event(
            request.user,
            config,
            CHANGE,
            f'{action_desc} email dispatched manually to {target_email}',
        )
        return JsonResponse({
            'ok': True,
            'message': f'Daily summary successfully dispatched to {target_email}.',
            'recipient': target_email,
        })
    else:
        return JsonResponse({
            'ok': False,
            'error': result.get('message', 'Failed to dispatch email.')
        }, status=500)


send_test_daily_summary = send_summary_now


@csrf_exempt
def cron_daily_summary(request):
    """
    Automated daily feedback summary endpoint for Vercel Cron Jobs,
    scheduled workflows, or external webhook calls.
    Supports GET (standard Vercel Cron) and POST.
    Protected by CRON_SECRET if configured in environment variables.
    """
    configured_secret = (getattr(settings, 'CRON_SECRET', '') or os.environ.get('CRON_SECRET', '')).strip()
    if configured_secret:
        auth_header = request.headers.get('Authorization', '')
        secret_param = request.GET.get('secret') or request.GET.get('key')
        expected_bearer = f"Bearer {configured_secret}"
        if auth_header != expected_bearer and secret_param != configured_secret:
            return JsonResponse({
                'ok': False,
                'error': 'Unauthorized: Invalid or missing cron secret.'
            }, status=401)

    # Optional query parameters: force, test, date (YYYY-MM-DD)
    force = request.GET.get('force') == 'true' or request.POST.get('force') == 'true'
    is_test = request.GET.get('test') == 'true' or request.POST.get('test') == 'true'
    date_str = request.GET.get('date') or request.POST.get('date')
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({
                'ok': False,
                'error': f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."
            }, status=400)

    base_url = request.build_absolute_uri('/')
    result = send_daily_summary_email(
        target_date=target_date,
        force=force,
        is_test=is_test,
        base_url=base_url
    )

    status_code = 200 if result.get('ok') else 400
    if result.get('reason') in ('disabled', 'zero_feedback_suppressed'):
        status_code = 200

    return JsonResponse(result, status=status_code)


@superuser_required
@require_POST
def backup_create(request):
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': 'Only superusers can create backups.'}, status=403)
    try:
        result = create_backup()
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Backup failed: {e}'}, status=500)

    log_admin_event(request.user, FeedbackConfiguration.get_solo(), ADDITION,
                     f'Created backup "{result["filename"]}"')

    feedback_count = FeedbackEntry.objects.count()

    return JsonResponse({
        'ok': True,
        'message': f'Backup created: {result["filename"]}',
        'feedback_count': feedback_count,
        'backup': {
            'filename': result['filename'],
            'size_bytes': result['size_bytes'],
            'size_display': result.get('size_display', ''),
            'created_display': result.get('created_display', ''),
            'download_url': reverse('backup_download', args=[result['filename']]),
        },
    })


@superuser_required
def backup_download(request, filename):
    if not request.user.is_superuser:
        messages.error(request, 'Only superusers can download backups.')
        return redirect('settings_page')
    try:
        path = resolve_backup_path(filename)
    except (SuspiciousFileOperation, FileNotFoundError):
        messages.error(request, f'Backup file "{filename}" was not found on disk. The list has been refreshed.')
        return redirect(reverse('settings_page') + '#system-settings')
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=filename)


@superuser_required
@require_POST
def backup_delete(request, filename):
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': 'Only superusers can delete backups.'}, status=403)
    try:
        delete_backup(filename)
    except (SuspiciousFileOperation, FileNotFoundError):
        return JsonResponse({'ok': False, 'error': 'Backup not found.'}, status=404)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Delete failed: {e}'}, status=500)

    log_admin_event(request.user, FeedbackConfiguration.get_solo(), DELETION,
                     f'Deleted backup "{filename}"')
    return JsonResponse({'ok': True, 'message': f'Backup "{filename}" deleted.'})


@superuser_required
@require_POST
def backup_restore(request):
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': 'Only superusers can restore backups.'}, status=403)

    uploaded = request.FILES.get('backup_file')
    existing_filename = request.POST.get('existing_filename', '').strip()

    if not uploaded and not existing_filename:
        return JsonResponse({'ok': False, 'error': 'No backup file or archive specified.'}, status=400)

    try:
        if uploaded:
            safety = restore_backup(uploaded)
            source_label = getattr(uploaded, 'name', 'uploaded backup')
        else:
            path = resolve_backup_path(existing_filename)
            with open(path, 'rb') as f:
                safety = restore_backup(f)
            source_label = existing_filename
    except (SuspiciousFileOperation, FileNotFoundError):
        return JsonResponse({'ok': False, 'error': f'Backup file "{existing_filename}" was not found on disk.'}, status=404)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Restore failed: {e}'}, status=500)

    log_admin_event(request.user, FeedbackConfiguration.get_solo(), CHANGE,
                     f'Restored database from "{source_label}" (safety backup: "{safety["filename"]}")')

    feedback_count = FeedbackEntry.objects.count()

    return JsonResponse({
        'ok': True,
        'message': f'Database restored successfully from "{source_label}". Previous data was saved as "{safety["filename"]}".',
        'feedback_count': feedback_count,
        'safety_backup': {
            'filename': safety['filename'],
            'size_bytes': safety['size_bytes'],
            'size_display': safety.get('size_display', ''),
            'created_display': safety.get('created_display', ''),
            'download_url': reverse('backup_download', args=[safety['filename']]),
        },
    })