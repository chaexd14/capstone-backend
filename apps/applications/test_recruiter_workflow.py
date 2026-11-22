import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import RoleChoices, UserRole
from apps.applications.models import (
    Application,
    ApplicationActivity,
    ApplicationStatus,
    CommunicationTemplate,
    TemplateStage,
)
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

    def test_note_can_be_updated_and_deleted(self):
        created = self.client.post(
            f'/api/applications/{self.application.pk}/notes/',
            {'body': 'Initial private note.'},
            format='json',
        )
        updated = self.client.patch(
            f'/api/applications/{self.application.pk}/notes/{created.data["id"]}/',
            {'body': 'Updated private note.'},
            format='json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['body'], 'Updated private note.')
        deleted = self.client.delete(
            f'/api/applications/{self.application.pk}/notes/{created.data["id"]}/'
        )
        self.assertEqual(deleted.status_code, 204)

    def test_task_can_be_updated_and_deleted(self):
        created = self.client.post(
            f'/api/applications/{self.application.pk}/tasks/',
            {'title': 'Initial task', 'priority': 'LOW'},
            format='json',
        )
        updated = self.client.patch(
            f'/api/applications/{self.application.pk}/tasks/{created.data["id"]}/',
            {'title': 'Updated task', 'priority': 'HIGH'},
            format='json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['title'], 'Updated task')
        deleted = self.client.delete(
            f'/api/applications/{self.application.pk}/tasks/{created.data["id"]}/'
        )
        self.assertEqual(deleted.status_code, 204)

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

    def test_communication_templates_auto_seeded_on_first_fetch(self):
        self.assertEqual(CommunicationTemplate.objects.filter(company=self.company).count(), 0)
        response = self.client.get('/api/communication-templates/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        stages = {item['stage'] for item in response.data}
        self.assertIn('SHORTLISTED', stages)
        self.assertIn('INTERVIEW', stages)
        self.assertIn('HIRED', stages)
        self.assertIn('REJECTED', stages)
        self.assertEqual(CommunicationTemplate.objects.filter(company=self.company).count(), 4)

    def test_communication_template_crud(self):
        # 1. Create custom template
        create_resp = self.client.post(
            '/api/communication-templates/',
            {
                'name': 'Take-Home Assessment',
                'stage': TemplateStage.INTERVIEW,
                'subject': 'Coding Challenge for {{job_title}}',
                'body': 'Hello {{candidate_name}}, please complete this assessment.',
            },
            format='json',
        )
        self.assertEqual(create_resp.status_code, 201)
        template_id = create_resp.data['id']
        self.assertEqual(create_resp.data['name'], 'Take-Home Assessment')

        # 2. Update template
        patch_resp = self.client.patch(
            f'/api/communication-templates/{template_id}/',
            {'subject': 'Updated: Coding Challenge for {{job_title}}'},
            format='json',
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.data['subject'], 'Updated: Coding Challenge for {{job_title}}')

        # 3. Delete template
        delete_resp = self.client.delete(f'/api/communication-templates/{template_id}/')
        self.assertEqual(delete_resp.status_code, 204)
        self.assertFalse(CommunicationTemplate.objects.filter(pk=template_id).exists())

    def test_communication_templates_company_isolation(self):
        other_company = Company.objects.create(name='Other Corp')
        other_template = CommunicationTemplate.objects.create(
            company=other_company,
            name='Other Secret Template',
            stage=TemplateStage.GENERAL,
            subject='Confidential',
            body='Secret details',
        )

        # Recruiter should only see their own company templates
        response = self.client.get('/api/communication-templates/')
        self.assertEqual(response.status_code, 200)
        template_names = [item['name'] for item in response.data]
        self.assertNotIn('Other Secret Template', template_names)

        # Recruiter cannot modify other company's template
        patch_resp = self.client.patch(
            f'/api/communication-templates/{other_template.pk}/',
            {'name': 'Hacked Template'},
            format='json',
        )
        self.assertEqual(patch_resp.status_code, 404)

    def test_legacy_endpoint_compatibility(self):
        response = self.client.get('/api/applications/communication-templates/')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 4)

    def test_rejection_reason_and_tags_endpoints(self):
        status_resp = self.client.patch(
            f'/api/applications/{self.application.pk}/status/',
            {
                'status': ApplicationStatus.REJECTED,
                'rejection_reason': 'SKILLS_GAP',
                'rejection_note': 'Missing required distributed systems experience.',
            },
            format='json',
        )
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.data['rejection_reason'], 'SKILLS_GAP')
        self.assertEqual(status_resp.data['rejection_note'], 'Missing required distributed systems experience.')

        tags_resp = self.client.patch(
            f'/api/applications/{self.application.pk}/tags/',
            {'tags': ['Silver Medalist', 'Strong Python', 'silver medalist']},
            format='json',
        )
        self.assertEqual(tags_resp.status_code, 200)
        self.assertEqual(tags_resp.data['tags'], ['Silver Medalist', 'Strong Python'])

    def test_global_tasks_analytics_and_talent_pool(self):
        self.client.post(
            f'/api/applications/{self.application.pk}/tasks/',
            {'title': 'Follow up on portfolio', 'priority': 'HIGH'},
            format='json',
        )
        tasks_resp = self.client.get('/api/applications/tasks/?status=open')
        self.assertEqual(tasks_resp.status_code, 200)
        self.assertEqual(tasks_resp.data['summary']['open'], 1)
        self.assertEqual(len(tasks_resp.data['tasks']), 1)

        analytics_resp = self.client.get('/api/applications/analytics/')
        self.assertEqual(analytics_resp.status_code, 200)
        self.assertEqual(analytics_resp.data['total_applications'], 1)
        self.assertIn('funnel', analytics_resp.data)

        pool_resp = self.client.get('/api/applications/talent-pool/?q=Platform')
        self.assertEqual(pool_resp.status_code, 200)
        self.assertEqual(len(pool_resp.data), 1)

    def test_interviews_ics_and_scorecards(self):
        create_int = self.client.post(
            f'/api/applications/{self.application.pk}/interviews/',
            {
                'title': 'Technical Screen',
                'scheduled_at': '2026-12-01T09:00:00Z',
                'duration_minutes': 45,
                'mode': 'VIDEO',
                'meeting_link': 'https://meet.example.test/abc',
                'interviewer_names': ['Lead Engineer'],
            },
            format='json',
        )
        self.assertEqual(create_int.status_code, 201)
        interview_id = create_int.data['id']

        ics_resp = self.client.get(f'/api/applications/{self.application.pk}/interviews/{interview_id}/ics/')
        self.assertEqual(ics_resp.status_code, 200)
        self.assertIn(b'BEGIN:VCALENDAR', ics_resp.content)

        create_sc = self.client.post(
            f'/api/applications/{self.application.pk}/scorecards/',
            {
                'interview': interview_id,
                'overall_rating': 5,
                'recommendation': 'STRONG_YES',
                'competency_ratings': [{'label': 'Python', 'rating': 5, 'note': 'Excellent depth'}],
                'strengths': 'Strong architecture skills',
                'concerns': 'None',
            },
            format='json',
        )
        self.assertEqual(create_sc.status_code, 201)
        self.assertEqual(create_sc.data['overall_rating'], 5)

    def test_company_team_and_job_fairness_audit(self):
        team_resp = self.client.get('/api/companies/team/')
        self.assertEqual(team_resp.status_code, 200)
        self.assertEqual(len(team_resp.data), 1)

        audit_resp = self.client.get(f'/api/jobs/{self.job.pk}/fairness-audit/')
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(audit_resp.data['job_id'], self.job.pk)

        cf_resp = self.client.post(f'/api/jobs/{self.job.pk}/fairness-audit/counterfactual/', {}, format='json')
        self.assertEqual(cf_resp.status_code, 200)
        self.assertTrue(cf_resp.data['is_invariant'])
        self.assertEqual(cf_resp.data['max_counterfactual_gap'], 0.0)

