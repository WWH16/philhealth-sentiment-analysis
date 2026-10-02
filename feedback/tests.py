import json
from unittest.mock import patch

from django.test import TestCase

from .models import FeedbackConfiguration, FeedbackEntry, FeedbackToken


class SubmitFeedbackAutoAnalysisTests(TestCase):
    def _submit(self, payload):
        return self.client.post(
            '/feedback/submit/',
            data=json.dumps({'token': FeedbackToken.issue('1', None).key, **payload}),
            content_type='application/json',
        )

    @patch('feedback.views.analyze_comment_sentiment', return_value=FeedbackEntry.POSITIVE)
    def test_submit_feedback_runs_analysis_when_enabled(self, mocked_analyze):
        config = FeedbackConfiguration.get_solo()
        config.auto_analysis_enabled = True
        config.save()

        response = self._submit({
            'experience': FeedbackEntry.VERY_SATISFACTORY,
            'comment': 'Excellent service.',
        })

        self.assertEqual(response.status_code, 201)
        entry = FeedbackEntry.objects.get()
        self.assertEqual(entry.sentiment, FeedbackEntry.POSITIVE)
        mocked_analyze.assert_called_once_with('Excellent service.')

    @patch('feedback.views.analyze_comment_sentiment', return_value=FeedbackEntry.POSITIVE)
    def test_submit_feedback_skips_analysis_when_disabled(self, mocked_analyze):
        config = FeedbackConfiguration.get_solo()
        config.auto_analysis_enabled = False
        config.save()

        response = self._submit({
            'experience': FeedbackEntry.SATISFACTORY,
            'comment': 'Please improve wait times.',
        })

        self.assertEqual(response.status_code, 201)
        entry = FeedbackEntry.objects.get()
        self.assertEqual(entry.sentiment, FeedbackEntry.PENDING)
        mocked_analyze.assert_not_called()

    @patch('feedback.views.analyze_comment_sentiment', return_value=FeedbackEntry.POSITIVE)
    def test_unsatisfactory_rating_turns_positive_comment_neutral(self, _mocked_analyze):
        config = FeedbackConfiguration.get_solo()
        config.auto_analysis_enabled = True
        config.save()

        response = self._submit({
            'experience': FeedbackEntry.UNSATISFACTORY,
            'comment': 'Thank you po.',
        })

        self.assertEqual(response.status_code, 201)
        entry = FeedbackEntry.objects.get()
        self.assertEqual(entry.comment_sentiment, FeedbackEntry.POSITIVE)
        self.assertEqual(entry.sentiment, FeedbackEntry.NEUTRAL)

    def test_submit_feedback_requires_comment(self):
        for comment in ('', '   ', None):
            response = self._submit({
                'experience': FeedbackEntry.VERY_SATISFACTORY,
                'comment': comment,
            })
            self.assertEqual(response.status_code, 400)
        self.assertEqual(FeedbackEntry.objects.count(), 0)

    def test_submit_short_form_without_staff_when_active_staff_exist(self):
        from django.contrib.auth.models import User
        User.objects.create_user(
            username='staff_test',
            first_name='Test',
            last_name='Staff',
            is_staff=True,
            is_active=True,
            is_superuser=False,
        )
        response = self._submit({
            'experience': FeedbackEntry.UNSATISFACTORY,
            'comment': 'Mahaba ang pila.',
        })
        self.assertEqual(response.status_code, 201)
        entry = FeedbackEntry.objects.get()
        self.assertEqual(entry.experience, FeedbackEntry.UNSATISFACTORY)
        self.assertEqual(entry.comment, 'Mahaba ang pila.')

    def test_submit_limits_comment_to_1000_characters(self):
        response = self._submit({
            'experience': FeedbackEntry.SATISFACTORY,
            'comment': 'a' * 1000,
        })
        self.assertEqual(response.status_code, 201)
        response = self._submit({
            'experience': FeedbackEntry.SATISFACTORY,
            'comment': 'a' * 1001,
        })
        self.assertEqual(response.status_code, 400)


    def test_submit_rejects_rating_outside_three_point_scale(self):
        for payload in ({'experience': 'strongly_agree'}, {'experience': 3}, {'experience': []}, {}):
            response = self._submit(payload)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(FeedbackEntry.objects.count(), 0)


class SentimentServiceTests(TestCase):
    def test_seed_comments_read_as_their_intended_sentiment(self):
        from .management.commands.seed_feedback_data import SAMPLE_FEEDBACK
        from .services import _get_model, analyze_comment_sentiment

        if _get_model() is None:
            self.skipTest('Sentiment model file is not present (it is gitignored).')
        wrong = [
            (group['sentiment'], analyze_comment_sentiment(comment), comment)
            for group in SAMPLE_FEEDBACK
            for comment in group['comments']
            if analyze_comment_sentiment(comment) != group['sentiment']
        ]
        self.assertEqual(wrong, [])

    def test_preprocess_splits_on_punctuation_and_keeps_negations(self):
        from .services import _preprocess_light
        tokens = _preprocess_light('Nag-update lang po, 3hrs!!').split()
        self.assertIn('nag', tokens)
        self.assertIn('updat', tokens)
        tokens = _preprocess_light('hindi ako satisfied, not good').split()
        self.assertIn('hindi', tokens)
        self.assertIn('not', tokens)

    def test_analyze_comment_with_form_headers(self):
        from .services import analyze_comment_sentiment
        res = analyze_comment_sentiment('Comments: The staff was very helpful and accommodating.')
        self.assertIn(res, [FeedbackEntry.POSITIVE, FeedbackEntry.NEUTRAL])

    def test_analyze_comment_empty_returns_not_applicable(self):
        from .services import analyze_comment_sentiment
        self.assertEqual(analyze_comment_sentiment(''), FeedbackEntry.NOT_APPLICABLE)
        self.assertEqual(analyze_comment_sentiment(None), FeedbackEntry.NOT_APPLICABLE)
        self.assertEqual(analyze_comment_sentiment('   '), FeedbackEntry.NOT_APPLICABLE)

    def test_entry_to_row_empty_comment_displays_na(self):
        from feedback_admin.views import _entry_to_row
        entry = FeedbackEntry.objects.create(
            experience=FeedbackEntry.SATISFACTORY,
            sentiment=FeedbackEntry.PENDING,
            comment='',
        )
        row = _entry_to_row(entry)
        self.assertEqual(row['sentiment'], 'N/A')
        self.assertEqual(row['sentiment_value'], FeedbackEntry.NOT_APPLICABLE)


    def test_combine_sentiment_applies_rating_to_comment(self):
        from .services import combine_sentiment
        E = FeedbackEntry
        expected = {
            E.VERY_SATISFACTORY: {E.POSITIVE: E.POSITIVE, E.NEUTRAL: E.NEUTRAL, E.NEGATIVE: E.NEGATIVE},
            E.SATISFACTORY: {E.POSITIVE: E.POSITIVE, E.NEUTRAL: E.NEUTRAL, E.NEGATIVE: E.NEGATIVE},
            E.UNSATISFACTORY: {E.POSITIVE: E.NEUTRAL, E.NEUTRAL: E.NEGATIVE, E.NEGATIVE: E.NEGATIVE},
        }
        for rating, by_comment in expected.items():
            for comment_sentiment, final in by_comment.items():
                with self.subTest(rating=rating, comment=comment_sentiment):
                    self.assertEqual(combine_sentiment(rating, comment_sentiment), final)

    def test_combine_sentiment_never_derives_from_rating_alone(self):
        from .services import combine_sentiment
        for rating, _ in FeedbackEntry.EXPERIENCE_CHOICES:
            for unresolved in (FeedbackEntry.PENDING, FeedbackEntry.NOT_APPLICABLE):
                with self.subTest(rating=rating, comment=unresolved):
                    self.assertEqual(combine_sentiment(rating, unresolved), unresolved)

    @patch('feedback.services.analyze_comment_sentiment', return_value=FeedbackEntry.NEUTRAL)
    def test_reanalyze_saves_comment_and_final_sentiment(self, _mocked_analyze):
        from .services import reanalyze_pending_entries
        entry = FeedbackEntry.objects.create(
            experience=FeedbackEntry.UNSATISFACTORY,
            comment='Kumuha ng ID.',
        )
        reanalyze_pending_entries()
        entry.refresh_from_db()
        self.assertEqual(entry.comment_sentiment, FeedbackEntry.NEUTRAL)
        self.assertEqual(entry.sentiment, FeedbackEntry.NEGATIVE)

    def test_entry_to_row_shows_comment_sentiment_only_when_adjusted(self):
        from feedback_admin.views import _entry_to_row
        adjusted = FeedbackEntry.objects.create(
            experience=FeedbackEntry.UNSATISFACTORY,
            comment_sentiment=FeedbackEntry.POSITIVE,
            sentiment=FeedbackEntry.NEUTRAL,
            comment='Salamat po.',
        )
        unchanged = FeedbackEntry.objects.create(
            experience=FeedbackEntry.SATISFACTORY,
            comment_sentiment=FeedbackEntry.POSITIVE,
            sentiment=FeedbackEntry.POSITIVE,
            comment='Salamat po.',
        )
        self.assertEqual(_entry_to_row(adjusted)['comment_sentiment'], 'Positive')
        self.assertIsNone(_entry_to_row(unchanged)['comment_sentiment'])
        # The CSV column always carries the comment's own reading.
        self.assertEqual(_entry_to_row(unchanged)['comment_sentiment_label'], 'Positive')

    @patch('feedback.services._get_model', return_value=None)
    def test_sentiment_does_not_fall_back_to_rating(self, _mocked_model):
        from .services import analyze_comment_sentiment
        # Without a usable model the comment stays unanalyzed; the rating is never used.
        self.assertEqual(analyze_comment_sentiment('Mabilis ang serbisyo.'), FeedbackEntry.PENDING)


class DailySummaryEmailTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.today = timezone.localtime().date()
        self.config = FeedbackConfiguration.get_solo()
        self.config.daily_summary_enabled = True
        self.config.notification_email = 'supervisor@philhealth.gov.ph'
        self.config.save()

    def test_metrics_calculation(self):
        from .email_service import get_daily_summary_metrics
        # Create test feedback entries
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.VERY_SATISFACTORY,
            sentiment=FeedbackEntry.POSITIVE,
            category=FeedbackEntry.COMPLIMENT,
            comment='Great service at Window 2.'
        )
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.SATISFACTORY,
            sentiment=FeedbackEntry.POSITIVE,
            category=FeedbackEntry.SUGGESTION,
            comment='Smooth transaction.'
        )
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.UNSATISFACTORY,
            sentiment=FeedbackEntry.NEGATIVE,
            category=FeedbackEntry.COMPLAINT,
            comment='Long waiting queue.'
        )

        metrics = get_daily_summary_metrics(self.today)
        self.assertEqual(metrics['total_count'], 3)
        # (2 Very Satisfactory/Satisfactory ratings out of 3) = 67%
        self.assertEqual(metrics['satisfaction_rate'], 67)
        self.assertEqual(metrics['pos_count'], 2)
        self.assertEqual(metrics['neg_count'], 1)
        self.assertEqual(metrics['categories']['complaints'], 1)
        self.assertEqual(len(metrics['flagged_items']), 1)
        self.assertIn('Long waiting queue', metrics['flagged_items'][0]['comment'])

    def test_zero_count_allowed(self):
        from .email_service import send_daily_summary_email
        from django.core import mail
        # When count is 0, dispatch proceeds and sends the summary of 0 submissions
        result = send_daily_summary_email(
            target_date=self.today,
            recipient_email='admin@philhealth.gov.ph',
            force=True,
            is_test=False
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['metrics']['total_count'], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_send_test_email(self):
        from .email_service import send_daily_summary_email
        from django.core import mail
        # Force dispatch in test mode
        result = send_daily_summary_email(
            target_date=self.today,
            recipient_email='testadmin@philhealth.gov.ph',
            force=True,
            is_test=True
        )
        self.assertTrue(result['ok'])
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn('[TEST]', sent_email.subject)
        self.assertIn('testadmin@philhealth.gov.ph', sent_email.to)

    def test_dynamic_base_url_vercel(self):
        import os
        from .email_service import send_daily_summary_email
        from django.core import mail

        os.environ['VERCEL_URL'] = 'philhealth-sentiment-analysis.vercel.app'
        try:
            result = send_daily_summary_email(
                target_date=self.today,
                recipient_email='admin@philhealth.gov.ph',
                force=True
            )
            self.assertTrue(result['ok'])
            self.assertEqual(len(mail.outbox), 1)
            sent_email = mail.outbox[0]
            # Check html content has vercel https url
            html_content = sent_email.alternatives[0][0]
            self.assertIn('https://philhealth-sentiment-analysis.vercel.app/dashboard/', html_content)
        finally:
            os.environ.pop('VERCEL_URL', None)

    def test_cron_daily_summary_view(self):
        from django.test import Client
        client = Client()
        # Test endpoint
        response = client.get('/api/cron/daily-summary/?force=true')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('ok'))
        self.assertEqual(data.get('recipient'), 'supervisor@philhealth.gov.ph')

    def test_cron_daily_summary_with_secret(self):
        from django.test import Client, override_settings
        client = Client()
        with override_settings(CRON_SECRET='my-secret-token'):
            # Without secret -> 401
            res_fail = client.get('/api/cron/daily-summary/')
            self.assertEqual(res_fail.status_code, 401)

            # With query param secret -> 200
            res_param = client.get('/api/cron/daily-summary/?secret=my-secret-token&force=true')
            self.assertEqual(res_param.status_code, 200)
            self.assertTrue(res_param.json().get('ok'))

            # With Bearer header secret -> 200
            res_header = client.get(
                '/api/cron/daily-summary/?force=true',
                HTTP_AUTHORIZATION='Bearer my-secret-token'
            )
            self.assertEqual(res_header.status_code, 200)
            self.assertTrue(res_header.json().get('ok'))


class SurveyAvailabilityTests(TestCase):
    def setUp(self):
        self.config = FeedbackConfiguration.get_solo()
        self.config.survey_enabled = True
        self.config.survey_offline_message = 'Custom offline notice for testing.'
        self.config.save()

    def test_form_is_served_at_root_and_old_address_redirects(self):
        response = self.client.get('/feedback/')
        self.assertRedirects(response, '/')

    def test_index_renders_form_when_survey_enabled(self):
        response = self.client.get('/?t=' + FeedbackToken.issue('23', None).key)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['survey_enabled'])
        self.assertContains(response, 'id="feedbackForm"')
        self.assertContains(response, 'name="experience"', count=3)
        self.assertNotContains(response, 'The online form is paused right now')

    def test_index_renders_offline_notice_when_survey_disabled(self):
        self.config.survey_enabled = False
        self.config.save()

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['survey_enabled'])
        self.assertContains(response, 'The online form is paused right now')
        self.assertContains(response, 'Custom offline notice for testing.')
        self.assertNotContains(response, 'id="feedbackForm"')

    def test_submit_feedback_blocked_when_survey_disabled(self):
        self.config.survey_enabled = False
        self.config.save()

        response = self.client.post(
            '/feedback/submit/',
            data=json.dumps({
                'experience': FeedbackEntry.SATISFACTORY,
                'comment': 'Trying to submit while offline.',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertTrue(data.get('survey_disabled'))
        self.assertEqual(data['error'], 'Custom offline notice for testing.')
        self.assertEqual(FeedbackEntry.objects.count(), 0)

    def test_submit_feedback_allowed_when_survey_enabled(self):
        self.config.survey_enabled = True
        self.config.save()

        response = self.client.post(
            '/feedback/submit/',
            data=json.dumps({
                'token': FeedbackToken.issue('1', None).key,
                'experience': FeedbackEntry.VERY_SATISFACTORY,
                'comment': 'Submitting while active.',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(FeedbackEntry.objects.count(), 1)

