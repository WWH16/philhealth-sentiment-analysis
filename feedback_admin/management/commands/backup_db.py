from django.core.management.base import BaseCommand

from feedback_admin.backup_utils import create_backup


class Command(BaseCommand):
    help = 'Creates a manual backup of the database (BASE_DIR/backups by default).'

    def handle(self, *args, **options):
        result = create_backup()
        self.stdout.write(self.style.SUCCESS(
            f"Backup created: {result['filename']} ({result['size_bytes']} bytes)"
        ))