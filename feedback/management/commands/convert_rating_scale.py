from django.core.management.base import BaseCommand
from django.db import transaction

from feedback.models import FeedbackEntry

SQD_FIELDS = [f'sqd{i}' for i in range(9)]


class Command(BaseCommand):
    help = (
        "Convert feedback entries recorded on the former 5-point agreement scale "
        "(Strongly Agree ... Strongly Disagree, Not Applicable) to the 3-point "
        "Very Satisfactory / Satisfactory / Unsatisfactory scale. Only entries "
        "whose experience still holds a legacy value are touched, so the command "
        "is safe to run more than once. Sentiment is not changed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        legacy_map = FeedbackEntry.LEGACY_EXPERIENCE_MAP
        score_map = FeedbackEntry.LEGACY_SQD_SCORE_MAP

        entries = FeedbackEntry.objects.filter(experience__in=legacy_map.keys())
        unmapped = (
            FeedbackEntry.objects
            .exclude(experience__in=legacy_map.keys())
            .exclude(experience__in=[value for value, _ in FeedbackEntry.EXPERIENCE_CHOICES])
            .count()
        )

        converted = 0
        with transaction.atomic():
            for entry in entries.iterator(chunk_size=200):
                entry.experience = legacy_map[entry.experience]
                for field in SQD_FIELDS:
                    score = getattr(entry, field)
                    if score is not None:
                        setattr(entry, field, score_map.get(score))
                if not dry_run:
                    entry.save(update_fields=['experience', *SQD_FIELDS])
                converted += 1

        verb = 'Would convert' if dry_run else 'Converted'
        self.stdout.write(self.style.SUCCESS(f'{verb} {converted} entries to the 3-point scale.'))
        if unmapped:
            self.stdout.write(self.style.WARNING(
                f'{unmapped} entries hold a rating with no 3-point equivalent '
                "(for example 'Neither Agree nor Disagree' or 'Not Applicable') and were left unchanged."
            ))
