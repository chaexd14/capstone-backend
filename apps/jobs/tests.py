from django.test import TestCase
from rest_framework.test import APIClient
from apps.jobs.models import Job, JobStatus
from apps.jobs.services import (
    clean_biased_text,
    detect_is_regulated_role,
    lint_job_requisition,
    generate_job_requisition_with_gemini
)

class JobCalibrationStudioTests(TestCase):
    def setUp(self):
        self.client = APIClient()

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
            required_skills=["Patient Care", "IV Therapy"] # Missing PRC RN
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
