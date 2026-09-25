import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from feedback.models import FeedbackConfiguration, FeedbackEntry
from feedback.services import analyze_comment_sentiment

SAMPLE_FEEDBACK = [
    # Strongly Agree / Positive Compliments
    {
        'experience': FeedbackEntry.STRONGLY_AGREE,
        'category': FeedbackEntry.COMPLIMENT,
        'comments': [
            "Fast and courteous service at window 3. Very satisfied with the PhilHealth ID processing!",
            "Nagpapasalamat ako sa mabilis na pag-assist sa aking Member Data Record (MDR) request.",
            "Clean office environment and helpful security staff guiding senior citizens.",
            "Efficient transaction today! Less than 15 minutes wait time for contribution updates.",
            "Very responsive staff at the express lane for pregnant women and PWDs.",
            "Excellent customer service! All my questions about Konsulta benefits were explained clearly.",
        ]
    },
    # Agree / Positive & Neutral
    {
        'experience': FeedbackEntry.AGREE,
        'category': FeedbackEntry.COMPLIMENT,
        'comments': [
            "Mabait at maasikaso ang frontline staff sa pag-update ng aking dependents.",
            "Smooth submission process. The Citizen's Charter guidelines were very clear.",
            "Process was clear and staff answered all our questions kindly.",
        ]
    },
    # Neither Agree nor Disagree / Neutral Suggestions
    {
        'experience': FeedbackEntry.NEITHER,
        'category': FeedbackEntry.SUGGESTION,
        'comments': [
            "Overall good experience, but please consider adding more chairs in the waiting area.",
            "Sana ay palakihin pa ang queue display monitor para kitang-kita mula sa dulo.",
            "Service was fine. It would be better if online appointment system is promoted more.",
            "Mabilis naman ang proseso, mas maganda sana kung may libreng drinking water sa waiting lounge.",
            "Clear process, though queueing system ticket printer occasionally stalls.",
            "Staff was polite. Additional air-conditioning in the main hall would help during peak hours.",
            "Good service overall. Clearer signage for senior citizen express counter is suggested.",
        ]
    },
    # Disagree / Negative Complaints
    {
        'experience': FeedbackEntry.DISAGREE,
        'category': FeedbackEntry.COMPLAINT,
        'comments': [
            "Medyo matagal ang pila nung umaga. Sana madagdagan ang active counters during peak hours.",
            "Informational posters regarding updated premium contribution rates were confusing.",
            "Counters were understaffed during lunch break resulting in long line buildup.",
            "Matagal ang veripikasyon ng member record. Kailangan po ng karagdagang verification officers.",
        ]
    },
    # Strongly Disagree / Severe Negative Complaints & Concerns
    {
        'experience': FeedbackEntry.STRONGLY_DISAGREE,
        'category': FeedbackEntry.COMPLAINT,
        'comments': [
            "Waited over 2 hours just to submit my claim documents. Window 2 was offline for too long.",
            "Network system interruption for 30 minutes caused unexpected delay.",
            "System verification error required me to return another day for my MDR update.",
            "Need clearer step-by-step flowchart at the entrance for first-time walk-in applicants.",
            "Long waiting time at counter 4 for employer remittance corrections.",
        ]
    },
    # Not Applicable / General Inquiries
    {
        'experience': FeedbackEntry.NOT_APPLICABLE,
        'category': FeedbackEntry.SUGGESTION,
        'comments': [
            "Inquired about Konsulta package requirements for non-resident relatives.",
            "Just picked up printed form guidelines.",
        ]
    }
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
                weights=[45, 30, 12, 6, 4, 3],  # 45% SA, 30% A, 12% NAD, 6% D, 4% SD, 3% N/A
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
