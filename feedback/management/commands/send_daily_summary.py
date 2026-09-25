from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime

from feedback.email_service import send_daily_summary_email


class Command(BaseCommand):
    help = "Dispatches the daily feedback count and satisfaction rate summary email."

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Target date to summarize in YYYY-MM-DD format (defaults to current date in Asia/Manila).'
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Recipient email override (bypasses saved settings recipient).'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force send even if daily summary is disabled in settings.'
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Send in test dispatch mode with test alert banners.'
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        target_date = None
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format '{date_str}'. Expected YYYY-MM-DD."))
                return

        recipient = options.get('email')
        force = options.get('force', False)
        is_test = options.get('test', False)

        self.stdout.write(f"Preparing daily feedback summary for {target_date or timezone.localtime().date()}...")

        result = send_daily_summary_email(
            target_date=target_date,
            recipient_email=recipient,
            force=force,
            is_test=is_test
        )

        if result['ok']:
            self.stdout.write(self.style.SUCCESS(
                f"[SUCCESS] {result['message']} "
                f"Count: {result['metrics']['total_count']}, "
                f"Satisfaction: {result['metrics']['satisfaction_rate']}%"
            ))
        else:
            reason = result.get('reason')
            if reason == 'zero_feedback_suppressed':
                self.stdout.write(self.style.WARNING(f"[SUPPRESSED] {result['message']}"))
            elif reason == 'disabled':
                self.stdout.write(self.style.WARNING(f"[DISABLED] {result['message']} (Use --force to override)"))
            else:
                self.stderr.write(self.style.ERROR(f"[FAILED] {result['message']}"))
