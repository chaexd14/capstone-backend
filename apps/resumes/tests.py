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
    match_skills_flexibly,
    evaluate_education_relevance,
    calculate_semantic_content_score
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

    def test_sentence_qualification_matching_preserves_casing_and_scores_domain_evidence(self):
        required_skills = [
            "Hands-on experience building REST APIs with Python and Django",
            "Experience managing enterprise B2B sales pipelines and client accounts",
        ]
        preferred_skills = [
            "Familiarity with Docker containerization and CI/CD workflows",
        ]
        resume_text = """
        PROFESSIONAL EXPERIENCE
        Senior Backend Engineer (2021 - Present)
        - Built and maintained production REST APIs using Python and Django for 50k daily users.
        - Containerized backend services using Docker and automated GitHub Actions CI/CD pipelines.
        """
        matched, missing, req_score, pref_score = match_skills_flexibly(
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            extracted_skills=["Python", "Django", "Docker"],
            licenses=[],
            resume_text=resume_text,
        )
        self.assertIn("Hands-on experience building REST APIs with Python and Django", matched)
        self.assertIn("Experience managing enterprise B2B sales pipelines and client accounts", missing)
        self.assertIn("Familiarity with Docker containerization and CI/CD workflows (Preferred)", matched)
        self.assertEqual(req_score, 50.0)
        self.assertGreaterEqual(pref_score, 90.0)

    def test_unrelated_education_returns_zero_for_regulated_roles(self):
        # BS Information Technology applying for BS Nursing / PRC RN license
        edu_score = evaluate_education_relevance(
            required_edu="BS Nursing with active PRC Registered Nurse (RN) license",
            candidate_degrees=["Bachelor of Science in Information Technology"],
            candidate_licenses=[],
            resume_text="Bachelor of Science in Information Technology"
        )
        self.assertEqual(edu_score, 0.0)

    def test_zero_match_project_relevance_returns_zero(self):
        # IT web projects evaluated against clinical nursing skills
        proj_score = evaluate_project_relevance(
            projects=["Parkada: RTU Parking Ticketing C++", "Spendlyy: Financial Web App"],
            job_desc="ICU patient care and clinical vitals monitoring",
            required_skills=["Patient Care", "IV Therapy", "Vital Signs Monitoring"]
        )
        self.assertEqual(proj_score, 0.0)

    def test_unrelated_semantic_score_returns_zero(self):
        # Pure IT help desk text evaluated against Nurse role
        sem_score = calculate_semantic_content_score(
            job_title="Registered Staff Nurse",
            job_desc="Provide clinical nursing care, IV infusion, and medication administration.",
            resume_text="Configured routers, crimped RJ45 Ethernet cables, Active Directory help desk."
        )
        self.assertEqual(sem_score, 0.0)


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


class ResumeSubmissionPipelineTestCase(TestCase):
    """Verifies on-commit worker dispatch and in-memory payload cache."""

    def test_on_commit_dispatch_and_payload_cache_complete_evaluation(self):
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from apps.companies.models import Company
        from apps.jobs.models import Job, JobStatus
        from apps.applications.serializers import ApplicationCreateSerializer
        from apps.resumes.models import ResumeWork
        from apps.resumes.tasks import enqueue_resume_processing, _drain_resume_work_in_background

        company = Company.objects.create(name="Fast Eval Corp")
        job = Job.objects.create(
            company=company,
            title="Backend Python Engineer",
            department="Engineering",
            description="Build REST APIs with Python, Django, and PostgreSQL.",
            minimum_experience="2 years",
            education_requirement="BS Computer Science",
            required_skills=["Python", "Django", "PostgreSQL"],
            preferred_skills=["Docker"],
            status=JobStatus.PUBLISHED,
        )
        resume_bytes = (
            b"EXPERIENCE\n"
            b"Backend Engineer (2021 - Present)\n"
            b"- Built production REST APIs with Python, Django, and PostgreSQL.\n"
            b"- Containerized services using Docker.\n"
            b"EDUCATION\n"
            b"Bachelor of Science in Computer Science\n"
        )
        upload = SimpleUploadedFile("resume.txt", resume_bytes, content_type="text/plain")
        serializer = ApplicationCreateSerializer(data={
            "job": job.pk,
            "first_name": "Juan",
            "last_name": "Dela Cruz",
            "email": "juan@example.com",
            "phone": "+63 917 123 4567",
            "resume": upload,
        })
        with override_settings(
            ENABLE_GEMINI_PROCESSING=False,
            REQUIRE_RESUME_SCAN=False,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            self.assertTrue(serializer.is_valid(), serializer.errors)
            application = serializer.save()
            with patch("apps.resumes.tasks._resume_worker_pool.submit", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                with patch.object(application.resume.file, "open", side_effect=AssertionError("Should use in-memory cache")):
                    with self.captureOnCommitCallbacks(execute=True):
                        enqueue_resume_processing(application.pk)

        application.refresh_from_db()
        application.resume.refresh_from_db()
        self.assertEqual(application.resume.processing_status, "COMPLETED")
        self.assertEqual(application.status, "UNDER_REVIEW")
        self.assertFalse(ResumeWork.objects.filter(target=str(application.pk)).exists())
        self.assertTrue(hasattr(application, "match_result"))
        self.assertGreater(application.match_result.match_score, 70.0)


class HolisticAndProofAwareEvaluationTestCase(TestCase):
    """Tests holistic multi-section evaluation, proof-aware vs general mention matching, quantitative WPM/tenure, software suites, and multi-option requirements."""

    def test_carl_newman_non_voice_customer_service_holistic_evaluation(self):
        from apps.resumes.services import (
            extract_education_from_text,
            extract_experience_years,
            extract_licenses_from_text,
            parse_resume_evidence_units,
            evaluate_requirement_details,
            match_skills_flexibly,
        )

        carl_resume_text = """
        Carl Newman
        Customer Service Manager
        email@email.com | (541) 754-3010
        Summary
        Dedicated Customer Service Manager with 5+ years of experience in improving customer satisfaction and retention. Proven track record in reducing customer service response times by 35% and increasing customer retention by 45%.
        Experience
        Customer Service Pty Ltd
        Customer Service Manager
        July 2015–Present
        •
        Managed a team of 15 customer service representatives, improving service quality and efficiency.
        •
        Reduced average customer service response time by 35% through the implementation of a new ticketing system.
        •
        Key Achievement: Implemented a new training program that increased customer retention by 45%.
        Client Care Pty Ltd
        Customer Service Representative
        Jan 2013–June 2015
        •
        Handled customer complaints and inquiries via phone and email, resolving 95% of issues on the first contact.
        •
        Key Achievement: Recognized as ‘Employee of the Month’ three times for achieving a 90% customer satisfaction rating.
        Education
        Bachelor of Arts in Communication
        University of Washington
        Sept 2008–June 2012
        •
        Graduated with a 3.7 GPA.
        •
        Active member of the University Debate Team and Communication Club.
        Skills
        Proficient in CRM software (Salesforce, Zendesk), Strong communication and interpersonal skills, Excellent problem-solving and conflict resolution abilities, Experience in team leadership and training, Proficient in Microsoft Office Suite (Word, Excel, PowerPoint), Strong time management and organizational skills
        Certifications
        Certified Customer Service Professional (CCSP) — National Customer Service Association
        Awards
        •
        Top Customer Service Representative Award — Client Care Pty Ltd
        Memberships
        •
        Member of the Customer Service Manager Association (CSMA), since 2016.
        •
        Presented a talk on ‘Efficiency in Customer Service’ at the 2018 National Customer Service Conference.
        Languages
        •
        English—Native
        •
        Spanish—Intermediate
        """

        redacted = redact_raw_resume_text(carl_resume_text, {"applicant_name": "Carl Newman", "email": "email@email.com"})
        # Degree must NOT be eaten by school redaction
        self.assertIn("Bachelor of Arts in Communication", redacted)
        self.assertNotIn("University of Washington", redacted)
        self.assertNotIn("Carl Newman", redacted)

        degrees = extract_education_from_text(redacted)
        self.assertTrue(any("Bachelor of Arts in Communication" in d for d in degrees))

        exp_years = extract_experience_years(redacted)
        self.assertGreaterEqual(exp_years, 10.0)

        certs = extract_licenses_from_text(redacted)
        self.assertTrue(any("CCSP" in c or "Certified Customer Service Professional" in c for c in certs))

        required_skills = [
            "At least six (6) months of customer service experience e-commerce experience is preferred.",
            "Prior experience managing multiple chat sessions is a plus (for online chat business only).",
            "High school graduate or higher.",
            "Fluent written English, with little to no discernible accent.",
            "Typing speed: 50 WPM or faster.",
            "Willing to work on a shifting schedule (including night shifts and weekends).",
        ]

        units = parse_resume_evidence_units(redacted, licenses=certs)
        details = [
            evaluate_requirement_details(r, units, redacted, exp_years=exp_years, education_list=degrees)
            for r in required_skills
        ]

        # First 4 requirements must be MET with real quotes from the resume
        for idx in range(4):
            self.assertEqual(details[idx]["status"], "met", f"Requirement {required_skills[idx]} should be met")
            self.assertTrue(len(details[idx]["evidence"]) > 5)

        # Unstated typing speed and shifting schedule must be flagged verify_in_screening (not penalized)
        self.assertEqual(details[4]["status"], "verify_in_screening")
        self.assertEqual(details[5]["status"], "verify_in_screening")

        matched, missing, req_score, _ = match_skills_flexibly(
            required_skills, [], [], certs, redacted
        )
        self.assertEqual(len(missing), 0)
        self.assertGreaterEqual(req_score, 90.0)

    def test_wpm_microsoft_suite_and_multi_option_matching(self):
        from apps.resumes.services import parse_resume_evidence_units, evaluate_requirement_details

        resume_text = """
        SUMMARY
         Operations and technical specialist who can type up to 70wpm with high accuracy.
        EXPERIENCE
        Junior Developer & Support Specialist (2023 - Present)
        - Created automated reports and spreadsheets using Microsoft Excel and Word.
        PROJECTS
        - Inventory Tracker: Developed backend service using Python and SQLite.
        CERTIFICATIONS
        - Certificate of Proficiency in Microsoft Office Software
        - Certified JavaScript Developer Course
        """
        units = parse_resume_evidence_units(resume_text)

        # 1. Quantitative WPM ("Typing speed: 50 WPM or faster." vs "can type up to 70wpm")
        wpm_res = evaluate_requirement_details("Typing speed: 50 WPM or faster.", units, resume_text, exp_years=2.0)
        self.assertEqual(wpm_res["status"], "met")
        self.assertGreaterEqual(wpm_res["credit"], 0.85)
        self.assertIn("70wpm", wpm_res["evidence"].lower())

        # 2. Microsoft software suite ("Proficient and Knowledgeable in Microsoft software")
        ms_res = evaluate_requirement_details("Proficient and Knowledgeable in Microsoft software", units, resume_text, exp_years=2.0)
        self.assertEqual(ms_res["status"], "met")
        self.assertEqual(ms_res["credit"], 1.0)

        # 3. Multi-option ("Develop using Python, Java") matched via Python project or JavaScript cert
        py_java_res = evaluate_requirement_details("Develop using Python, Java", units, resume_text, exp_years=2.0)
        self.assertEqual(py_java_res["status"], "met")
        self.assertEqual(py_java_res["credit"], 1.0)

    def test_zero_pii_and_location_redaction_gate(self):
        from apps.resumes.redaction_service import ensure_zero_pii_payload

        raw_resume = """CARL A. NEWMAN JR.
Portland, OR • (123) 456-7891 • cnewman@email.com
26 years old | Male | Single | Filipino | Born on May 14, 1998 | Pronouns: he/him
Address: 123 Ayala Avenue, Brgy. San Antonio, Makati City, Metro Manila, Philippines
SSS No: 34-1234567-8 | Religion: Roman Catholic

Summary
Customer Service Representative in Quezon City and Seattle, WA serving as single point of contact and managing Single Sign-On (SSO) tickets.
Honored by Mr. Robert Smith during annual review.

Education
Bachelor of Arts in Communication
University of Oregon | Portland, OR

Character References
Jane Doe - Manager - (555) 234-5678 - Cebu City
"""
        clean_text, clean_profile = ensure_zero_pii_payload(
            text=raw_resume,
            profile={
                "candidate_code": "TM-0099",
                "experience": [
                    {
                        "title": "Customer Service Representative",
                        "company": "Fintract Global",
                        "location": "Portland, OR",
                        "duties": "Handled customer chats in Portland, OR and Makati City for Carl Newman."
                    }
                ]
            }
        )

        # Verify all personal names, locations, ages, demographics, IDs, and references are stripped
        self.assertNotIn("CARL", clean_text.upper())
        self.assertNotIn("NEWMAN", clean_text.upper())
        self.assertNotIn("Portland, OR", clean_text)
        self.assertNotIn("Seattle, WA", clean_text)
        self.assertNotIn("Makati", clean_text)
        self.assertNotIn("Quezon City", clean_text)
        self.assertNotIn("Metro Manila", clean_text)
        self.assertNotIn("Philippines", clean_text)
        self.assertNotIn("26 years old", clean_text)
        self.assertNotIn("May 14, 1998", clean_text)
        self.assertNotIn("he/him", clean_text)
        self.assertNotIn("Roman Catholic", clean_text)
        self.assertNotIn("34-1234567-8", clean_text)
        self.assertNotIn("Robert Smith", clean_text)
        self.assertNotIn("Jane Doe", clean_text)
        self.assertNotIn("Cebu City", clean_text)

        # Verify job-relevant terms and "Single Sign-On" / "single point of contact" are preserved
        self.assertIn("Bachelor of Arts in Communication", clean_text)
        self.assertIn("single point of contact", clean_text)
        self.assertIn("Single Sign-On", clean_text)

        # Verify structured profile strips company & location
        exp_entry = clean_profile["experience"][0]
        self.assertNotIn("company", exp_entry)
        self.assertNotIn("location", exp_entry)
        self.assertEqual(exp_entry["organization"], "Organization #1")
        self.assertNotIn("Portland, OR", exp_entry["duties"])
        self.assertNotIn("Makati", exp_entry["duties"])
        self.assertNotIn("Newman", exp_entry["duties"])
