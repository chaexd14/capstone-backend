from unittest.mock import patch

from django.db import DatabaseError
from django.core.checks import run_checks
from django.test import TestCase, override_settings


class HealthEndpointTests(TestCase):
    def test_liveness_does_not_depend_on_database(self):
        with patch('config.health.connection.cursor', side_effect=DatabaseError('offline')):
            response = self.client.get('/api/live/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'alive')
        self.assertTrue(response['X-Request-ID'])

    def test_readiness_reports_database_failure(self):
        with patch('config.health.connection.cursor', side_effect=DatabaseError('offline')):
            response = self.client.get('/api/ready/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['checks']['database'], 'unavailable')

    def test_readiness_reports_pending_migrations(self):
        with patch('config.health._pending_migration_count', return_value=2):
            response = self.client.get('/api/ready/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['checks']['migrations'], '2_pending')

    def test_readiness_succeeds_when_dependencies_are_ready(self):
        with patch('config.health._pending_migration_count', return_value=0):
            response = self.client.get('/api/ready/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ready')


class ProductionEvidenceCheckTests(TestCase):
    @override_settings(
        BACKUP_RESTORE_VERIFIED=False,
        SECURITY_LOG_FORWARDING_CONFIRMED=False,
        PRIVACY_NOTICE_APPROVED=False,
        STAGING_ACCEPTANCE_APPROVED=False,
        CAPACITY_TEST_VERIFIED=False,
    )
    def test_unconfirmed_release_evidence_blocks_deploy_check(self):
        ids = {issue.id for issue in run_checks(include_deployment_checks=True)}
        self.assertTrue({
            'talentmatch.E011',
            'talentmatch.E012',
            'talentmatch.E013',
            'talentmatch.E014',
            'talentmatch.E015',
        } <= ids)
