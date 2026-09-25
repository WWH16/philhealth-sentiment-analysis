from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from feedback.models import FeedbackConfiguration


class SentimentSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_settings_page_uses_persisted_toggle_state(self):
        config = FeedbackConfiguration.get_solo()
        config.auto_analysis_enabled = False
        config.save()

        response = self.client.get(reverse('settings_page') + '#sentiment-analysis')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="autoAnalysisToggle"')
        self.assertNotContains(response, 'id="autoAnalysisToggle"\n                      name="auto_analysis_enabled"\n                      checked', html=False)

    def test_update_sentiment_settings_persists_toggle(self):
        response = self.client.post(
            reverse('update_sentiment_settings'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['auto_analysis_enabled'])
        self.assertFalse(FeedbackConfiguration.get_solo().auto_analysis_enabled)

        response = self.client.post(
            reverse('update_sentiment_settings'),
            {'auto_analysis_enabled': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['auto_analysis_enabled'])
        self.assertTrue(FeedbackConfiguration.get_solo().auto_analysis_enabled)


class SurveySettingsAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_settings_page_uses_persisted_survey_availability(self):
        config = FeedbackConfiguration.get_solo()
        config.survey_enabled = False
        config.survey_offline_message = 'Maintenance ongoing.'
        config.save()

        response = self.client.get(reverse('settings_page') + '#survey-settings')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="surveyEnabledToggle"')
        self.assertContains(response, 'Maintenance ongoing.')
        self.assertContains(response, 'Form Paused')

    def test_update_survey_settings_disables_and_enables(self):
        # Disable survey
        response = self.client.post(
            reverse('update_survey_settings'),
            {'survey_offline_message': 'Temporary pause.'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['survey_enabled'])
        self.assertEqual(payload['survey_offline_message'], 'Temporary pause.')
        self.assertFalse(FeedbackConfiguration.get_solo().survey_enabled)
        self.assertEqual(FeedbackConfiguration.get_solo().survey_offline_message, 'Temporary pause.')

        # Re-enable survey
        response = self.client.post(
            reverse('update_survey_settings'),
            {
                'survey_enabled': 'on',
                'survey_offline_message': 'System is active.',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['survey_enabled'])
        self.assertTrue(FeedbackConfiguration.get_solo().survey_enabled)


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_dashboard_view_renders_successfully(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            category='compliment',
            comment='Great service!',
        )
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.AGREE,
            category='suggestion',
            comment='Smooth process.',
        )

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('filter_data', response.context)
        self.assertEqual(response.context['total'], 2)
        self.assertEqual(response.context['strongly_agree'], 1)
        self.assertEqual(response.context['agree'], 1)
        self.assertEqual(response.context['disagree'], 0)
        self.assertEqual(response.context['filter_data']['all']['total'], 2)
        self.assertContains(response, 'id="nav-dashboard"')
        self.assertContains(response, 'nav-item active')


class SentimentAnalysisViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_sentiment_analysis_view_renders_successfully(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            sentiment=FeedbackEntry.POSITIVE,
            comment='Exemplary assistance.',
        )
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.DISAGREE,
            sentiment=FeedbackEntry.NEGATIVE,
            comment='Delayed processing.',
        )

        response = self.client.get(reverse('sentiment_analysis'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 2)
        self.assertEqual(response.context['positive'], 1)
        self.assertEqual(response.context['neutral'], 0)
        self.assertEqual(response.context['negative'], 1)
        self.assertContains(response, 'nav-item active')


class ReportsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_reports_view_renders_successfully(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            category='compliment',
            comment='Superb staff responsiveness.',
        )

        response = self.client.get(reverse('reports'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('report_json', response.context)
        report_data = response.context['report_json']
        self.assertIn('daily', report_data)
        self.assertIn('weekly', report_data)
        self.assertIn('monthly', report_data)
        self.assertIn('quarterly', report_data)
        self.assertIn('annual', report_data)
        self.assertEqual(report_data['daily']['strongly_agree'], 1)
        self.assertContains(response, 'nav-item active')


class ResponsesViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_responses_view_renders_successfully(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            category='compliment',
            comment='Fast transaction.',
        )

        response = self.client.get(reverse('responses'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['strongly_agree'], 1)
        self.assertEqual(len(response.context['entries_data']), 1)
        self.assertContains(response, 'nav-item active')

    def test_responses_delete_single(self):
        from feedback.models import FeedbackEntry
        from django.contrib.admin.models import LogEntry, DELETION
        e1 = FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            category='compliment',
            comment='Response 1',
        )
        e2 = FeedbackEntry.objects.create(
            experience=FeedbackEntry.AGREE,
            category='suggestion',
            comment='Response 2',
        )

        response = self.client.post(
            reverse('responses_delete'),
            data={'ids': [e1.id]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['deleted_count'], 1)
        self.assertEqual(data['deleted_ids'], [e1.id])
        self.assertEqual(data['counts']['total'], 1)
        self.assertEqual(data['counts']['strongly_agree'], 0)
        self.assertEqual(data['counts']['agree'], 1)

        self.assertFalse(FeedbackEntry.objects.filter(id=e1.id).exists())
        self.assertTrue(FeedbackEntry.objects.filter(id=e2.id).exists())

        # Verify admin audit log
        log = LogEntry.objects.filter(object_id=str(e1.id), action_flag=DELETION).first()
        self.assertIsNotNone(log)
        self.assertIn(f'Deleted feedback response #{e1.id}', log.change_message)

    def test_responses_delete_multiple(self):
        from feedback.models import FeedbackEntry
        e1 = FeedbackEntry.objects.create(experience=FeedbackEntry.STRONGLY_AGREE)
        e2 = FeedbackEntry.objects.create(experience=FeedbackEntry.DISAGREE)
        e3 = FeedbackEntry.objects.create(experience=FeedbackEntry.NEITHER)

        response = self.client.post(
            reverse('responses_delete'),
            data={'ids': [e1.id, e2.id]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['deleted_count'], 2)
        self.assertEqual(set(data['deleted_ids']), {e1.id, e2.id})
        self.assertEqual(data['counts']['total'], 1)
        self.assertEqual(data['counts']['neither'], 1)

        self.assertFalse(FeedbackEntry.objects.filter(id__in=[e1.id, e2.id]).exists())
        self.assertTrue(FeedbackEntry.objects.filter(id=e3.id).exists())

    def test_responses_delete_invalid_payload(self):
        # Empty IDs list
        res1 = self.client.post(
            reverse('responses_delete'),
            data={'ids': []},
            content_type='application/json',
        )
        self.assertEqual(res1.status_code, 400)

        # Non-integer IDs
        res2 = self.client.post(
            reverse('responses_delete'),
            data={'ids': ['invalid']},
            content_type='application/json',
        )
        self.assertEqual(res2.status_code, 400)

        # Non-existent ID
        res3 = self.client.post(
            reverse('responses_delete'),
            data={'ids': [999999]},
            content_type='application/json',
        )
        self.assertEqual(res3.status_code, 404)

    def test_responses_delete_requires_post(self):
        response = self.client.get(reverse('responses_delete'))
        self.assertEqual(response.status_code, 405)

    def test_responses_delete_requires_staff(self):
        regular_user = User.objects.create_user(
            username='citizen',
            password='password123',
            is_staff=False,
        )
        self.client.force_login(regular_user)
        response = self.client.post(
            reverse('responses_delete'),
            data={'ids': [1]},
            content_type='application/json',
        )
        # staff_required redirects non-staff to admin_login
        self.assertEqual(response.status_code, 302)

    def test_responses_count(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.create(experience=FeedbackEntry.STRONGLY_AGREE)
        FeedbackEntry.objects.create(experience=FeedbackEntry.AGREE)

        response = self.client.get(reverse('responses_count'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['count'], 2)


class ActivityLogViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_activity_log_view_renders_successfully(self):
        response = self.client.get(reverse('activity_log'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('logs_data', response.context)
        self.assertContains(response, 'nav-item active')


class UsersViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_users_view_renders_successfully(self):
        response = self.client.get(reverse('users'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_users'], 1)
        self.assertEqual(response.context['active_users'], 1)
        self.assertEqual(response.context['staff_users'], 1)
        self.assertContains(response, 'nav-item active')

    def test_self_password_update_requires_current_password(self):
        response = self.client.post(
            reverse('user_edit', args=[self.user.id]),
            {
                'username': self.user.username,
                'email': self.user.email,
                'password1': 'newpassword12345',
                'password2': 'newpassword12345',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('Current password is required', data['error'])

    def test_self_password_update_rejects_wrong_current_password(self):
        response = self.client.post(
            reverse('user_edit', args=[self.user.id]),
            {
                'username': self.user.username,
                'email': self.user.email,
                'current_password': 'wrongpassword',
                'password1': 'newpassword12345',
                'password2': 'newpassword12345',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('Current password is incorrect', data['error'])

    def test_self_password_update_succeeds_with_correct_current_password(self):
        response = self.client.post(
            reverse('user_edit', args=[self.user.id]),
            {
                'username': self.user.username,
                'email': self.user.email,
                'current_password': 'password123',
                'password1': 'newpassword12345',
                'password2': 'newpassword12345',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword12345'))

    def test_admin_reset_other_user_password_does_not_require_current_password(self):
        other_user = User.objects.create_user(
            username='staff_member',
            password='oldpassword123',
            is_staff=True,
        )
        response = self.client.post(
            reverse('user_edit', args=[other_user.id]),
            {
                'username': other_user.username,
                'email': other_user.email,
                'password1': 'resetpassword12345',
                'password2': 'resetpassword12345',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        other_user.refresh_from_db()
        self.assertTrue(other_user.check_password('resetpassword12345'))


class RoleBasedAccessControlTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='staff_member',
            password='password123',
            is_active=True,
            is_staff=True,
            is_superuser=False,
        )
        self.superuser = User.objects.create_user(
            username='superuser_admin',
            password='password123',
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.regular_user = User.objects.create_user(
            username='regular_juan',
            password='password123',
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        self.inactive_user = User.objects.create_user(
            username='inactive_pedro',
            password='password123',
            is_active=False,
            is_staff=True,
            is_superuser=False,
        )

    def test_staff_can_access_core_operations(self):
        self.client.force_login(self.staff_user)

        # Dashboard, Responses, and Reports
        res_dash = self.client.get(reverse('dashboard'))
        self.assertEqual(res_dash.status_code, 200)

        res_resp = self.client.get(reverse('responses'))
        self.assertEqual(res_resp.status_code, 200)

        res_rep = self.client.get(reverse('reports'))
        self.assertEqual(res_rep.status_code, 200)

    def test_staff_is_blocked_from_superuser_views(self):
        self.client.force_login(self.staff_user)

        for route_name in ['users', 'settings_page', 'activity_log', 'sentiment_analysis']:
            response = self.client.get(reverse(route_name), follow=True)
            self.assertRedirects(response, reverse('dashboard'))
            self.assertContains(response, 'Access restricted to administrators.')

    def test_regular_user_cannot_login(self):
        response = self.client.post(
            reverse('admin_login'),
            {'username': 'regular_juan', 'password': 'password123'},
            follow=True,
        )
        self.assertContains(response, 'Access denied. Staff privileges are required.')
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_inactive_user_login_shows_deactivated_message(self):
        response = self.client.post(
            reverse('admin_login'),
            {'username': 'inactive_pedro', 'password': 'password123'},
            follow=True,
        )
        self.assertContains(response, 'This account has been deactivated. Please contact your system administrator.')
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_invalid_password_shows_generic_error(self):
        response = self.client.post(
            reverse('admin_login'),
            {'username': 'staff_member', 'password': 'wrongpassword'},
            follow=True,
        )
        self.assertContains(response, 'Invalid username or password. Please check your credentials and try again.')
        self.assertFalse('_auth_user_id' in self.client.session)


class DatabaseBackupTests(TransactionTestCase):
    def setUp(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.all().delete()
        self.superuser = User.objects.create_user(
            username='super_admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.staff_user = User.objects.create_user(
            username='staff_member',
            password='password123',
            is_staff=True,
            is_superuser=False,
        )
        self.client.force_login(self.superuser)

    def tearDown(self):
        from feedback.models import FeedbackEntry
        FeedbackEntry.objects.all().delete()
        super().tearDown()

    def test_create_and_list_backup(self):
        from feedback_admin.backup_utils import create_backup, list_backups, delete_backup, BACKUP_FILENAME_RE
        result = create_backup()
        self.assertTrue(BACKUP_FILENAME_RE.match(result['filename']))
        self.assertTrue(result['filename'].endswith('.json'))

        backups = list_backups()
        filenames = [b['filename'] for b in backups]
        self.assertIn(result['filename'], filenames)

        # Clean up
        delete_backup(result['filename'])

    def test_backup_create_view_and_download_delete(self):
        from feedback_admin.backup_utils import delete_backup, resolve_backup_path
        res = self.client.post(reverse('backup_create'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        filename = data['message'].replace('Backup created: ', '').strip()

        # Download
        res_dl = self.client.get(reverse('backup_download', args=[filename]))
        self.assertEqual(res_dl.status_code, 200)
        res_dl.close()

        # Delete
        res_del = self.client.post(reverse('backup_delete', args=[filename]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.json()['ok'])

    def test_backup_restore_json_flow(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from feedback.models import FeedbackEntry
        from feedback_admin.backup_utils import create_backup, resolve_backup_path, delete_backup

        entry1 = FeedbackEntry.objects.create(
            experience=FeedbackEntry.STRONGLY_AGREE,
            comment='Original entry before backup',
        )

        backup_res = create_backup()
        backup_file_path = resolve_backup_path(backup_res['filename'])

        # Add a new entry after backup
        entry2 = FeedbackEntry.objects.create(
            experience=FeedbackEntry.DISAGREE,
            comment='Entry created after backup',
        )
        self.assertEqual(FeedbackEntry.objects.count(), 2)

        with open(backup_file_path, 'rb') as f:
            uploaded = SimpleUploadedFile(backup_res['filename'], f.read(), content_type='application/json')

        res_restore = self.client.post(
            reverse('backup_restore'),
            {'backup_file': uploaded},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(res_restore.status_code, 200)
        self.assertTrue(res_restore.json()['ok'])

        # The entry created after backup should be gone, only original restored
        self.assertEqual(FeedbackEntry.objects.count(), 1)
        self.assertEqual(FeedbackEntry.objects.first().comment, 'Original entry before backup')

        # Clean up
        delete_backup(backup_res['filename'])
        delete_backup(res_restore.json()['safety_backup']['filename'])

    def test_staff_cannot_create_or_restore_backup(self):
        self.client.force_login(self.staff_user)

        res_create = self.client.post(reverse('backup_create'))
        self.assertEqual(res_create.status_code, 302)  # Redirected by superuser_required

        res_restore = self.client.post(reverse('backup_restore'))
        self.assertEqual(res_restore.status_code, 302)

    def test_get_backup_dir_fallback_when_readonly(self):
        import tempfile
        from unittest.mock import patch
        from pathlib import Path
        from feedback_admin.backup_utils import get_backup_dir

        with patch.object(Path, 'touch', side_effect=PermissionError('Read-only file system')):
            backup_dir = get_backup_dir()
            self.assertEqual(backup_dir, Path(tempfile.gettempdir()) / 'backups')

    def test_backup_restore_existing_file_flow(self):
        from feedback.models import FeedbackEntry
        from feedback_admin.backup_utils import create_backup, delete_backup

        FeedbackEntry.objects.create(
            experience=FeedbackEntry.AGREE,
            comment='Pre-existing backup entry',
        )

        backup_res = create_backup()
        fn = backup_res['filename']

        # Add another entry
        FeedbackEntry.objects.create(
            experience=FeedbackEntry.DISAGREE,
            comment='Should disappear after restore',
        )
        self.assertEqual(FeedbackEntry.objects.count(), 2)

        # 1-click restore using existing_filename
        res = self.client.post(
            reverse('backup_restore'),
            {'existing_filename': fn},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertIn('safety_backup', data)
        self.assertEqual(data.get('feedback_count'), 1)

        # Verify only original entry exists
        self.assertEqual(FeedbackEntry.objects.count(), 1)
        self.assertEqual(FeedbackEntry.objects.first().comment, 'Pre-existing backup entry')

        # Clean up
        delete_backup(fn)
        delete_backup(data['safety_backup']['filename'])
