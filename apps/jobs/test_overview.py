from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.companies.models import Company
from apps.jobs.models import ArchivedJob, Job, JobStatus


class JobOverviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(name="Overview Test Company")
        self.recruiter = User.objects.create_user(username="overview_recruiter", email="overview@example.com")
        self.recruiter.role = "recruiter"
        UserRole.objects.create(user=self.recruiter, company=self.company, role="recruiter")
        self.client.force_authenticate(user=self.recruiter)
        self.client.credentials(
            HTTP_X_APP_CLIENT_KEY="talentmatch-client-key-2026",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def payload(self, **overrides):
        payload = {
            "title": "Overview Test Engineer",
            "department": "Engineering",
            "description": "Build and maintain dependable services for the recruitment platform.",
            "job_overview": "Join a collaborative engineering team improving how recruiters and applicants move through hiring.",
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

    def test_overview_is_trimmed_and_required_for_published_jobs(self):
        response = self.client.post(
            "/api/jobs/", self.payload(job_overview="  " + self.payload()["job_overview"] + "  "), format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["job_overview"], self.payload()["job_overview"])

        for overview in (None, "Too short", "x" * 1001):
            invalid = self.client.post(
                "/api/jobs/", self.payload(title=f"Invalid {len(overview or '')}", job_overview=overview), format="json"
            )
            self.assertEqual(invalid.status_code, 400)

    def test_draft_may_omit_overview_but_publishing_patch_requires_it(self):
        draft = self.client.post(
            "/api/jobs/", self.payload(status=JobStatus.DRAFT, job_overview=None), format="json"
        )
        self.assertEqual(draft.status_code, 201)
        publish = self.client.patch(
            f"/api/jobs/{draft.data['id']}/", {"status": JobStatus.PUBLISHED}, format="json"
        )
        self.assertEqual(publish.status_code, 400)
        self.assertIn("job_overview", publish.data)

    def test_archive_preserves_overview_and_legacy_restore_becomes_draft(self):
        created = self.client.post("/api/jobs/", self.payload(), format="json")
        archived = self.client.post(f"/api/jobs/{created.data['id']}/archive/")
        self.assertEqual(archived.data["job_overview"], self.payload()["job_overview"])

        legacy = ArchivedJob.objects.create(
            company=self.company,
            title="Legacy Archived Role",
            department="Engineering",
            description="Maintain dependable legacy recruitment services and systems.",
            required_skills=["Python"],
            salary_min=50000,
            salary_max=75000,
        )
        restored = self.client.post(f"/api/archived-jobs/{legacy.id}/restore/")
        self.assertEqual(restored.status_code, 201)
        self.assertEqual(restored.data["status"], JobStatus.DRAFT)

    def test_lint_returns_separate_cleaned_overview(self):
        response = self.client.post(
            "/api/jobs/lint-jd/",
            {
                "title": "Support Specialist",
                "description": "Resolve customer requests using documented support workflows.",
                "job_overview": "Join our young and energetic support team serving customers.",
                "required_skills": ["Customer support"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("young and energetic", response.data["cleaned_job_overview"].lower())
        self.assertEqual(response.data["cleaned_description"], "Resolve customer requests using documented support workflows.")
