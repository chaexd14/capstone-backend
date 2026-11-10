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
from apps.accounts.staff_identity import AuthProviderUnavailable
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

    @override_settings(
        SUPABASE_URL='https://platform.invalid',
        SUPABASE_SECRET_KEY='sb_secret_synthetic',
        STAFF_INVITE_REDIRECT_URL='https://talentmatch.invalid/accept-staff-invite',
    )
    @patch('apps.accounts.staff_identity.requests.request')
    def test_invitation_is_idempotent_and_grants_no_role(self, provider_request):
        identity = uuid4()
        provider_request.return_value.status_code = 200
        provider_request.return_value.json.return_value = {'id': str(identity)}
        self.client.force_authenticate(self.admin)
        payload = {'email': 'staff@example.test', 'company': self.company.pk, 'role': 'superadmin'}
        first = self.client.post(self.staff_url, payload, format='json')
        second = self.client.post(self.staff_url, payload, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(first.data['state'], 'pending')
        self.assertFalse(UserRole.objects.filter(supabase_user_id=identity).exists())
        provider_request.assert_called_once()
        self.assertEqual(provider_request.call_args.args, ('POST', 'https://platform.invalid/auth/v1/invite'))
        self.assertEqual(provider_request.call_args.kwargs['headers'], {'apikey': 'sb_secret_synthetic'})
        self.assertEqual(provider_request.call_args.kwargs['json'], {'email': payload['email']})
        self.assertEqual(self.client.get(self.staff_url + f'invitations/?company={self.company.pk}').data[0]['state'], 'pending')
        self.assertTrue(SecurityEvent.objects.filter(action='staff.invite').exists())

    @override_settings(STAFF_INVITE_REDIRECT_URL='https://talentmatch.invalid/accept-staff-invite')
    @patch('apps.accounts.staff_invitations.send_staff_invitation', side_effect=AuthProviderUnavailable)
    def test_failed_invitation_is_visible_without_creating_a_role(self, send):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.staff_url, {'email': 'staff@example.test', 'company': self.company.pk}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(StaffInvitation.objects.get().state, 'failed')
        self.assertFalse(UserRole.objects.filter(email='staff@example.test').exists())

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
