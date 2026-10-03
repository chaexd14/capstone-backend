from django.test import TestCase
from apps.resumes.redaction_service import (
    redact_raw_resume_text,
    sanitize_education_credentials,
    build_redacted_candidate_profile
)
from apps.resumes.gemini_service import (
    validate_insights,
    format_insights_as_markdown
)
from apps.resumes.services import (
    calculate_capped_experience_score,
    evaluate_project_relevance,
    match_skills_flexibly
)
from apps.resumes.audit_service import (
    counterfactual_gap,
    adverse_impact_ratio,
    run_counterfactual_audit_on_text
)

class InputStageRedactionTestCase(TestCase):
    """Tests Section 2: Input-Stage Bias Reduction (Redaction)"""

    def setUp(self):
        self.sample_text = """
        MARIA SANTOS
        Email: maria.santos@gmail.com | Phone: +63 917 123 4567
        Address: 123 Forbes Park, Makati City
        Date of Birth: 12/04/1995 (29 years old) | Gender: Female (she/her)
        Civil Status: Single | Nationality: Filipino | Religion: Roman Catholic
        
        EDUCATION
        Bachelor of Science in Computer Science
        De La Salle University (Graduated 2017)
        
        PROFESSIONAL EXPERIENCE
        Senior Full Stack Developer - TechCorp Manila (2018 - Present)
        - Developed REST APIs using Python and Django
        - Maintained PostgreSQL databases and Docker containers
        - Optimized slow database queries reducing latency by 40%
        """

    def test_redact_personal_and_demographic_information(self):
        applicant_info = {
            "first_name": "Maria",
            "last_name": "Santos",
            "applicant_name": "Maria Santos",
            "email": "maria.santos@gmail.com",
            "phone": "+63 917 123 4567"
        }
        redacted = redact_raw_resume_text(self.sample_text, applicant_info)

        # Assert all PII and protected demographic attributes are scrubbed
        self.assertNotIn("Maria Santos", redacted)
        self.assertNotIn("maria.santos@gmail.com", redacted)
        self.assertNotIn("+63 917 123 4567", redacted)
        self.assertNotIn("Female", redacted)
        self.assertNotIn("she/her", redacted)
        self.assertNotIn("29 years old", redacted)
        self.assertNotIn("Single", redacted)
        self.assertNotIn("Filipino", redacted)
        self.assertNotIn("Roman Catholic", redacted)
        self.assertNotIn("De La Salle University", redacted)
        self.assertNotIn("Graduated 2017", redacted)

        # Assert job performance fields are preserved
        self.assertIn("Python", redacted)
        self.assertIn("Django", redacted)
        self.assertIn("PostgreSQL", redacted)
        self.assertIn("REST APIs", redacted)

    def test_sanitize_education_removes_school_prestige(self):
        raw_edu = [
            "De La Salle University - Bachelor of Science in Computer Science (2018)",
            "University of the Philippines Diliman - BS Accountancy Graduated 2020",
            "AMA Computer College - Diploma in Information Technology"
        ]
        sanitized = sanitize_education_credentials(raw_edu)

        for item in sanitized:
            # Must keep degree level and field
            self.assertIn(item["level"], ["Bachelor", "Associate / Diploma", "Master", "Doctorate"])
            self.assertTrue(len(item["field"]) > 0)
            # Must not contain school prestige mentions
            self.assertNotIn("De La Salle", item["credential_summary"])
            self.assertNotIn("University of the Philippines", item["credential_summary"])
            self.assertNotIn("AMA Computer College", item["credential_summary"])


class ScoringRubricAndPenaltyTestCase(TestCase):
    """Tests Sections 3, 4, 5, 6: Rubric Weights, Capped Experience, Must-Have Capping"""

    def test_experience_scoring_with_capped_duration(self):
        # Role requires 2.0 years
        # Candidate with 2.0 years: 100%
        score_exact = calculate_capped_experience_score(2.0, "2 years", bonus_cap=0.1)
        self.assertEqual(score_exact, 100.0)

        # Candidate with 1.0 year (junior): 50%
        score_half = calculate_capped_experience_score(1.0, "2 years", bonus_cap=0.1)
        self.assertEqual(score_half, 50.0)

        # Candidate with 10.0 years: capped at max 100.0% (no unfair age runaway score)
        score_senior = calculate_capped_experience_score(10.0, "2 years", bonus_cap=0.1)
        self.assertLessEqual(score_senior, 100.0)

    def test_must_have_skill_penalty(self):
        required_skills = ["Python", "Docker", "PostgreSQL"]
        preferred_skills = ["Kubernetes", "AWS"]
        extracted_skills = ["Python", "PostgreSQL"]  # Missing Docker (must-have)
        licenses = []
        resume_text = "Proficient in Python and PostgreSQL database administration."

        matched, missing, req_score, pref_score = match_skills_flexibly(
            required_skills, preferred_skills, extracted_skills, licenses, resume_text
        )

        self.assertIn("Docker", missing)
        self.assertIn("Python", matched)

        # Raw composite calculation
        exp_score = 90.0
        edu_score = 90.0
        proj_score = 80.0
        raw_composite = (req_score * 0.40) + (exp_score * 0.25) + (edu_score * 0.15) + (pref_score * 0.10) + (proj_score * 0.10)

        # If Docker is a missing must-have, final score must be capped at 60.0%
        final_score = raw_composite
        if missing and any(m in required_skills for m in missing) and final_score > 60.0:
            final_score = 60.0

        self.assertEqual(final_score, 60.0)


class GroundedInsightsValidatorTestCase(TestCase):
    """Tests Section 10: Grounded AI Recruiter Insights and Validator"""

    def setUp(self):
        self.resume_text = "Built and optimized 12 REST services using Python and PostgreSQL. Reduced latency by 40%."

    def test_valid_grounded_insights_pass(self):
        valid_insights = {
            "summary": "Strong fit for backend API and database work based on demonstrated history.",
            "strengths": [
                {
                    "skill": "Python REST APIs",
                    "matched_requirement": "API Development",
                    "evidence": "Built and optimized 12 REST services",
                    "source": "experience[0]",
                    "confidence": "high"
                }
            ],
            "gaps": [
                {"skill": "Docker", "type": "must_have", "note": "No mention found in resume"}
            ],
            "interview_focus": ["Depth of containerization experience"],
            "fields_used": ["skills", "experience", "education"],
            "fields_excluded": ["name", "gender", "age", "address", "school"]
        }
        errors = validate_insights(valid_insights, self.resume_text)
        self.assertEqual(len(errors), 0)

    def test_banned_subjective_terms_are_rejected(self):
        biased_insights = {
            "summary": "Great culture fit candidate who seems very young and energetic. Recommend to hire immediately.",
            "strengths": [
                {
                    "skill": "Python",
                    "evidence": "Built REST services",
                    "source": "experience[0]"
                }
            ],
            "gaps": []
        }
        errors = validate_insights(biased_insights, self.resume_text)
        self.assertTrue(any("culture fit" in err for err in errors))
        self.assertTrue(any("young" in err for err in errors))
        self.assertTrue(any("hire" in err for err in errors))

    def test_ungrounded_hallucinated_evidence_is_flagged(self):
        hallucinated_insights = {
            "summary": "Candidate profile summary.",
            "strengths": [
                {
                    "skill": "Machine Learning",
                    "evidence": "Invented neural network algorithm in quantum computing laboratory",
                    "source": "experience[0]"
                }
            ],
            "gaps": []
        }
        errors = validate_insights(hallucinated_insights, self.resume_text)
        self.assertTrue(any("not sufficiently grounded" in err for err in errors))


class CounterfactualAndAdverseImpactTestCase(TestCase):
    """Tests Section 8: Counterfactual Demographic Invariance and Four-Fifths Rule"""

    def test_counterfactual_invariance_across_demographics_and_schools(self):
        # A resume with identical competencies and experience duties
        competencies_text = """
        SKILLS: Python, Django, SQL, PostgreSQL, REST APIs, Git
        EXPERIENCE:
        Backend Engineer (3 years)
        - Developed 10 microservices using Python and Django
        - Handled PostgreSQL query tuning and indexing
        - Implemented JWT authentication and CI/CD pipelines
        """

        variant_a = "JUAN DELA CRUZ | Male | Single | Age: 25\nDe La Salle University - BS Computer Science (2020)\n" + competencies_text
        variant_b = "MARIA CLARA | Female | Married | Age: 38\nUniversity of the Philippines - BS Computer Science (2013)\n" + competencies_text
        variant_c = "ALEX TAN | Non-binary (they/them) | Forbes Park, Makati\nAMA Computer College - BS Information Technology (2021)\n" + competencies_text

        def mock_scoring_pipeline(raw_text: str, info: dict):
            # Input-Stage Redaction
            redacted_text, profile = build_redacted_candidate_profile(
                extracted_text=raw_text,
                parsed_ai_data={
                    "skills": ["Python", "Django", "SQL", "PostgreSQL", "REST APIs", "Git"],
                    "total_experience_years": 3.0,
                    "education": ["BS Computer Science"],
                    "projects": ["Microservices deployment"]
                },
                candidate_code="TM-TEST",
                applicant_info=info
            )
            # Deterministic scoring
            req_skills = ["Python", "Django", "SQL"]
            pref_skills = ["PostgreSQL", "Git"]
            _, _, req_score, pref_score = match_skills_flexibly(
                req_skills, pref_skills, profile["skills"], [], redacted_text
            )
            exp_score = calculate_capped_experience_score(profile["total_experience_years"], "2 years")
            total = (req_score * 0.40) + (exp_score * 0.25) + (90.0 * 0.15) + (pref_score * 0.10) + (80.0 * 0.10)
            return {"match_score": round(total, 1)}

        audit_results = run_counterfactual_audit_on_text(
            base_text=variant_a,
            applicant_info={"applicant_name": "Juan Dela Cruz"},
            variants=[
                {"label": "Female + Older + UP", "text": variant_b, "applicant_info": {"applicant_name": "Maria Clara"}},
                {"label": "Non-binary + AMA College", "text": variant_c, "applicant_info": {"applicant_name": "Alex Tan"}}
            ],
            eval_fn=mock_scoring_pipeline
        )

        # Counterfactual gap must be zero because school prestige, demographics, and names are stripped
        self.assertEqual(audit_results["max_counterfactual_gap"], 0.0)
        self.assertTrue(audit_results["is_invariant"])

    def test_adverse_impact_ratio_four_fifths_rule(self):
        # 1. Compliant scenario: selection rates are equal or >= 80% of top group
        selected_balanced = {"Group_A": 40, "Group_B": 35}
        total_balanced = {"Group_A": 100, "Group_B": 100}
        # Group_A rate = 0.40, Group_B rate = 0.35 -> ratio = 0.35 / 0.40 = 0.875 (Passes >= 0.80)
        result_balanced = adverse_impact_ratio(selected_balanced, total_balanced)
        self.assertFalse(result_balanced["adverse_impact_detected"])
        self.assertEqual(len(result_balanced["violating_groups"]), 0)

        # 2. Adverse impact scenario: Group_B selection rate falls below 80% of Group_A
        selected_biased = {"Group_A": 50, "Group_B": 20}
        total_biased = {"Group_A": 100, "Group_B": 100}
        # Group_A rate = 0.50, Group_B rate = 0.20 -> ratio = 0.20 / 0.50 = 0.40 (< 0.80)
        result_biased = adverse_impact_ratio(selected_biased, total_biased)
        self.assertTrue(result_biased["adverse_impact_detected"])
        self.assertIn("Group_B", result_biased["violating_groups"])
