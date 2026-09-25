from django.core.management.base import BaseCommand

from feedback.services import reanalyze_pending_entries


class Command(BaseCommand):
    help = (
        "Run sentiment analysis on feedback entries. By default, only "
        "entries with a comment that are still 'pending' are processed — "
        "already-categorized entries are left untouched. Pass --force to "
        "re-run analysis on every entry with a comment regardless of its "
        "current sentiment (use this after retraining the model)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-analyze ALL entries with a comment, even ones already categorized.',
        )

    def handle(self, *args, **options):
        force = options['force']
        total, processed = reanalyze_pending_entries(force=force)

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to process — no eligible entries found."))
            return

        mode = "FORCE mode (all entries)" if force else "default mode (pending only)"
        self.stdout.write(self.style.SUCCESS(
            f"Done [{mode}]. Scanned {total} entries — {processed} updated."
        ))