from datetime import timedelta
from time import time
from unittest.mock import patch
from uuid import uuid4

import jwt
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import SecurityEvent, StaffInvitation, UserRole
from apps.applications.models import Application
from apps.companies.models import Company
from apps.jobs.models import Job


class PlatformManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='platform-admin')
        self.admin.role = 'superadmin'
        self.company = Company.objects.create(name='Talent Operations')
        self.companies_url = '/api/platform/companies/'
        self.staff_url = '/api/platform/staff/'

    def test_recruiters_cannot_manage_companies_or_staff(self):
        recruiter = User.objects.create_user(username='recruiter')
        recruiter.role = 'recruiter'
        UserRole.objects.create(user=recruiter, company=self.company, role='recruiter')
        self.client.force_authenticate(recruiter)
        self.assertEqual(self.client.get(self.companies_url).status_code, 403)
        self.assertEqual(self.client.post(self.staff_url, {}, format='json').status_code, 403)

    def test_admin_can_create_and_update_company_with_audit(self):
        self.client.force_authenticate(self.admin)
        created = self.client.post(self.companies_url, {'name': 'Engineering Partners'}, format='json')
        self.assertEqual(created.status_code, 201)
        updated = self.client.patch(
            self.companies_url + f"{created.data['id']}/", {'name': 'Hiring Partners'}, format='json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['name'], 'Hiring Partners')
        self.assertEqual(updated['Cache-Control'], 'private, no-store')
        self.assertEqual(SecurityEvent.objects.filter(action__startswith='company.').count(), 2)

    def test_superadmin_has_management_access_and_cannot_view_or_change_hiring_records(self):
        self.client.force_authenticate(self.admin)
        job = Job.objects.create(company=self.company, title='Private draft', status='CLOSED')
        application = Application.objects.create(job=job, user_id=uuid4(), email='candidate@example.test')
        self.assertEqual(self.client.get(self.companies_url).status_code, 200)
        self.assertEqual(self.client.get(f'/api/jobs/{job.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/jobs/{job.pk}/report/', {'file_type': 'pdf'}).status_code, 403)
        self.assertEqual(self.client.patch(f'/api/applications/{application.pk}/status/', {'status': 'HIRED'}).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/applications/{application.pk}/').status_code, 405)
        self.assertTrue(Application.objects.filter(pk=application.pk, status='SUBMITTED').exists())

    @patch('apps.companies.management_views.create_staff_identity')
    def test_admin_can_create_recruiter_directly(self, create_identity):
        identity = uuid4()
        create_identity.return_value = identity
        self.client.force_authenticate(self.admin)
        payload = {
            'first_name': 'Alex', 'last_name': 'Santos', 'email': 'staff@example.test',
            'password': 'Reach-Green-River-147', 'role': 'recruiter', 'company': self.company.pk,
        }
        response = self.client.post(self.staff_url, payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['first_name'], 'Alex')
        self.assertEqual(response.data['role'], 'recruiter')
        profile = UserRole.objects.get(supabase_user_id=identity)
        self.assertEqual(profile.company, self.company)
        self.assertEqual(profile.user.first_name, 'Alex')
        create_identity.assert_called_once_with(
            payload['email'], payload['password'], payload['first_name'], payload['last_name'],
        )
        self.assertTrue(SecurityEvent.objects.filter(action='staff.create').exists())

    @patch('apps.companies.management_views.set_staff_password')
    @patch('apps.companies.management_views.send_password_reset')
    def test_admin_can_send_reset_link_and_change_password(self, send_reset, set_password):
        identity = uuid4()
        recruiter = User.objects.create_user(username=str(identity), email='staff@example.test')
        staff = UserRole.objects.create(
            user=recruiter, supabase_user_id=identity, email=recruiter.email,
            role='recruiter', company=self.company,
        )
        self.client.force_authenticate(self.admin)
        base = self.staff_url + f'{staff.pk}/'
        self.assertEqual(self.client.post(base + 'password-reset-link/', {}, format='json').status_code, 200)
        password = 'Reach-Green-River-258'
        self.assertEqual(self.client.post(base + 'password/', {'password': password}, format='json').status_code, 200)
        send_reset.assert_called_once_with(recruiter.email)
        set_password.assert_called_once_with(identity, password)
        staff.refresh_from_db()
        self.assertIsNotNone(staff.sessions_valid_after)
        self.assertTrue(SecurityEvent.objects.filter(action='staff.password_reset_link').exists())
        self.assertTrue(SecurityEvent.objects.filter(action='staff.password_reset').exists())

    @patch('apps.accounts.staff_invitations.set_invited_staff_password')
    def test_activation_requires_matching_unexpired_invitation_and_is_single_use(self, set_password):
        identity = uuid4()
        user = User.objects.create_user(username=str(identity), email='wrong@example.test')
        user.supabase_id = str(identity)
        profile = UserRole.objects.create(user=user, supabase_user_id=identity)
        invitation = StaffInvitation.objects.create(
            email='staff@example.test', company=self.company, invited_by='operator',
            supabase_user_id=identity, state='pending', expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_authenticate(user)
        path = '/api/auth/staff-invitation/accept/'
        payload = {'password': 'Reach-Green-River-147'}
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 404)
        user.email = invitation.email
        future = invitation.expires_at
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=['expires_at'])
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 403)
        set_password.assert_not_called()
        invitation.expires_at = future
        invitation.save(update_fields=['expires_at'])
        self.assertEqual(self.client.post(path, {'password': 'Short-Test-123'}, format='json').status_code, 400)
        set_password.assert_not_called()
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 200)
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 200)
        set_password.assert_called_once_with(identity, invitation.email, payload['password'])
        profile.refresh_from_db()
        self.assertEqual(profile.role, 'recruiter')
        self.assertEqual(profile.company_id, self.company.pk)
        self.assertIsNotNone(profile.sessions_valid_after)

    @override_settings(
        SUPABASE_URL='https://platform.invalid',
        SUPABASE_JWT_SECRET='local-synthetic-superadmin-test-secret',
    )
    @patch('config.authentication.staff_session_is_current')
    def test_unassigned_staff_can_be_assigned_and_existing_token_revoked(self, session_is_current):
        from django.conf import settings

        identity = uuid4()
        email = 'recruiter@example.test'
        profile = UserRole.objects.create(supabase_user_id=identity, email=email, role='recruiter')
        other_admin = UserRole.objects.create(supabase_user_id=uuid4(), role='superadmin')
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.staff_url + '?company=unassigned').data[0]['id'], profile.pk)
        staff_url = self.staff_url + f'{profile.pk}/'
        self.assertEqual(self.client.patch(staff_url, {'company': self.company.pk}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.staff_url + f'{other_admin.pk}/', {'active': False}, format='json').status_code, 404)
        token = jwt.encode({
            'sub': str(identity), 'email': email, 'aud': 'authenticated',
            'iss': 'https://platform.invalid/auth/v1', 'exp': int(time()) + 300, 'aal': 'aal1',
            'session_id': str(uuid4()),
        }, settings.SUPABASE_JWT_SECRET, algorithm='HS256')
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        session_is_current.return_value = True
        self.assertEqual(staff_client.get('/api/jobs/').status_code, 200)
        self.assertEqual(self.client.patch(staff_url, {'active': False}, format='json').status_code, 200)
        self.assertEqual(staff_client.get('/api/jobs/').status_code, 401)
        self.assertEqual(self.client.patch(staff_url, {'active': True}, format='json').status_code, 200)
        session_is_current.return_value = False
        self.assertEqual(staff_client.get('/api/jobs/').status_code, 401)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.sessions_valid_after)
        session_is_current.return_value = True
        self.assertEqual(staff_client.get('/api/jobs/').status_code, 200)

    @override_settings(
        DEBUG=True, SUPABASE_URL='https://platform.invalid',
        SUPABASE_JWT_SECRET='local-synthetic-superadmin-test-secret',
    )
    @patch('config.authentication.staff_session_is_current')
    def test_password_only_superadmin_session_expires_even_with_a_fresh_token(self, session_is_current):
        from django.conf import settings

        identity = uuid4()
        session_id = str(uuid4())
        profile = UserRole.objects.create(supabase_user_id=identity, role='superadmin')
        now = timezone.now()
        token = jwt.encode({
            'sub': str(identity), 'aud': 'authenticated', 'session_id': session_id,
            'iss': 'https://platform.invalid/auth/v1', 'exp': int(time()) + 300,
            'iat': int(time()), 'aal': 'aal1',
        }, settings.SUPABASE_JWT_SECRET, algorithm='HS256')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        created_at = now - timedelta(hours=7)
        session_is_current.side_effect = lambda user_id, sid, cutoff: (
            user_id == identity and sid == session_id and created_at >= cutoff
        )
        self.assertEqual(self.client.get('/api/auth/me/').data['user']['role'], 'superadmin')
        self.assertEqual(self.client.get(self.companies_url).status_code, 200)
        self.assertEqual(self.client.post(self.companies_url, {'name': 'New company'}, format='json').status_code, 201)

        created_at = now - timedelta(hours=8, seconds=1)
        self.assertEqual(self.client.get(self.companies_url).status_code, 401)
        self.assertEqual(self.client.post(self.companies_url, {'name': 'Blocked'}, format='json').status_code, 401)
        self.assertFalse(Company.objects.filter(name='Blocked').exists())

        created_at = now - timedelta(minutes=1)
        self.assertEqual(self.client.get(self.companies_url).status_code, 200)
        profile.sessions_valid_after = now
        profile.save(update_fields=['sessions_valid_after'])
        self.assertEqual(self.client.get(self.companies_url).status_code, 401)

    @patch('apps.companies.management_views.update_staff_identity_name')
    def test_admin_can_edit_recruiter_name_with_supabase_sync_and_redacted_audit(self, sync_name):
        identity = uuid4()
        recruiter = User.objects.create_user(
            username=str(identity), email='staff@example.test', first_name='Alex', last_name='Santos',
        )
        staff = UserRole.objects.create(
            user=recruiter, supabase_user_id=identity, email=recruiter.email,
            role='recruiter', company=self.company,
        )
        self.client.force_authenticate(self.admin)
        url = self.staff_url + f'{staff.pk}/'

        response = self.client.patch(url, {'first_name': '  Elena  ', 'last_name': '  Reyes  '}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['first_name'], 'Elena')
        self.assertEqual(response.data['last_name'], 'Reyes')
        recruiter.refresh_from_db()
        staff.refresh_from_db()
        self.assertEqual(recruiter.first_name, 'Elena')
        self.assertEqual(recruiter.last_name, 'Reyes')
        self.assertIsNone(staff.sessions_valid_after)
        sync_name.assert_called_once_with(identity, 'Elena', 'Reyes')

        event = SecurityEvent.objects.get(action='staff.profile')
        self.assertEqual(event.object_id, str(staff.pk))
        self.assertEqual(event.details, {'fields': ['first_name', 'last_name']})
        self.assertNotIn('Elena', str(event.details))
        self.assertNotIn('Reyes', str(event.details))

        sync_name.reset_mock()
        partial = self.client.patch(url, {'first_name': 'Maria'}, format='json')
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.data['first_name'], 'Maria')
        self.assertEqual(partial.data['last_name'], 'Reyes')
        sync_name.assert_called_once_with(identity, 'Maria', 'Reyes')
        latest_event = SecurityEvent.objects.filter(action='staff.profile').order_by('-id').first()
        self.assertEqual(latest_event.details, {'fields': ['first_name']})

        sync_name.reset_mock()
        noop = self.client.patch(url, {'first_name': 'Maria', 'last_name': 'Reyes'}, format='json')
        self.assertEqual(noop.status_code, 200)
        sync_name.assert_not_called()
        self.assertEqual(SecurityEvent.objects.filter(action='staff.profile').count(), 2)

    @patch('apps.companies.management_views.update_staff_identity_name')
    def test_recruiter_name_validation_and_supabase_failure_rollback(self, sync_name):
        from apps.accounts.staff_identity import AuthProviderUnavailable

        identity = uuid4()
        recruiter = User.objects.create_user(
            username=str(identity), email='staff@example.test', first_name='Alex', last_name='Santos',
        )
        staff = UserRole.objects.create(
            user=recruiter, supabase_user_id=identity, email=recruiter.email,
            role='recruiter', company=self.company,
        )
        other_company = Company.objects.create(name='Other Corp')
        other_admin = UserRole.objects.create(supabase_user_id=uuid4(), role='superadmin')
        self.client.force_authenticate(self.admin)
        url = self.staff_url + f'{staff.pk}/'

        self.assertEqual(self.client.patch(url, {'first_name': ''}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'last_name': '   '}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'first_name': 'A' * 151}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {}, format='json').status_code, 400)
        self.assertEqual(
            self.client.patch(self.staff_url + f'{other_admin.pk}/', {'first_name': 'Blocked'}, format='json').status_code,
            404,
        )
        sync_name.assert_not_called()

        sync_name.side_effect = AuthProviderUnavailable()
        failed = self.client.patch(
            url, {'first_name': 'Jordan', 'company': other_company.pk}, format='json',
        )
        self.assertEqual(failed.status_code, 503)
        recruiter.refresh_from_db()
        staff.refresh_from_db()
        self.assertEqual(recruiter.first_name, 'Alex')
        self.assertEqual(staff.company_id, self.company.pk)
        self.assertFalse(SecurityEvent.objects.filter(action__in=['staff.profile', 'staff.company']).exists())

        sync_name.side_effect = None
        sync_name.reset_mock()
        legacy_identity = uuid4()
        legacy_staff = UserRole.objects.create(
            supabase_user_id=legacy_identity, email='legacy@example.test', role='recruiter',
        )
        company_only = self.client.patch(
            self.staff_url + f'{legacy_staff.pk}/', {'company': self.company.pk}, format='json',
        )
        self.assertEqual(company_only.status_code, 200)
        sync_name.assert_not_called()

        linked = self.client.patch(
            self.staff_url + f'{legacy_staff.pk}/', {'first_name': 'Clara', 'last_name': 'Cruz'}, format='json',
        )
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.data['first_name'], 'Clara')
        self.assertEqual(linked.data['last_name'], 'Cruz')
        sync_name.assert_called_once_with(legacy_identity, 'Clara', 'Cruz')

        unverified = UserRole.objects.create(email='unverified@example.test', role='recruiter')
        self.assertEqual(
            self.client.patch(self.staff_url + f'{unverified.pk}/', {'first_name': 'NoId'}, format='json').status_code,
            400,
        )

        self.client.force_authenticate(recruiter)
        recruiter.role = 'recruiter'
        self.assertEqual(self.client.patch(url, {'first_name': 'SelfEdit'}, format='json').status_code, 403)

    @patch('apps.accounts.staff_identity._auth_admin_request')
    def test_update_staff_identity_name_merges_existing_metadata_and_handles_errors(self, admin_request):
        from unittest.mock import MagicMock
        from apps.accounts.staff_identity import AuthProviderUnavailable, update_staff_identity_name

        identity = uuid4()
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {
            'id': str(identity),
            'user_metadata': {'email_verified': True, 'company': 'Talent Operations', 'first_name': 'Old'},
        }
        put_resp = MagicMock(status_code=200)
        admin_request.side_effect = [get_resp, put_resp]

        update_staff_identity_name(identity, 'Elena', 'Reyes')
        self.assertEqual(admin_request.call_count, 2)
        self.assertEqual(admin_request.call_args_list[0].args, ('GET', f'/admin/users/{identity}'))
        self.assertEqual(admin_request.call_args_list[1].args, ('PUT', f'/admin/users/{identity}'))
        self.assertEqual(
            admin_request.call_args_list[1].kwargs['json'],
            {
                'user_metadata': {
                    'email_verified': True,
                    'company': 'Talent Operations',
                    'first_name': 'Elena',
                    'last_name': 'Reyes',
                    'full_name': 'Elena Reyes',
                },
            },
        )

        admin_request.side_effect = [MagicMock(status_code=500)]
        with self.assertRaises(AuthProviderUnavailable):
            update_staff_identity_name(identity, 'Elena', 'Reyes')

        admin_request.side_effect = [get_resp, MagicMock(status_code=502)]
        with self.assertRaises(AuthProviderUnavailable):
            update_staff_identity_name(identity, 'Elena', 'Reyes')
