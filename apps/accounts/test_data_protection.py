import os
from io import BytesIO, StringIO
from subprocess import CompletedProcess
from time import time
from unittest.mock import patch
from uuid import uuid4

import jwt
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import SecurityEvent, UserRole
from apps.applications.models import Application
from apps.companies.models import Company
from apps.jobs.models import Job
from apps.notifications.models import Notification, RecipientType
from apps.resumes.models import Resume, ResumeWork
from apps.resumes.services import extract_text_from_file, process_resume_and_calculate_match
from apps.resumes.tasks import enqueue_resume_processing, run_next_resume_work, scan_resume
from apps.resumes.upload_validation import validate_resume_format


class DataProtectionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Company A')
        self.job = Job.objects.create(company=self.company, title='QA Engineer')
        self.owner = User.objects.create_user(username=str(uuid4()))
        self.application = Application.objects.create(
            job=self.job, user_id=self.owner.username, first_name='Dina', last_name='Private',
            email='dina@example.test', phone='09171234567',
        )
        self.client = APIClient()

    def create_resume(self):
        return Resume.objects.create(
            application=self.application, original_filename='resume.txt', scan_passed=True,
            file=SimpleUploadedFile('resume.txt', b'Dina Private\ndina@example.test\n09171234567\nPython'),
        )

    @override_settings(DEBUG=False)
    def test_private_resume_requires_authentication_at_download(self):
        resume = self.create_resume()
        download = f'/api/resumes/{resume.pk}/download/'
        self.assertEqual(self.client.get(f'/media/{resume.file.name}').status_code, 404)
        self.assertEqual(self.client.get(download).status_code, 401)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertNotIn('Location', response)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(b''.join(response.streaming_content), b'Dina Private\ndina@example.test\n09171234567\nPython')
        response.close()
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(download).status_code, 401)
        self.assertEqual(self.client.get('/api/resume-file/old-ticket/').status_code, 404)

    @override_settings(REQUIRE_RESUME_SCAN=True)
    def test_unscanned_resume_cannot_be_downloaded(self):
        resume = self.create_resume()
        Resume.objects.filter(pk=resume.pk).update(scan_passed=False)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get(f'/api/resumes/{resume.pk}/download/').status_code, 409)

    def test_first_gemini_call_receives_redacted_text(self):
        self.create_resume()
        with patch('apps.resumes.services.analyze_resume_with_gemini', side_effect=RuntimeError('stop after capture')) as model:
            with self.assertRaisesMessage(RuntimeError, 'stop after capture'):
                process_resume_and_calculate_match(self.application)
        (prompt,), _ = model.call_args
        for identifier in ('Dina', 'Private', 'dina@example.test', '09171234567'):
            self.assertNotIn(identifier, prompt)
        self.assertIn('Python', prompt)

    def test_disguised_upload_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_resume_format(BytesIO(b'<html>not a PDF</html>'), 'resume.pdf')

    def test_pdf_is_parsed_in_the_child_process(self):
        import pymupdf

        with pymupdf.open() as document:
            document.new_page().insert_text((72, 72), 'Synthetic resume skill')
            content = document.tobytes()
        self.assertEqual(extract_text_from_file(content, 'resume.pdf'), 'Synthetic resume skill')

    @override_settings(REQUIRE_RESUME_SCAN=True)
    def test_scanner_detection_keeps_file_unapproved(self):
        resume = self.create_resume()
        resume.scan_passed = False
        resume.save(update_fields=['scan_passed'])
        with patch('apps.resumes.tasks.subprocess.run', return_value=CompletedProcess([], 1)):
            self.assertFalse(scan_resume(resume))
        resume.refresh_from_db()
        self.assertFalse(resume.scan_passed)

    def test_company_access_applies_to_reads_mutations_and_notifications(self):
        recruiter = User.objects.create_user(username=str(uuid4()))
        recruiter.role = 'recruiter'
        other_company = Company.objects.create(name='Company B')
        profile = UserRole.objects.create(user=recruiter, company=other_company, role='recruiter')
        resume = self.create_resume()
        notification = Notification.objects.create(
            job=self.job, application=self.application, recipient_type=RecipientType.RECRUITER,
            title='Applicant received', notification_type='NEW_APPLICATION',
        )
        self.client.force_authenticate(user=recruiter)
        app_path = f'/api/applications/{self.application.pk}/'
        self.assertEqual(self.client.get(app_path).status_code, 404)
        self.assertEqual(self.client.get(f'/api/resumes/{resume.pk}/download/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/jobs/{self.job.pk}/archive/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/notifications/{notification.pk}/read/').status_code, 404)
        self.client.post('/api/notifications/mark-all-read/', {'recipient_type': 'RECRUITER'}, format='json')
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)
        profile.company = self.company
        profile.save(update_fields=['company'])
        response = self.client.patch(app_path, {'user_id': str(uuid4()), 'notes': 'Reviewed'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(str(self.application.user_id), self.owner.username)

        response = self.client.patch(app_path, {'first_name': 'Changed', 'email': 'changed@example.test', 'phone': '1'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual((self.application.first_name, self.application.email, self.application.phone),
                         ('Dina', 'dina@example.test', '09171234567'))

        status_path = app_path + 'status/'
        with patch('apps.applications.views.create_status_change_notification', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.patch(status_path, {'status': 'SHORTLISTED'}, format='json')
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'SUBMITTED')
        self.assertFalse(SecurityEvent.objects.filter(action='application.status').exists())
        for _ in range(2):
            self.assertEqual(self.client.patch(status_path, {'status': 'SHORTLISTED'}, format='json').status_code, 200)
        self.assertEqual(SecurityEvent.objects.filter(action='application.status').count(), 1)
        self.assertEqual(Notification.objects.filter(notification_type='STATUS_UPDATE').count(), 1)

    def test_signed_in_caller_cannot_bypass_tracking_throttle(self):
        cache.clear()
        self.addCleanup(cache.clear)
        path = '/api/applications/track-status/'
        for _ in range(20):
            self.assertEqual(self.client.post(path, {'token': 'invalid'}, format='json').status_code, 400)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(path, {'token': 'invalid'}, format='json').status_code, 429)

    @override_settings(REQUIRE_RESUME_SCAN=False, EDGE_RATE_LIMIT_ENFORCED=False, USE_R2_STORAGE=False,
                       POSTGRES_RATE_LIMITING=False)
    def test_deploy_checks_report_security_errors_instead_of_crashing(self):
        ids = {issue.id for issue in run_checks(include_deployment_checks=True)}
        self.assertTrue({'talentmatch.E001', 'talentmatch.E003', 'talentmatch.E004'} <= ids)

    def test_failed_resume_work_is_retried_without_duplicate_queue_rows(self):
        enqueue_resume_processing(self.application.pk)
        enqueue_resume_processing(self.application.pk)
        work = ResumeWork.objects.get()
        with patch('apps.resumes.tasks.evaluate_resume', side_effect=OSError):
            self.assertTrue(run_next_resume_work())
        work.refresh_from_db()
        self.assertEqual(work.attempts, 1)
        self.assertGreater(work.run_after, timezone.now())
        work.run_after = timezone.now()
        work.save(update_fields=['run_after'])
        with patch('apps.resumes.tasks.evaluate_resume'):
            self.assertTrue(run_next_resume_work())
        self.assertFalse(ResumeWork.objects.exists())

    def test_rolled_back_deletion_preserves_storage_and_removes_queued_work(self):
        resume = self.create_resume()
        name = resume.file.name
        with transaction.atomic():
            resume.delete()
            self.assertTrue(ResumeWork.objects.filter(kind='delete', target=name).exists())
            transaction.set_rollback(True)
        self.assertTrue(resume.file.storage.exists(name))
        self.assertFalse(ResumeWork.objects.filter(kind='delete', target=name).exists())

    @override_settings(
        SUPABASE_URL='https://auth.invalid',
        SUPABASE_JWT_SECRET='local-synthetic-jwt-signing-key-32-bytes',
    )
    def test_password_only_recruiter_access_stays_company_scoped(self):
        from django.conf import settings
        recruiter_uuid = uuid4()
        UserRole.objects.create(supabase_user_id=recruiter_uuid, role='recruiter', company=self.company)
        claims = {
            'sub': str(recruiter_uuid), 'aud': 'authenticated', 'iss': 'https://auth.invalid/auth/v1',
            'email': 'recruiter@example.test', 'exp': int(time()) + 300,
        }
        token = jwt.encode({**claims, 'aal': 'aal1'}, settings.SUPABASE_JWT_SECRET, algorithm='HS256')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        path = f'/api/applications/{self.application.pk}/'
        self.assertEqual(self.client.get(path).status_code, 200)
        UserRole.objects.filter(supabase_user_id=recruiter_uuid).update(company=Company.objects.create(name='Company B'))
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/api/platform/companies/').status_code, 403)


@override_settings(SUPABASE_URL='https://auditproject.supabase.co')
class SignupSecurityCommandTests(TestCase):
    @patch.dict(os.environ, {'SUPABASE_ACCESS_TOKEN': 'synthetic-management-token'})
    @patch('apps.accounts.management.commands.check_signup_security.requests.request')
    def test_check_preserves_stricter_existing_auth_email_limit(self, request):
        request.return_value.status_code = 200
        request.return_value.json.return_value = {
            'mailer_autoconfirm': False, 'mailer_allow_unverified_email_sign_ins': False,
            'external_anonymous_users_enabled': False, 'rate_limit_email_sent': 2,
        }
        call_command('check_signup_security', max_auth_emails_per_hour=30, apply=True, stdout=StringIO())
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET'])

    @patch.dict(os.environ, {'SUPABASE_ACCESS_TOKEN': 'synthetic-management-token'})
    @patch('apps.accounts.management.commands.check_signup_security.requests.request')
    def test_apply_caps_the_email_quota_and_rechecks_the_provider(self, request):
        request.return_value.status_code = 200
        expected = {
            'mailer_autoconfirm': False, 'mailer_allow_unverified_email_sign_ins': False,
            'external_anonymous_users_enabled': False, 'rate_limit_email_sent': 30,
        }
        request.return_value.json.side_effect = [{'rate_limit_email_sent': 100}, {}, expected]
        call_command('check_signup_security', max_auth_emails_per_hour=30, apply=True, stdout=StringIO())
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'PATCH', 'GET'])
        self.assertEqual(request.call_args_list[1].kwargs['json'], expected)

    @patch.dict(os.environ, {'SUPABASE_ACCESS_TOKEN': 'synthetic-management-token'})
    @patch('apps.accounts.management.commands.check_signup_security.requests.request')
    def test_read_only_check_does_not_change_hosted_settings(self, request):
        request.return_value.status_code = 200
        request.return_value.json.return_value = {'mailer_autoconfirm': True, 'rate_limit_email_sent': 100}
        with self.assertRaisesMessage(CommandError, 'Auth settings require changes'):
            call_command('check_signup_security', max_auth_emails_per_hour=30)
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET'])
