import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from feedback.models import FeedbackConfiguration, FeedbackEntry
from feedback.services import analyze_comment_sentiment

# Each comment is written so the sentiment model reads it as the group's
# `sentiment`; feedback/tests.py checks this whenever the model file exists.
SAMPLE_FEEDBACK = [
    {
        'experience': FeedbackEntry.VERY_SATISFACTORY,
        'category': FeedbackEntry.COMPLIMENT,
        'sentiment': FeedbackEntry.POSITIVE,
        'comments': [
            "Excellent service! The staff were very kind and helpful.",
            "Salamat po sa mabilis at maayos na serbisyo!",
            "Thank you! Fast, friendly, and excellent service.",
            "Maraming salamat po, excellent ang serbisyo!",
            "The best experience I have had in a government office. Thank you!",
        ]
    },
    {
        'experience': FeedbackEntry.SATISFACTORY,
        'category': FeedbackEntry.COMPLIMENT,
        'sentiment': FeedbackEntry.POSITIVE,
        'comments': [
            "Excellent customer service. Everything was fast and easy.",
            "Great job to the frontline staff. Very helpful and friendly.",
            "Friendly guard, helpful staff, and a clean office. Great job!",
            "Very happy with the fast and friendly service today. Thank you!",
        ]
    },
    {
        'experience': FeedbackEntry.SATISFACTORY,
        'category': '',
        'sentiment': FeedbackEntry.NEUTRAL,
        'comments': [
            "Submitted my documents.",
            "Inquiry lang po.",
            "Okay lang naman.",
            "Medyo okay naman.",
            "Average lang po.",
            "Pwede na.",
            "It was an average visit.",
        ]
    },
    {
        'experience': FeedbackEntry.UNSATISFACTORY,
        'category': FeedbackEntry.COMPLAINT,
        'sentiment': FeedbackEntry.NEGATIVE,
        'comments': [
            "The staff were rude and I was very disappointed.",
            "Unhelpful staff and confusing instructions. I wasted my whole day.",
            "Delayed again. Very disappointing and confusing process.",
            "Very disappointed. My claim was delayed again with no explanation.",
            "Terrible service. I wasted three hours and nobody helped me.",
            "Worst service. The guard was rude and the queue was a mess.",
            "Very disappointed with the long delay and the unhelpful staff.",
            "The system was down and I had to come back another day. Very disappointing.",
            "Napakabagal ng proseso. Ang tagal naming naghintay.",
            "Sobrang tagal ng pila at masungit ang staff.",
        ]
    },
]

STATUSES = [FeedbackEntry.PENDING, 'reviewed', 'resolved']


class Command(BaseCommand):
    help = 'Seed realistic sample feedback entries into the database for testing and demonstration.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=40,
            help='Number of feedback entries to generate (default: 40)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=60,
            help='Number of past days to distribute created timestamps across (default: 60)'
        )

    def handle(self, *args, **options):
        count = options['count']
        days = options['days']
        now = timezone.now()
        auto_analysis = FeedbackConfiguration.auto_analysis_is_enabled()

        self.stdout.write(f'Generating {count} sample feedback entries across the past {days} days (Auto-analysis: {auto_analysis})...')

        created_entries = 0
        for i in range(count):
            group = random.choices(
                SAMPLE_FEEDBACK,
                weights=[35, 20, 25, 20],
                k=1
            )[0]

            exp = group['experience']
            cat = group['category']
            comment = random.choice(group['comments'])
            status = random.choices(STATUSES, weights=[40, 35, 25], k=1)[0]

            # Determine sentiment strictly based on system settings
            if auto_analysis and comment:
                sent = analyze_comment_sentiment(comment)
            else:
                sent = FeedbackEntry.PENDING

            # Random timestamp within past `days`
            random_seconds = random.randint(0, days * 86400)
            created_at = now - timedelta(seconds=random_seconds)

            entry = FeedbackEntry(
                experience=exp,
                sentiment=sent,
                category=cat,
                comment=comment,
                status=status,
            )
            # Save to generate tracking code
            entry.save()

            # Override created_at timestamp
            FeedbackEntry.objects.filter(pk=entry.pk).update(created_at=created_at, updated_at=created_at)
            created_entries += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully created {created_entries} sample feedback entries!'))
