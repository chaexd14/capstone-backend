import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import RoleChoices, UserRole
from apps.applications.models import Application, ApplicationActivity, ApplicationStatus
from apps.companies.models import Company
from apps.jobs.models import Job


class RecruiterWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Workflow Company')
        self.recruiter_uuid = uuid.uuid4()
        self.recruiter = User.objects.create_user(
            username=str(self.recruiter_uuid), email='recruiter@example.test'
        )
        self.recruiter.role = RoleChoices.RECRUITER
        self.recruiter.supabase_id = str(self.recruiter_uuid)
        UserRole.objects.create(
            user=self.recruiter,
            supabase_user_id=self.recruiter_uuid,
            email=self.recruiter.email,
            role=RoleChoices.RECRUITER,
            company=self.company,
        )
        self.job = Job.objects.create(
            company=self.company,
            title='Platform Engineer',
            department='Engineering',
            description='Build reliable platform services.',
            required_skills=['Python'],
            preferred_skills=['Django'],
        )
        self.application = Application.objects.create(
            job=self.job,
            first_name='Alex',
            last_name='Rivera',
            email='alex@example.test',
            user_id=uuid.uuid4(),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.recruiter)

    def test_note_creates_private_activity(self):
        response = self.client.post(
            f'/api/applications/{self.application.pk}/notes/',
            {'body': 'Confirm on-call availability during screening.'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['author_id'], str(self.recruiter_uuid))
        activity = ApplicationActivity.objects.get(application=self.application)
        self.assertEqual(activity.kind, ApplicationActivity.Kind.NOTE)

    def test_task_can_be_created_and_completed(self):
        created = self.client.post(
            f'/api/applications/{self.application.pk}/tasks/',
            {'title': 'Schedule screening call', 'priority': 'HIGH'},
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        completed = self.client.patch(
            f'/api/applications/{self.application.pk}/tasks/{created.data["id"]}/',
            {'is_complete': True},
            format='json',
        )
        self.assertEqual(completed.status_code, 200)
        self.assertTrue(completed.data['is_complete'])

    def test_bulk_status_change_is_audited(self):
        response = self.client.post(
            '/api/applications/bulk/',
            {'application_ids': [self.application.pk], 'status': ApplicationStatus.SHORTLISTED},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.SHORTLISTED)
        self.assertTrue(
            ApplicationActivity.objects.filter(
                application=self.application,
                kind=ApplicationActivity.Kind.STATUS,
            ).exists()
        )

    def test_bulk_status_change_rejects_cross_company_records(self):
        other_company = Company.objects.create(name='Other Company')
        other_job = Job.objects.create(
            company=other_company,
            title='Private Role',
            department='Operations',
            description='Company-private role.',
            required_skills=[],
            preferred_skills=[],
        )
        other_application = Application.objects.create(
            job=other_job,
            first_name='Private',
            last_name='Candidate',
            email='private@example.test',
        )
        response = self.client.post(
            '/api/applications/bulk/',
            {
                'application_ids': [self.application.pk, other_application.pk],
                'status': ApplicationStatus.SHORTLISTED,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.SUBMITTED)

    def test_bulk_status_change_rejects_non_integer_ids(self):
        response = self.client.post(
            '/api/applications/bulk/',
            {'application_ids': [str(self.application.pk)], 'status': ApplicationStatus.SHORTLISTED},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_applicant_cannot_access_recruiter_notes(self):
        applicant = User.objects.create_user(
            username=str(self.application.user_id), email=self.application.email
        )
        applicant.role = RoleChoices.APPLICANT
        applicant.supabase_id = str(self.application.user_id)
        self.client.force_authenticate(applicant)
        response = self.client.get(f'/api/applications/{self.application.pk}/notes/')
        self.assertEqual(response.status_code, 403)

    def test_email_draft_action_is_recorded_without_claiming_delivery(self):
        response = self.client.post(
            f'/api/applications/{self.application.pk}/communications/',
            {
                'subject': 'Interview invitation',
                'body': 'Hello Alex, please review this interview invitation.',
                'draft_action': 'OPENED_GMAIL',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        activity = ApplicationActivity.objects.get(
            application=self.application,
            kind=ApplicationActivity.Kind.COMMUNICATION,
        )
        self.assertEqual(activity.summary, 'Opened Gmail draft: Interview invitation')
        self.assertEqual(activity.details['channel'], 'GMAIL')
        self.assertEqual(activity.details['delivery_status'], 'DRAFT')

    def test_email_draft_rejects_unknown_action(self):
        response = self.client.post(
            f'/api/applications/{self.application.pk}/communications/',
            {
                'subject': 'Application update',
                'body': 'Hello Alex.',
                'draft_action': 'SENT',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            ApplicationActivity.objects.filter(
                application=self.application,
                kind=ApplicationActivity.Kind.COMMUNICATION,
            ).exists()
        )
