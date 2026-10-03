"""
Database backup and restore utilities.

Backups are Django JSON fixtures made with `dumpdata`, so no pg_dump or other
database binary is needed (works locally and on Vercel). Restores run `loaddata`
inside one transaction.

Files live in settings.BACKUP_DIR, falling back to tempfile.gettempdir()/backups
when that is read-only (e.g. on Vercel).
"""
import datetime
import json
import re
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

BACKUP_FILENAME_RE = re.compile(r'^philhealth_backup_(\d{8}_\d{6})(?:_\d+)?\.json$')


def get_backup_dir() -> Path:
    """settings.BACKUP_DIR, or the temp dir when that cannot be written (Vercel)."""
    candidate = Path(settings.BACKUP_DIR)
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
    backup_dir.mkdir(parents=True, exist_ok=True)
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
        match = BACKUP_FILENAME_RE.match(path.name)
        if not match:
            continue
        try:
            stat = path.stat()
            created = timezone.make_aware(datetime.datetime.strptime(match.group(1), '%Y%m%d_%H%M%S'))
        except (OSError, ValueError):
            continue
        rows.append({
            'filename': path.name,
            'size_bytes': stat.st_size,
            'size_display': _human_size(stat.st_size),
            'created_at': created,
            'created_display': timezone.localtime(created).strftime('%b %d, %Y at %I:%M %p'),
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


def restore_backup(uploaded_file):
    """Restores the database from a .json backup (an UploadedFile or open binary file).
    Takes a safety backup of the current database before applying changes.
    """
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='wb') as tmp:
        shutil.copyfileobj(uploaded_file, tmp)
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
            # Older backups may hold fields since dropped (e.g. survey settings).
            call_command('loaddata', str(tmp_path), ignorenonexistent=True)
    except Exception as exc:
        raise RuntimeError(f'Failed to restore backup: {exc}') from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return safety