from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.companies.models import Company
from apps.jobs.models import ArchivedJob, Job, JobStatus


class JobSalaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(name="Salary Test Company")
        self.recruiter = User.objects.create_user(
            username="salary_recruiter", email="salary@example.com"
        )
        self.recruiter.role = "recruiter"
        UserRole.objects.create(user=self.recruiter, company=self.company, role="recruiter")
        self.client.force_authenticate(user=self.recruiter)
        self.client.credentials(
            HTTP_X_APP_CLIENT_KEY="talentmatch-client-key-2026",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def payload(self, **overrides):
        payload = {
            "title": "Salary Test Engineer",
            "department": "Engineering",
            "description": "Build and maintain reliable services for the hiring platform.",
            "job_overview": "Join the platform engineering team to improve dependable hiring workflows for recruiters and applicants.",
            "employment_type": "Full-time",
            "location": "Manila / Hybrid",
            "minimum_experience": "2+ years",
            "education_requirement": "BS Computer Science",
            "required_skills": ["Python"],
            "preferred_skills": [],
            "salary_min": 50000,
            "salary_max": 75000,
            "status": JobStatus.PUBLISHED,
        }
        payload.update(overrides)
        return payload

    def test_published_job_accepts_fixed_and_range_salary(self):
        ranged = self.client.post("/api/jobs/", self.payload(), format="json")
        self.assertEqual(ranged.status_code, 201)
        self.assertEqual(ranged.data["salary_min"], 50000)
        self.assertEqual(ranged.data["salary_max"], 75000)

        fixed = self.client.post(
            "/api/jobs/", self.payload(title="Fixed Salary Engineer", salary_max=50000), format="json"
        )
        self.assertEqual(fixed.status_code, 201)
        self.assertEqual(fixed.data["salary_min"], fixed.data["salary_max"])

    def test_salary_validation_and_draft_omission(self):
        draft = self.client.post(
            "/api/jobs/",
            self.payload(title="Draft Engineer", status=JobStatus.DRAFT, salary_min=None, salary_max=None),
            format="json",
        )
        self.assertEqual(draft.status_code, 201)

        for overrides in (
            {"salary_min": None, "salary_max": None},
            {"salary_min": 50000, "salary_max": None},
            {"salary_min": 75000, "salary_max": 50000},
            {"salary_min": 0, "salary_max": 50000},
        ):
            response = self.client.post("/api/jobs/", self.payload(**overrides), format="json")
            self.assertEqual(response.status_code, 400)

    def test_edit_and_reopen_require_salary(self):
        legacy = Job.objects.create(
            company=self.company,
            title="Legacy Engineer",
            department="Engineering",
            description="Maintain reliable legacy hiring platform services.",
            required_skills=["Python"],
            status=JobStatus.CLOSED,
        )
        reopen = self.client.post(f"/api/jobs/{legacy.id}/reopen/")
        self.assertEqual(reopen.status_code, 400)

        patch = self.client.patch(
            f"/api/jobs/{legacy.id}/",
            {"status": JobStatus.PUBLISHED},
            format="json",
        )
        self.assertEqual(patch.status_code, 400)

    def test_archive_preserves_salary_and_legacy_restore_becomes_draft(self):
        job = Job.objects.create(
            company=self.company,
            title="Paid Engineer",
            department="Engineering",
            description="Maintain reliable paid hiring platform services.",
            job_overview="Join the platform engineering team to maintain dependable hiring services for recruiters.",
            required_skills=["Python"],
            salary_min=60000,
            salary_max=80000,
            status=JobStatus.PUBLISHED,
        )
        archived_response = self.client.post(f"/api/jobs/{job.id}/archive/")
        self.assertEqual(archived_response.status_code, 200)
        self.assertEqual(archived_response.data["salary_min"], 60000)
        self.assertEqual(archived_response.data["salary_max"], 80000)

        legacy_archive = ArchivedJob.objects.create(
            company=self.company,
            title="Legacy Archived Engineer",
            department="Engineering",
            description="Maintain reliable archived hiring platform services.",
            required_skills=["Python"],
        )
        restored = self.client.post(f"/api/archived-jobs/{legacy_archive.id}/restore/")
        self.assertEqual(restored.status_code, 201)
        self.assertEqual(restored.data["status"], JobStatus.DRAFT)
