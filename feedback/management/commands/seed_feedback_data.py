import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from feedback.models import FeedbackConfiguration, FeedbackEntry
from feedback.services import analyze_comment_sentiment, combine_sentiment

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
            "Ang bilis ng proseso, mabait si ate sa counter. Salamat po!",
            "The staff was very helpful and accommodating.",
            "Malinis at malamig ang waiting area, comfortable maghintay.",
            "Smooth ang claim ko, approved agad. Salamat PhilHealth!",
            "Ang dali lang kumuha ng PhilHealth ID, same day nakuha ko na.",
            "Excellent service! Fast and friendly staff.",
            "Na-guide ako ng maayos sa pag-register ng dependents ko. Maraming salamat po.",
            "Sobrang bait ng staff, pinaliwanag lahat ng requirements.",
            "Kumuha ng PhilHealth ID para sa anak ko.",
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
            "Thank you sa guard na tumulong sa akin mag-fill out ng form.",
            "The employee explained the Konsulta package clearly. I understood everything.",
            "Great improvement compared last year, mas mabilis na talaga ngayon.",
            "Hanggang ngayon hindi pa rin approved ang claim ko, 2 months na.",
            "Inquiry about Konsulta registration.",
            "Nagpasa ng requirements para sa claim.",
            "Okay naman, pero sana dagdagan ang upuan sa waiting area.",
            "Mabait ang staff pero ang tagal ng pila.",
            "Fast service today, but the comfort room was dirty.",
            "Maayos naman ang transaction, kaso offline ang system kanina kaya naghintay kami ng isang oras.",
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
            "Napakabilis ng pag-update ng MDR ko, wala pang 10 minutes tapos na.",
            "Very organized ang pila, may number system na kaya hindi na magulo.",
            "Magalang at matiyaga si kuya kahit ang dami kong tanong.",
            "Inuna nila ang senior citizen at PWD, very considerate.",
            "Sobrang tagal ng pila, 3 oras ako naghintay tapos walang paliwanag.",
            "Hindi maayos ang serbisyo, bastos pa yung guard.",
            "The staff was rude and did not answer my questions properly.",
            "Pinabalik-balik ako ng tatlong beses dahil kulang daw ang requirements, hindi naman sinabi agad.",
            "Walang upuan sa labas, nakatayo kami sa init ng ilang oras.",
            "Offline daw ang system kaya hindi ako na-serve. Sayang ang pamasahe ko.",
            "Ang sungit ng nasa window 2, sinigawan pa ako.",
            "Very slow process. Only two windows open for so many clients.",
            "Walang malinaw na instructions, hindi ko alam kung saan pipila.",
            "Nag-lunch break lahat ng staff ng sabay, walang nag-aasikaso.",
            "Disappointed. Pinaghintay kami tapos sinabing bumalik na lang bukas.",
            "Hindi gumagana ang aircon, sobrang init sa loob.",
            "Mali ang spelling ng pangalan ko sa ID, kailangan ko pang bumalik ulit.",
            "Not helpful at all, they just kept passing me to another window.",
            "Nag-update lang po ako ng MDR.",
            "Nagbayad ng contribution para sa buwan na ito.",
            "Paano po mag-register as voluntary member?",
            "Sana po may online appointment para hindi na pumila.",
            "Pa-follow up lang po ng status ng claim ko.",
            "Changed my civil status from single to married.",
            "Suggestion: maglagay ng signage kung saan ang bawat window.",
            "Helpful si ma'am sa window 1, pero masungit yung sa window 3.",
        ]
    },
]

# Topics a client would pick for each sample comment (one or two).
W, S, F, D, O = 'waiting_time', 'staff', 'facilities', 'documents', 'other'
SAMPLE_TOPICS = {
    'Excellent service! The staff were very kind and helpful.': [S],
    'Salamat po sa mabilis at maayos na serbisyo!': [W],
    'Thank you! Fast, friendly, and excellent service.': [W, S],
    'Maraming salamat po, excellent ang serbisyo!': [S],
    'The best experience I have had in a government office. Thank you!': [O],
    'Ang bilis ng proseso, mabait si ate sa counter. Salamat po!': [W, S],
    'The staff was very helpful and accommodating.': [S],
    'Malinis at malamig ang waiting area, comfortable maghintay.': [F],
    'Smooth ang claim ko, approved agad. Salamat PhilHealth!': [D, W],
    'Ang dali lang kumuha ng PhilHealth ID, same day nakuha ko na.': [D, W],
    'Excellent service! Fast and friendly staff.': [W, S],
    'Na-guide ako ng maayos sa pag-register ng dependents ko. Maraming salamat po.': [S, D],
    'Sobrang bait ng staff, pinaliwanag lahat ng requirements.': [S, D],
    'Kumuha ng PhilHealth ID para sa anak ko.': [D],
    'Excellent customer service. Everything was fast and easy.': [W],
    'Great job to the frontline staff. Very helpful and friendly.': [S],
    'Friendly guard, helpful staff, and a clean office. Great job!': [S, F],
    'Very happy with the fast and friendly service today. Thank you!': [W, S],
    'Submitted my documents.': [D],
    'Inquiry lang po.': [O],
    'Okay lang naman.': [O],
    'Medyo okay naman.': [O],
    'Average lang po.': [O],
    'Pwede na.': [O],
    'It was an average visit.': [O],
    'Thank you sa guard na tumulong sa akin mag-fill out ng form.': [S, D],
    'The employee explained the Konsulta package clearly. I understood everything.': [S, D],
    'Great improvement compared last year, mas mabilis na talaga ngayon.': [W],
    'Hanggang ngayon hindi pa rin approved ang claim ko, 2 months na.': [D, W],
    'Inquiry about Konsulta registration.': [D],
    'Nagpasa ng requirements para sa claim.': [D],
    'Okay naman, pero sana dagdagan ang upuan sa waiting area.': [F],
    'Mabait ang staff pero ang tagal ng pila.': [S, W],
    'Fast service today, but the comfort room was dirty.': [W, F],
    'Maayos naman ang transaction, kaso offline ang system kanina kaya naghintay kami ng isang oras.': [W, F],
    'The staff were rude and I was very disappointed.': [S],
    'Unhelpful staff and confusing instructions. I wasted my whole day.': [S, W],
    'Delayed again. Very disappointing and confusing process.': [W],
    'Very disappointed. My claim was delayed again with no explanation.': [D, W],
    'Terrible service. I wasted three hours and nobody helped me.': [W, S],
    'Worst service. The guard was rude and the queue was a mess.': [S, W],
    'Very disappointed with the long delay and the unhelpful staff.': [W, S],
    'The system was down and I had to come back another day. Very disappointing.': [F, W],
    'Napakabagal ng proseso. Ang tagal naming naghintay.': [W],
    'Sobrang tagal ng pila at masungit ang staff.': [W, S],
    'Napakabilis ng pag-update ng MDR ko, wala pang 10 minutes tapos na.': [W, D],
    'Very organized ang pila, may number system na kaya hindi na magulo.': [W, F],
    'Magalang at matiyaga si kuya kahit ang dami kong tanong.': [S],
    'Inuna nila ang senior citizen at PWD, very considerate.': [S],
    'Sobrang tagal ng pila, 3 oras ako naghintay tapos walang paliwanag.': [W],
    'Hindi maayos ang serbisyo, bastos pa yung guard.': [S],
    'The staff was rude and did not answer my questions properly.': [S],
    'Pinabalik-balik ako ng tatlong beses dahil kulang daw ang requirements, hindi naman sinabi agad.': [D],
    'Walang upuan sa labas, nakatayo kami sa init ng ilang oras.': [F, W],
    'Offline daw ang system kaya hindi ako na-serve. Sayang ang pamasahe ko.': [F],
    'Ang sungit ng nasa window 2, sinigawan pa ako.': [S],
    'Very slow process. Only two windows open for so many clients.': [W],
    'Walang malinaw na instructions, hindi ko alam kung saan pipila.': [D, F],
    'Nag-lunch break lahat ng staff ng sabay, walang nag-aasikaso.': [S, W],
    'Disappointed. Pinaghintay kami tapos sinabing bumalik na lang bukas.': [W],
    'Hindi gumagana ang aircon, sobrang init sa loob.': [F],
    'Mali ang spelling ng pangalan ko sa ID, kailangan ko pang bumalik ulit.': [D],
    'Not helpful at all, they just kept passing me to another window.': [S],
    'Nag-update lang po ako ng MDR.': [D],
    'Nagbayad ng contribution para sa buwan na ito.': [D],
    'Paano po mag-register as voluntary member?': [D],
    'Sana po may online appointment para hindi na pumila.': [W, O],
    'Pa-follow up lang po ng status ng claim ko.': [D],
    'Changed my civil status from single to married.': [D],
    'Suggestion: maglagay ng signage kung saan ang bawat window.': [F],
    "Helpful si ma'am sa window 1, pero masungit yung sa window 3.": [S],
}

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
        auto_analysis = FeedbackConfiguration.get_solo().auto_analysis_enabled

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
            comment_sent = analyze_comment_sentiment(comment) if auto_analysis else FeedbackEntry.PENDING

            # Random timestamp within past `days`
            random_seconds = random.randint(0, days * 86400)
            created_at = now - timedelta(seconds=random_seconds)

            entry = FeedbackEntry(
                experience=exp,
                comment_sentiment=comment_sent,
                sentiment=combine_sentiment(exp, comment_sent),
                category=cat,
                topics=SAMPLE_TOPICS[comment],
                comment=comment,
                status=status,
            )
            # Save to generate tracking code
            entry.save()

            # Override created_at timestamp
            FeedbackEntry.objects.filter(pk=entry.pk).update(created_at=created_at, updated_at=created_at)
            created_entries += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully created {created_entries} sample feedback entries!'))
