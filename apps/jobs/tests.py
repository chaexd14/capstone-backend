from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from apps.jobs.models import Job, JobStatus, ArchivedJob
from apps.applications.models import Application
from apps.resumes.models import Resume
from apps.jobs.services import (
    clean_biased_text,
    detect_is_regulated_role,
    lint_job_requisition,
)


class JobCalibrationStudioTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.recruiter = User.objects.create_user(
            username="recruiter_test",
            email="recruiter@talentmatch.ai",
            is_staff=True
        )
        self.client.force_authenticate(user=self.recruiter)
        self.client.credentials(
            HTTP_X_APP_CLIENT_KEY="talentmatch-client-key-2026",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )

    def test_clean_biased_text(self):
        dirty = "Looking for a young and energetic digital native and rockstar ninja for our energetic team with a pleasing personality."
        cleaned = clean_biased_text(dirty)
        self.assertNotIn("digital native", cleaned.lower())
        self.assertNotIn("young and energetic", cleaned.lower())
        self.assertNotIn("rockstar", cleaned.lower())
        self.assertNotIn("ninja", cleaned.lower())
        self.assertNotIn("pleasing personality", cleaned.lower())

    def test_detect_is_regulated_role(self):
        self.assertTrue(detect_is_regulated_role("Staff Nurse", "Healthcare"))
        self.assertTrue(detect_is_regulated_role("Certified Public Accountant", "Finance"))
        self.assertTrue(detect_is_regulated_role("Civil Project Engineer", "Construction"))
        self.assertFalse(detect_is_regulated_role("Full Stack Developer", "IT"))
        self.assertFalse(detect_is_regulated_role("Customer Service Specialist", "BPO"))

    def test_lint_job_requisition_flags_bias_and_missing_license(self):
        res = lint_job_requisition(
            title="Registered Nurse",
            description="We want a young and energetic digital native nurse.",
            required_skills=["Patient Care", "IV Therapy"]  # Missing PRC RN
        )
        self.assertFalse(res["is_clean"])
        categories = [f["category"] for f in res["flags"]]
        self.assertIn("age_bias", categories)
        self.assertIn("missing_regulatory_license", categories)
        self.assertTrue(res["has_replacements"])

    def test_ai_draft_endpoint(self):
        response = self.client.post("/api/jobs/ai-draft/", {"title": "Full Stack Developer", "department": "IT"}, format="json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("title", data)
        self.assertIn("description", data)
        self.assertIn("required_skills", data)
        self.assertIn("preferred_skills", data)
        self.assertIn("minimum_experience", data)
        self.assertIn("job_family", data)
        self.assertIn("weights", data)

    def test_lint_jd_endpoint(self):
        response = self.client.post("/api/jobs/lint-jd/", {
            "title": "CPA Accountant",
            "description": "Clean description with no bias.",
            "required_skills": ["PRC CPA", "Tax Preparation"]
        }, format="json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["is_clean"])
        self.assertEqual(len(data["flags"]), 0)

    def test_job_creation_with_valid_custom_rubric_weights(self):
        payload = {
            "title": "Backend Python Engineer",
            "department": "Engineering",
            "location": "Manila / Hybrid",
            "description": "Design and build fast, scalable REST APIs in Django and PostgreSQL.",
            "minimum_experience": "2+ years",
            "education_requirement": "BS Computer Science",
            "required_skills": ["Python", "Django"],
            "preferred_skills": ["Docker"],
            "rubric_weights": {
                "required_skills": 50,
                "experience": 20,
                "education": 10,
                "preferred_skills": 10,
                "projects": 10,
            }
        }
        response = self.client.post("/api/jobs/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["rubric_weights"]["required_skills"], 50)
        self.assertEqual(data["rubric_weights"]["experience"], 20)

    def test_job_creation_with_invalid_rubric_weights_rejected(self):
        payload = {
            "title": "Invalid Weights Role",
            "department": "Engineering",
            "location": "Manila / Hybrid",
            "description": "Design and build scalable services and backend microservices.",
            "required_skills": ["Python"],
            "rubric_weights": {
                "required_skills": 30,
                "experience": 20,
                "education": 10,
                "preferred_skills": 10,
                "projects": 10,  # Sum is 80, not 100
            }
        }
        response = self.client.post("/api/jobs/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("rubric_weights", response.json())

    def test_job_creation_rejects_short_fields_and_empty_required_skills(self):
        payload = {
            "title": "AB",
            "department": "I",
            "location": "M",
            "description": "Too short",
            "required_skills": [],
            "status": JobStatus.PUBLISHED,
        }
        response = self.client.post("/api/jobs/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("title", data)
        self.assertIn("department", data)
        self.assertIn("location", data)
        self.assertIn("description", data)

    def test_job_creation_accepts_sentence_qualifications_and_strips_bullets(self):
        payload = {
            "title": "Senior B2B Enterprise Account Executive",
            "department": "Sales & Business Development",
            "employment_type": "Full-time",
            "location": "Makati City / Hybrid",
            "description": "• Prospect and close enterprise software accounts across financial and retail verticals.",
            "minimum_experience": "At least 3 years in enterprise B2B sales",
            "education_requirement": "Bachelor's degree in Business Administration or related field",
            "required_skills": [
                "• Proven track record in B2B enterprise software sales, client negotiation, and account management",
                "- Hands-on experience managing full-cycle sales pipelines in Salesforce or HubSpot CRM",
            ],
            "preferred_skills": [
                "• Experience presenting commercial proposals to C-level executives",
            ],
            "status": JobStatus.PUBLISHED,
        }
        response = self.client.post("/api/jobs/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(
            data["required_skills"][0],
            "Proven track record in B2B enterprise software sales, client negotiation, and account management",
        )
        self.assertEqual(
            data["required_skills"][1],
            "Hands-on experience managing full-cycle sales pipelines in Salesforce or HubSpot CRM",
        )


class JobLifecycleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.recruiter = User.objects.create_user(
            username="recruiter_lifecycle",
            email="recruiter2@talentmatch.ai",
            is_staff=True
        )
        self.client.force_authenticate(user=self.recruiter)
        self.client.credentials(
            HTTP_X_APP_CLIENT_KEY="talentmatch-client-key-2026",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.job = Job.objects.create(
            title="Senior QA Engineer",
            department="Engineering",
            description="Leading automated QA tests and end-to-end verification suites.",
            employment_type="Full-time",
            location="Manila / Remote",
            minimum_experience="3+ years",
            education_requirement="BS Computer Science",
            required_skills=["PyTest", "Selenium"],
            status=JobStatus.PUBLISHED,
        )

    def test_close_job(self):
        url = f"/api/jobs/{self.job.id}/close/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobStatus.CLOSED)

    def test_reopen_job(self):
        self.job.status = JobStatus.CLOSED
        self.job.save()

        url = f"/api/jobs/{self.job.id}/reopen/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobStatus.PUBLISHED)

    def test_cannot_apply_to_closed_job(self):
        self.job.status = JobStatus.CLOSED
        self.job.save()

        url = f"/api/jobs/{self.job.id}/apply/"
        payload = {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane.doe@example.com",
            "phone": "09171234567",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("closed", response.json()["error"].lower())

    def test_archive_job_purges_applicants_and_creates_archived_record(self):
        # Create an application with resume for this job
        app = Application.objects.create(
            job=self.job,
            first_name="Candidate",
            last_name="One",
            email="candidate1@example.com",
        )
        resume = Resume.objects.create(
            application=app,
            original_filename="sample_resume.pdf"
        )

        job_id = self.job.id
        url = f"/api/jobs/{job_id}/archive/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)

        # Active job deleted
        self.assertFalse(Job.objects.filter(id=job_id).exists())

        # Application and resume deleted
        self.assertFalse(Application.objects.filter(id=app.id).exists())
        self.assertFalse(Resume.objects.filter(id=resume.id).exists())

        # ArchivedJob created
        archived = ArchivedJob.objects.filter(title="Senior QA Engineer").first()
        self.assertIsNotNone(archived)
        self.assertEqual(archived.department, "Engineering")

    def test_restore_archived_job_recreates_published_job(self):
        archived = ArchivedJob.objects.create(
            title="DevOps Lead",
            department="Infrastructure",
            description="Manage Kubernetes clusters and cloud deployment pipelines.",
            employment_type="Full-time",
            location="Remote",
            minimum_experience="5+ years",
            education_requirement="BS Engineering",
            required_skills=["AWS", "Kubernetes"],
        )

        url = f"/api/archived-jobs/{archived.id}/restore/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 201)

        # Archived job deleted
        self.assertFalse(ArchivedJob.objects.filter(id=archived.id).exists())

        # New active Job created with PUBLISHED status
        restored_job = Job.objects.filter(title="DevOps Lead").first()
        self.assertIsNotNone(restored_job)
        self.assertEqual(restored_job.status, JobStatus.PUBLISHED)
        self.assertEqual(restored_job.department, "Infrastructure")

    def test_delete_job_cascades_and_removes_applicants(self):
        app = Application.objects.create(
            job=self.job,
            first_name="Candidate",
            last_name="Two",
            email="candidate2@example.com",
        )
        job_id = self.job.id
        response = self.client.delete(f"/api/jobs/{job_id}/")
        self.assertEqual(response.status_code, 204)

        self.assertFalse(Job.objects.filter(id=job_id).exists())
        self.assertFalse(Application.objects.filter(id=app.id).exists())
