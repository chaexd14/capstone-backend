import uuid
from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from apps.jobs.models import Job, JobStatus
from apps.applications.models import Application, ApplicationStatus
from apps.notifications.models import Notification, RecipientType, NotificationType
from apps.notifications.services import create_new_application_notification, create_status_change_notification

class NotificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_uuid = uuid.uuid4()
        self.recruiter_uuid = uuid.uuid4()

        self.recruiter_user = User.objects.create(
            username=str(self.recruiter_uuid),
            email="recruiter_test@example.com",
            is_staff=True
        )
        self.recruiter_user.role = "recruiter"
        self.recruiter_user.supabase_id = str(self.recruiter_uuid)

        self.applicant_user = User.objects.create(
            username=str(self.user_uuid),
            email="jane.doe@example.com",
            is_staff=False
        )
        self.applicant_user.role = "applicant"
        self.applicant_user.supabase_id = str(self.user_uuid)

        self.job = Job.objects.create(
            title="Senior AI Engineer",
            department="Engineering",
            description="Build scalable AI engines",
            status=JobStatus.PUBLISHED,
            required_skills=["Python", "PyTorch"]
        )
        self.application = Application.objects.create(
            job=self.job,
            first_name="Jane",
            last_name="Doe",
            email="jane.doe@example.com",
            user_id=self.user_uuid,
            status=ApplicationStatus.SUBMITTED
        )

    def test_create_new_application_notification(self):
        # Call service
        create_new_application_notification(self.application)

        # Should create 1 recruiter notification and 1 applicant notification
        recruiter_notif = Notification.objects.filter(
            recipient_type=RecipientType.RECRUITER,
            application=self.application
        ).first()
        self.assertIsNotNone(recruiter_notif)
        self.assertIn("Jane Doe", recruiter_notif.title)
        self.assertFalse(recruiter_notif.is_read)

        applicant_notif = Notification.objects.filter(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.user_uuid
        ).first()
        self.assertIsNotNone(applicant_notif)
        self.assertEqual(applicant_notif.notification_type, NotificationType.APPLICATION_SUBMITTED)
        self.assertFalse(applicant_notif.is_read)

    def test_status_change_notification(self):
        create_status_change_notification(
            self.application,
            previous_status=ApplicationStatus.SUBMITTED,
            new_status=ApplicationStatus.SHORTLISTED
        )

        notif = Notification.objects.filter(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.user_uuid,
            notification_type=NotificationType.STATUS_UPDATE
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Shortlisted", notif.title)
        self.assertIn("Shortlisted", notif.message)

    def test_list_notifications_api(self):
        create_new_application_notification(self.application)

        # 1. Recruiter query
        self.client.force_authenticate(user=self.recruiter_user)
        res_recruiter = self.client.get('/api/notifications/?recipient_type=recruiter')
        self.assertEqual(res_recruiter.status_code, status.HTTP_200_OK)
        self.assertEqual(res_recruiter.data['unread_count'], 1)
        self.assertEqual(len(res_recruiter.data['notifications']), 1)
        self.assertEqual(res_recruiter.data['notifications'][0]['recipient_type'], 'RECRUITER')

        # 2. Applicant query
        self.client.force_authenticate(user=self.applicant_user)
        res_applicant = self.client.get(f'/api/notifications/?recipient_type=applicant&user_id={self.user_uuid}')
        self.assertEqual(res_applicant.status_code, status.HTTP_200_OK)
        self.assertEqual(res_applicant.data['unread_count'], 1)
        self.assertEqual(len(res_applicant.data['notifications']), 1)
        self.assertEqual(res_applicant.data['notifications'][0]['recipient_type'], 'APPLICANT')

    def test_mark_as_read_api(self):
        create_new_application_notification(self.application)
        notif = Notification.objects.filter(recipient_type=RecipientType.RECRUITER).first()

        self.client.force_authenticate(user=self.recruiter_user)
        res = self.client.patch(f'/api/notifications/{notif.id}/read/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_mark_all_read_api(self):
        create_new_application_notification(self.application)
        self.assertEqual(Notification.objects.filter(recipient_type=RecipientType.RECRUITER, is_read=False).count(), 1)

        self.client.force_authenticate(user=self.recruiter_user)
        res = self.client.post('/api/notifications/mark-all-read/', {'recipient_type': 'recruiter'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient_type=RecipientType.RECRUITER, is_read=False).count(), 0)

    def test_unauthenticated_notification_access_denied(self):
        self.client.force_authenticate(user=None)
        res = self.client.get('/api/notifications/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_direct_notification_creation_disabled(self):
        self.client.force_authenticate(user=self.recruiter_user)
        res = self.client.post('/api/notifications/', {'title': 'Spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
