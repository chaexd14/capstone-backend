from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.monitoring.models import SystemSnapshot


@override_settings(MONITORING_AGENT_SECRET='monitoring-test-secret')
class MonitoringAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='system-admin')
        self.admin.role = 'superadmin'

    def test_only_superadmin_can_read_monitoring(self):
        self.assertEqual(self.client.get('/api/platform/monitoring/overview/').status_code, 401)
        for role in ('recruiter', 'applicant'):
            user = User.objects.create_user(username=role)
            user.role = role
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/platform/monitoring/overview/').status_code, 403)
            self.assertEqual(self.client.get('/api/platform/monitoring/timeseries/').status_code, 403)
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/platform/monitoring/overview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get('/api/platform/monitoring/timeseries/').status_code, 200)

    def test_agent_requires_separate_machine_secret(self):
        payload = {
            'agent_id': 'primary-vm', 'cpu_percent': 20, 'memory_percent': 40,
            'disk_percent': 30, 'uptime_seconds': 900, 'api_service_up': True,
            'worker_service_up': True,
        }
        path = '/api/platform/monitoring/agent/report/'
        self.assertEqual(self.client.post(path, payload, format='json').status_code, 403)
        response = self.client.post(
            path, payload, format='json', HTTP_X_MONITORING_AGENT_KEY='monitoring-test-secret',
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(SystemSnapshot.objects.count(), 1)

    def test_overview_aggregates_security_events_without_actor_details(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/platform/monitoring/overview/')
        rendered = str(response.data)
        self.assertNotIn('actor_id', rendered)
        self.assertNotIn('details', rendered)
