"""
Database backup and restore utilities.

Supports both Local and Vercel (serverless AWS Lambda) environments without
requiring external database CLI binaries (mysqldump, pg_dump, psql, mysql) on PATH.

Backups are created as standard Django JSON fixtures via `dumpdata` (or SQL if provided).
Restores load JSON fixtures via `loaddata` inside an atomic transaction, or execute SQL
statements directly / via available DB tools.

Files live in settings.BACKUP_DIR or BASE_DIR/backups, falling back to tempfile.gettempdir()/backups
when the filesystem is read-only (e.g. on Vercel).
"""
import datetime
import json
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

BACKUP_FILENAME_RE = re.compile(r'^philhealth_backup_\d{8}_\d{6}(?:_\d+)?\.json$')


def get_backup_dir() -> Path:
    """Returns the writable backup directory.
    On Local: uses settings.BACKUP_DIR or BASE_DIR/backups.
    On Vercel (serverless read-only filesystem): falls back to /tmp/backups.
    """
    configured = getattr(settings, 'BACKUP_DIR', None)
    candidate = Path(configured) if configured else settings.BASE_DIR / 'backups'
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        # Verify writability (critical on Vercel where BASE_DIR cannot be written to)
        test_file = candidate / '.write_perm_test'
        test_file.touch()
        test_file.unlink()
        return candidate
    except (OSError, PermissionError):
        pass

    backup_dir = Path(tempfile.gettempdir()) / 'backups'
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return backup_dir


def _human_size(num_bytes):
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def create_backup():
    """Creates a timestamped database backup.
    Uses pure-Python Django dumpdata serializer to avoid any external binary dependency
    (works on Windows, Linux, and Vercel serverless without pg_dump or mysqldump).
    """
    backup_dir = get_backup_dir()
    timestamp = timezone.localtime(timezone.now()).strftime('%Y%m%d_%H%M%S')
    filename = f'philhealth_backup_{timestamp}.json'
    dest_path = backup_dir / filename
    counter = 1
    while dest_path.exists():
        filename = f'philhealth_backup_{timestamp}_{counter}.json'
        dest_path = backup_dir / filename
        counter += 1

    try:
        with open(dest_path, 'w', encoding='utf-8') as out:
            call_command(
                'dumpdata',
                exclude=['contenttypes', 'auth.permission', 'sessions'],
                natural_foreign=True,
                natural_primary=True,
                indent=2,
                stdout=out,
            )
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f'Backup creation failed: {exc}') from exc

    stat = dest_path.stat()
    created = timezone.localtime(timezone.now())
    return {
        'filename': filename,
        'size_bytes': stat.st_size,
        'size_display': _human_size(stat.st_size),
        'created_display': created.strftime('%b %d, %Y at %I:%M %p'),
    }


def list_backups():
    """Returns backup metadata, newest first (JSON only)."""
    backup_dir = get_backup_dir()
    rows = []
    for path in backup_dir.glob('philhealth_backup_*.json'):
        if not BACKUP_FILENAME_RE.match(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        ts_match = re.search(r'philhealth_backup_(\d{8}_\d{6})', path.name)
        if ts_match:
            ts_str = ts_match.group(1)
            try:
                created = timezone.make_aware(datetime.datetime.strptime(ts_str, '%Y%m%d_%H%M%S'))
            except (ValueError, OverflowError):
                created = timezone.make_aware(datetime.datetime.fromtimestamp(stat.st_mtime))
        else:
            created = timezone.make_aware(datetime.datetime.fromtimestamp(stat.st_mtime))

        rows.append({
            'filename': path.name,
            'size_bytes': stat.st_size,
            'size_display': _human_size(stat.st_size),
            'created_at': created,
            'created_display': timezone.localtime(created).strftime('%b %d, %Y at %I:%M %p'),
            'extension': 'JSON',
        })
    rows.sort(key=lambda r: r['created_at'], reverse=True)
    return rows


def resolve_backup_path(filename):
    if not BACKUP_FILENAME_RE.match(filename):
        raise SuspiciousFileOperation('Invalid backup filename.')
    path = get_backup_dir() / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def delete_backup(filename):
    resolve_backup_path(filename).unlink()


def _iter_file_chunks(file_obj, chunk_size=65536):
    if hasattr(file_obj, 'chunks'):
        yield from file_obj.chunks()
    else:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk


def restore_backup(uploaded_file):
    """Restores the database from an uploaded .json backup file.
    Takes an automated safety backup of the current database before applying changes.
    Supports both Django UploadedFile instances and standard file-like objects.
    """
    filename = getattr(uploaded_file, 'name', '') or 'backup'
    is_json = filename.lower().endswith('.json')

    head = uploaded_file.read(4096)
    uploaded_file.seek(0)
    head_text = head.decode('utf-8', errors='ignore').strip()

    if not is_json and not (head_text.startswith('[') or head_text.startswith('{')):
        raise ValueError('Only .json backup files are supported for database restore.')

    with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='wb') as tmp:
        for chunk in _iter_file_chunks(uploaded_file):
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    try:
        with open(tmp_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as err:
                raise ValueError(f'Invalid JSON format: {err}')
            if not isinstance(data, list):
                raise ValueError('Invalid backup format: expected a valid JSON backup list.')

        models_in_fixture = {
            item.get('model') for item in data if isinstance(item, dict) and 'model' in item
        }

        safety = create_backup()

        from feedback.models import FeedbackEntry

        with transaction.atomic():
            if 'feedback.feedbackentry' in models_in_fixture:
                FeedbackEntry.objects.all().delete()
            call_command('loaddata', str(tmp_path))
    except Exception as exc:
        raise RuntimeError(f'Failed to restore backup: {exc}') from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return safety