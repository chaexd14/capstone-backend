import tempfile
import uuid
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from apps.jobs.models import Job
from apps.applications.models import Application


@override_settings(
    MEDIA_ROOT=tempfile.gettempdir(),
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class DuplicateAndValidationApplicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_uuid = uuid.uuid4()
        self.user = User.objects.create_user(username=str(self.user_uuid), email="juan.delacruz@example.com")
        self.client.force_authenticate(user=self.user)
        self.client.credentials(
            HTTP_X_APP_CLIENT_KEY="talentmatch-client-key-2026",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.job1 = Job.objects.create(
            title="Software Developer",
            department="IT",
            description="Design and build scalable web applications using Python and Django.",
            required_skills=["Python", "Django"],
            minimum_experience="2 years",
            education_requirement="BS CS"
        )
        self.job2 = Job.objects.create(
            title="Data Analyst",
            department="Analytics",
            description="Analyze enterprise datasets and build SQL reporting pipelines.",
            required_skills=["SQL", "Python"],
            minimum_experience="1 year",
            education_requirement="BS Math"
        )

    def test_first_application_succeeds(self):
        resume_file = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 test content", content_type="application/pdf")
        response = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Juan",
                "last_name": "Dela Cruz",
                "email": "juan.delacruz@example.com",
                "phone": "+63 917 123 4567",
                "resume": resume_file,
            },
            format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Application.objects.filter(job=self.job1, email="juan.delacruz@example.com").count(), 1)

    def test_duplicate_application_by_email_is_blocked(self):
        resume_file1 = SimpleUploadedFile("resume1.pdf", b"%PDF-1.4 test content 1", content_type="application/pdf")
        res1 = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Juan",
                "last_name": "Dela Cruz",
                "email": "juan.delacruz@example.com",
                "phone": "+63 917 123 4567",
                "resume": resume_file1,
            },
            format="multipart"
        )
        self.assertEqual(res1.status_code, 201)

        # Attempt to apply again with case-varied email to same job
        resume_file2 = SimpleUploadedFile("resume2.pdf", b"%PDF-1.4 test content 2", content_type="application/pdf")
        res2 = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Juan",
                "last_name": "Dela Cruz",
                "email": "JUAN.DELACRUZ@EXAMPLE.COM",
                "phone": "+63 917 123 4567",
                "resume": resume_file2,
            },
            format="multipart"
        )
        self.assertEqual(res2.status_code, 400)
        data = res2.json()
        self.assertTrue("detail" in data or "email" in data)
        self.assertIn("already applied", str(data).lower())

    def test_duplicate_application_by_user_id_is_blocked(self):
        user_uuid = uuid.uuid4()
        resume_file1 = SimpleUploadedFile("resume1.pdf", b"%PDF-1.4 test content 1", content_type="application/pdf")
        res1 = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Maria",
                "last_name": "Santos",
                "email": "maria1@example.com",
                "phone": "09171234567",
                "user_id": str(user_uuid),
                "resume": resume_file1,
            },
            format="multipart"
        )
        self.assertEqual(res1.status_code, 201)

        # Attempt to apply again with different email but same user_id to same job
        resume_file2 = SimpleUploadedFile("resume2.pdf", b"%PDF-1.4 test content 2", content_type="application/pdf")
        res2 = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Maria",
                "last_name": "Santos",
                "email": "maria2_alt@example.com",
                "phone": "09171234567",
                "user_id": str(user_uuid),
                "resume": resume_file2,
            },
            format="multipart"
        )
        self.assertEqual(res2.status_code, 400)
        data = res2.json()
        self.assertIn("already applied", str(data).lower())

    def test_same_applicant_can_apply_to_different_jobs(self):
        resume_file1 = SimpleUploadedFile("resume1.pdf", b"%PDF-1.4 test content 1", content_type="application/pdf")
        res1 = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Pedro",
                "last_name": "Penduko",
                "email": "pedro@example.com",
                "phone": "+63 918 765 4321",
                "resume": resume_file1,
            },
            format="multipart"
        )
        self.assertEqual(res1.status_code, 201)

        # Applying to job2 should succeed
        resume_file2 = SimpleUploadedFile("resume2.pdf", b"%PDF-1.4 test content 2", content_type="application/pdf")
        res2 = self.client.post(
            f"/api/jobs/{self.job2.id}/apply/",
            {
                "first_name": "Pedro",
                "last_name": "Penduko",
                "email": "pedro@example.com",
                "phone": "+63 918 765 4321",
                "resume": resume_file2,
            },
            format="multipart"
        )
        self.assertEqual(res2.status_code, 201)

    def test_rejects_invalid_names_and_phone(self):
        resume_file = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 test content", content_type="application/pdf")
        res = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "J",
                "last_name": "Doe123",
                "email": "invalid@example.com",
                "phone": "abc-123",
                "resume": resume_file,
            },
            format="multipart"
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("first_name", data)
        self.assertIn("last_name", data)
        self.assertIn("phone", data)

    def test_rejects_empty_and_legacy_doc_resume_files(self):
        empty_file = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        res_empty = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Maria",
                "last_name": "Clara",
                "email": "maria.clara@example.com",
                "phone": "+63 917 123 4567",
                "resume": empty_file,
            },
            format="multipart"
        )
        self.assertEqual(res_empty.status_code, 400)
        self.assertIn("resume", res_empty.json())

        legacy_doc = SimpleUploadedFile("legacy.doc", b"binary doc content", content_type="application/msword")
        res_doc = self.client.post(
            f"/api/jobs/{self.job1.id}/apply/",
            {
                "first_name": "Maria",
                "last_name": "Clara",
                "email": "maria.clara@example.com",
                "phone": "+63 917 123 4567",
                "resume": legacy_doc,
            },
            format="multipart"
        )
        self.assertEqual(res_doc.status_code, 400)
        self.assertIn("resume", res_doc.json())
        self.assertIn(".doc", str(res_doc.json()["resume"]).lower())
