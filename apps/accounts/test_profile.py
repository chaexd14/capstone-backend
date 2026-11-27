from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import SecurityEvent
from apps.accounts.staff_identity import AuthProviderUnavailable


class AccountProfileTests(APITestCase):
    def setUp(self):
        self.identity = uuid4()
        self.user = User.objects.create_user(
            username=str(self.identity),
            email='alex@example.test',
            first_name='Alex',
            last_name='Santos',
        )
        self.user.role = 'applicant'
        self.user.supabase_id = str(self.identity)
        self.client.force_authenticate(self.user)

    @patch('apps.accounts.views.update_identity_name')
    def test_user_can_update_own_profile_name(self, update_identity):
        response = self.client.patch(
            '/api/auth/me/',
            {'first_name': '  María  ', 'last_name': "Dela Cruz"},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['first_name'], 'María')
        self.assertEqual(response.data['user']['last_name'], 'Dela Cruz')
        self.assertEqual(response.data['user']['email'], 'alex@example.test')
        update_identity.assert_called_once_with(str(self.identity), 'María', 'Dela Cruz')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'María')
        self.assertEqual(self.user.last_name, 'Dela Cruz')
        self.assertTrue(
            SecurityEvent.objects.filter(
                actor_id=str(self.identity), action='account.profile', object_id=str(self.user.pk),
            ).exists()
        )

    @patch('apps.accounts.views.update_identity_name')
    def test_unchanged_name_is_a_noop(self, update_identity):
        response = self.client.patch(
            '/api/auth/me/',
            {'first_name': 'Alex', 'last_name': 'Santos'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        update_identity.assert_not_called()
        self.assertFalse(SecurityEvent.objects.filter(action='account.profile').exists())

    def test_profile_rejects_invalid_or_protected_fields(self):
        invalid = self.client.patch(
            '/api/auth/me/',
            {'first_name': 'A1', 'last_name': 'Santos'},
            format='json',
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('first_name', invalid.data)

        protected = self.client.patch(
            '/api/auth/me/',
            {'first_name': 'Alex', 'last_name': 'Santos', 'email': 'other@example.test'},
            format='json',
        )
        self.assertEqual(protected.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'alex@example.test')

    @patch(
        'apps.accounts.views.update_identity_name',
        side_effect=AuthProviderUnavailable('Profile updates are temporarily unavailable.'),
    )
    def test_provider_failure_does_not_change_django_name(self, _update_identity):
        response = self.client.patch(
            '/api/auth/me/',
            {'first_name': 'Maria', 'last_name': 'Reyes'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Alex')
        self.assertEqual(self.user.last_name, 'Santos')

    def test_anonymous_profile_update_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.patch(
            '/api/auth/me/',
            {'first_name': 'Maria', 'last_name': 'Reyes'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
