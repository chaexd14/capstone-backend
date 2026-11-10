from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.resumes.models import ResumeWork, WorkerHeartbeat


class ResumeWorkerHealthCommandTests(TestCase):
    def test_healthy_worker_and_empty_queue_pass(self):
        WorkerHeartbeat.objects.create(name='resume-worker')
        output = StringIO()
        call_command('check_resume_worker_health', stdout=output)
        self.assertIn('healthy', output.getvalue().lower())

    def test_missing_heartbeat_fails(self):
        with self.assertRaisesMessage(CommandError, 'has not reported a heartbeat'):
            call_command('check_resume_worker_health')

    def test_old_queue_and_failed_work_fail(self):
        WorkerHeartbeat.objects.create(name='resume-worker')
        ResumeWork.objects.create(
            kind=ResumeWork.Kind.SCAN,
            target='old-ready-work',
            run_after=timezone.now() - timedelta(minutes=10),
        )
        ResumeWork.objects.create(
            kind=ResumeWork.Kind.DELETE,
            target='failed-work',
            failed=True,
        )
        with self.assertRaises(CommandError) as raised:
            call_command('check_resume_worker_health', max_queue_age=300, max_failed=0)
        self.assertIn('oldest ready work', str(raised.exception))
        self.assertIn('failed work item', str(raised.exception))
