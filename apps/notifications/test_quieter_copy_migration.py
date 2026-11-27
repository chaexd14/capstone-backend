import importlib
import uuid

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase

from apps.accounts.models import UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.notifications.models import Notification, NotificationType, RecipientType


migration = importlib.import_module(
    'apps.notifications.migrations.0004_quieter_notification_copy'
)


class QuieterNotificationCopyMigrationTests(TestCase):
    def setUp(self):
        self.user_uuid = uuid.uuid4()
        recruiter = User.objects.create(
            username=str(uuid.uuid4()), email='recruiter@example.test',
        )
        company = Company.objects.create(name='Migration Company')
        UserRole.objects.create(user=recruiter, company=company, role='recruiter')
        self.job = Job.objects.create(
            company=company,
            title='Senior AI Engineer',
            department='Engineering',
            description='Build scalable AI engines',
            status=JobStatus.PUBLISHED,
            required_skills=['Python'],
        )
        self.application = Application.objects.create(
            job=self.job,
            first_name='Jane',
            last_name='Doe',
            email='jane@example.test',
            user_id=self.user_uuid,
            status=ApplicationStatus.SUBMITTED,
        )

    def test_forward_and_reverse_preserve_historical_status(self):
        notification = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.user_uuid,
            application=self.application,
            job=self.job,
            title='Application Update: Shortlisted',
            message=(
                f'Your application for {self.job.title} ({self.application.candidate_code}) '
                "has been moved to 'Shortlisted'."
            ),
            notification_type=NotificationType.STATUS_UPDATE,
        )
        self.application.status = ApplicationStatus.HIRED
        self.application.save(update_fields=['status'])

        migration.simplify_notification_copy(apps, None)
        notification.refresh_from_db()
        self.assertEqual(notification.title, 'Application Update')
        self.assertEqual(
            notification.message,
            "Your application for Senior AI Engineer has been moved to 'Shortlisted'.",
        )

        migration.restore_notification_copy(apps, None)
        notification.refresh_from_db()
        self.assertEqual(notification.title, 'Application Update: Shortlisted')
        self.assertEqual(
            notification.message,
            f'Your application for Senior AI Engineer ({self.application.candidate_code}) '
            "has been moved to 'Shortlisted'.",
        )

    def test_forward_normalizes_all_types_and_preserves_orphaned_message(self):
        recruiter = Notification.objects.create(
            recipient_type=RecipientType.RECRUITER,
            application=self.application,
            job=self.job,
            title=f'New Candidate Applied: {self.application.candidate_code}',
            message=f'Candidate {self.application.candidate_code} applied for {self.job.title}.',
            notification_type=NotificationType.NEW_APPLICATION,
        )
        submitted = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.user_uuid,
            application=self.application,
            job=self.job,
            title='Application Submitted',
            message=(
                f'Your application for {self.job.title} ({self.application.candidate_code}) '
                'was successfully submitted.'
            ),
            notification_type=NotificationType.APPLICATION_SUBMITTED,
        )
        orphan = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            title='Application Update: Under Review',
            message="Application status changed to 'Under Review'.",
            notification_type=NotificationType.STATUS_UPDATE,
        )

        migration.simplify_notification_copy(apps, None)
        recruiter.refresh_from_db()
        submitted.refresh_from_db()
        orphan.refresh_from_db()

        self.assertEqual(recruiter.title, 'New Application')
        self.assertEqual(
            recruiter.message,
            f'Candidate {self.application.candidate_code} applied for Senior AI Engineer.',
        )
        self.assertEqual(submitted.title, 'Application Submitted')
        self.assertEqual(
            submitted.message,
            'Your application for Senior AI Engineer was submitted.',
        )
        self.assertEqual(orphan.title, 'Application Update')
        self.assertEqual(orphan.message, "Application status changed to 'Under Review'.")
