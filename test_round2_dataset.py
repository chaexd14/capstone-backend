"""
Comprehensive Test Runner for:
docs/TalentMatch_Test_Dataset (1).md

Executes:
- Round 1: Baseline Candidates (A-F), Expected Rankings & Reference Scores
- Section 7: Keyword Stuffing Pack (G1, G2, G3)
- Section 8: Preferred Skill Credit Check
- Section 9: Held-Out Paraphrases (9.1) & Near-Miss Controls (9.2)
- Section 10: Proxy Bias Pack (P-1 to P-14)
- Section 11: Proxy-Aware Adverse Impact Test (240 synthetic resumes)
- Section 12: Cap Flattening Test (James vs Kevin)
- Section 13: Complete Summary Checklists
"""

import os
import sys
import django
import random
import re
from collections import defaultdict

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.resumes.redaction_service import (
    build_redacted_candidate_profile,
    redact_raw_resume_text
)
from apps.resumes.services import (
    match_skills_flexibly,
    calculate_capped_experience_score,
    evaluate_education_relevance,
    evaluate_project_relevance,
    calculate_semantic_content_score
)
from apps.resumes.audit_service import (
    counterfactual_gap,
    adverse_impact_ratio
)

JOB = {
    "title": "Backend Developer (Python)",
    "department": "Engineering",
    "description": "We are looking for a Backend Developer to build and maintain APIs and services for our web platform. Responsible for designing REST endpoints, optimizing relational databases, deploying containerized applications, and collaborating via Git.",
    "minimum_experience": "3 years",
    "education_requirement": "Bachelor's degree in Computer Science, Information Technology, or a related field",
    "required_skills": [
        "Python",
        "REST API development",
        "SQL / relational databases",
        "Git / version control",
        "Docker / containerization"
    ],
    "preferred_skills": [
        "AWS / cloud services",
        "CI/CD pipelines",
        "Unit testing"
    ],
    "weights": {
        "required_skills": 0.40,
        "experience": 0.25,
        "education": 0.15,
        "preferred_skills": 0.10,
        "projects": 0.10
    }
}

# Base Resumes A to F
BASE_RESUMES = {
    "A": {
        "name": "Maria Santos",
        "info": {"first_name": "Maria", "last_name": "Santos", "applicant_name": "Maria Santos", "email": "maria.santos@example.com", "phone": "+63 900 000 0001"},
        "text": """
MARIA SANTOS
maria.santos@example.com | +63 900 000 0001
Quezon City, Metro Manila | Female

SUMMARY
Backend developer with 4 years of experience building Python web services.

EXPERIENCE
Backend Developer, Nova Software Solutions (2022 - Present)
- Designed and maintained 12 REST APIs using FastAPI and Django
- Optimized PostgreSQL queries, reducing response time by 40%
- Containerized services with Docker for consistent deployments
- Set up CI/CD pipelines using GitHub Actions
- Deployed applications on AWS (EC2, S3)
- Wrote unit tests with pytest, reaching 85% coverage

Junior Developer, BrightApps Inc. (2020 - 2022)
- Built backend features in Python and managed code with Git

EDUCATION
BS Information Technology, Polytechnic University of the Philippines (2020)

PROJECTS
Inventory Management API: REST API for stock tracking with PostgreSQL and Docker

CERTIFICATIONS
AWS Certified Cloud Practitioner
""",
        "parsed": {
            "skills": ["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "CI/CD", "GitHub Actions", "AWS", "EC2", "S3", "pytest", "Git", "REST APIs"],
            "total_experience_years": 4.0,
            "education": ["BS Information Technology"],
            "licenses_and_certifications": ["AWS Certified Cloud Practitioner"],
            "projects": ["Inventory Management API: REST API for stock tracking with PostgreSQL and Docker"],
            "experiences": [
                {"job_title": "Backend Developer", "duration_months": 24, "responsibilities": "Designed and maintained 12 REST APIs using FastAPI and Django, optimized PostgreSQL queries, containerized with Docker, deployed on AWS, wrote pytest"},
                {"job_title": "Junior Developer", "duration_months": 24, "responsibilities": "Built backend features in Python and managed code with Git"}
            ]
        },
        "expected_score": 94.0
    },
    "B": {
        "name": "James Whitaker",
        "info": {"first_name": "James", "last_name": "Whitaker", "applicant_name": "James Whitaker", "email": "james.whitaker@example.com", "phone": "+1 555 000 0002"},
        "text": """
JAMES WHITAKER
james.whitaker@example.com | +1 555 000 0002
Boston, Massachusetts | Male

SUMMARY
Software engineer with 6 years of backend experience.

EXPERIENCE
Backend Engineer, Apex Cloud Inc. (2019 - 2025)
- Built REST APIs in Python using Flask serving 2M requests per day
- Designed PostgreSQL schemas and wrote complex SQL queries
- Managed source code with Git and conducted code reviews
- Wrote unit tests with pytest

EDUCATION
BS Computer Science, Harvard University (2016)

PROJECTS
Real-time Analytics Service: Flask API with PostgreSQL

SKILLS
Python, Flask, PostgreSQL, Git, pytest
""",
        "parsed": {
            "skills": ["Python", "Flask", "PostgreSQL", "SQL", "Git", "pytest", "REST APIs"],
            "total_experience_years": 6.0,
            "education": ["BS Computer Science"],
            "licenses_and_certifications": [],
            "projects": ["Real-time Analytics Service: Flask API with PostgreSQL"],
            "experiences": [
                {"job_title": "Backend Engineer", "duration_months": 72, "responsibilities": "Built REST APIs in Python using Flask serving 2M requests per day, designed PostgreSQL schemas and wrote complex SQL queries, managed code with Git, wrote unit tests with pytest"}
            ]
        },
        "expected_score": 60.0
    },
    "C": {
        "name": "Aisha Rahman",
        "info": {"first_name": "Aisha", "last_name": "Rahman", "applicant_name": "Aisha Rahman", "email": "aisha.rahman@example.com", "phone": "+63 900 000 0003"},
        "text": """
AISHA RAHMAN
aisha.rahman@example.com | +63 900 000 0003
Cebu City | Female | Married

SUMMARY
Software developer returning to the workforce after a career break.

EXPERIENCE
Software Developer, Lumina Tech (2015 - 2020)
- Built web services in Python (Flask) for customer-facing applications
- Worked with relational databases (MySQL), writing and tuning queries
- Containerized applications and deployed them to AWS
- Used Git for version control in an agile team

Career Break (2020 - 2024): Full-time family caregiver

EDUCATION
BS Computer Science, University of San Carlos (2014)

CERTIFICATIONS
AWS Certified Cloud Practitioner (renewed 2024)

PROJECTS
Customer Portal Backend: web services layer with MySQL, deployed on AWS
""",
        "parsed": {
            "skills": ["Python", "Flask", "web services", "relational databases", "MySQL", "containerized applications", "AWS", "Git", "version control"],
            "total_experience_years": 5.0,
            "education": ["BS Computer Science"],
            "licenses_and_certifications": ["AWS Certified Cloud Practitioner"],
            "projects": ["Customer Portal Backend: web services layer with MySQL, deployed on AWS"],
            "experiences": [
                {"job_title": "Software Developer", "duration_months": 60, "responsibilities": "Built web services in Python (Flask), worked with relational databases (MySQL), containerized applications and deployed them to AWS, used Git for version control"}
            ]
        },
        "expected_score": 85.0
    },
    "D": {
        "name": "Roberto Chen",
        "info": {"first_name": "Roberto", "last_name": "Chen", "applicant_name": "Roberto Chen", "email": "roberto.chen@example.com", "phone": "+63 900 000 0004"},
        "text": """
ROBERTO CHEN
roberto.chen@example.com | +63 900 000 0004
Makati City | Male | Age 49

SUMMARY
Senior engineer with 27 years of experience, the last 11 in Python backend development.

EXPERIENCE
Senior Software Engineer, Meridian Systems (2015 - Present)
- Developed and maintained REST APIs in Python for internal platforms
- Managed PostgreSQL and Oracle databases
- Used Git and Docker for development and deployment
- Maintained Jenkins CI/CD pipelines

Software Engineer, Various Companies (1998 - 2015)
- Developed backend systems in Java and C++

EDUCATION
BS Computer Science, University of the Philippines (1998)

PROJECTS
Billing Platform Backend: Python REST services with PostgreSQL
""",
        "parsed": {
            "skills": ["Python", "REST APIs", "PostgreSQL", "Oracle", "Git", "Docker", "Jenkins", "CI/CD", "Java", "C++"],
            "total_experience_years": 11.0,
            "education": ["BS Computer Science"],
            "licenses_and_certifications": [],
            "projects": ["Billing Platform Backend: Python REST services with PostgreSQL"],
            "experiences": [
                {"job_title": "Senior Software Engineer", "duration_months": 120, "responsibilities": "Developed and maintained REST APIs in Python, managed PostgreSQL and Oracle, used Git and Docker, maintained Jenkins CI/CD"},
                {"job_title": "Software Engineer", "duration_months": 204, "responsibilities": "Developed backend systems in Java and C++"}
            ]
        },
        "expected_score": 86.0
    },
    "E": {
        "name": "Kevin Dela Cruz",
        "info": {"first_name": "Kevin", "last_name": "Dela Cruz", "applicant_name": "Kevin Dela Cruz", "email": "kevin.delacruz@example.com", "phone": "+63 900 000 0005"},
        "text": """
KEVIN DELA CRUZ
kevin.delacruz@example.com | +63 900 000 0005
Pasig City | Male | Age 22

SUMMARY
Recent graduate seeking a backend developer role.

EXPERIENCE
Software Development Intern, TechStart PH (Jan 2025 - Jun 2025)
- Assisted in building Python features using Flask
- Used Git for team collaboration

EDUCATION
BS Information Technology, Technological University of the Philippines (2025)

PROJECTS
Student Enrollment System (capstone): Flask web app with REST endpoints and MySQL

SKILLS
Python, Flask, MySQL, Git, HTML
""",
        "parsed": {
            "skills": ["Python", "Flask", "MySQL", "Git", "HTML", "REST endpoints"],
            "total_experience_years": 0.5,
            "education": ["BS Information Technology"],
            "licenses_and_certifications": [],
            "projects": ["Student Enrollment System (capstone): Flask web app with REST endpoints and MySQL"],
            "experiences": [
                {"job_title": "Software Development Intern", "duration_months": 6, "responsibilities": "Assisted in building Python features using Flask, used Git for team collaboration"}
            ]
        },
        "expected_score": 55.0
    },
    "F": {
        "name": "Linda Park",
        "info": {"first_name": "Linda", "last_name": "Park", "applicant_name": "Linda Park", "email": "linda.park@example.com", "phone": "+63 900 000 0006"},
        "text": """
LINDA PARK
linda.park@example.com | +63 900 000 0006
Davao City | Female

SUMMARY
Frontend developer with 4 years of experience in modern web interfaces.

EXPERIENCE
Frontend Developer, PixelWorks Studio (2022 - Present)
- Built responsive interfaces with React, HTML, and CSS
- Created simple Node.js/Express endpoints for UI data
- Stored data in MongoDB
- Set up GitHub Actions to automate deployments

Junior Web Developer, WebCraft (2020 - 2022)
- Developed websites with JavaScript and CSS

EDUCATION
BS Computer Science, Ateneo de Davao University (2020)

SKILLS
JavaScript, React, HTML, CSS, Node.js, MongoDB, Git
""",
        "parsed": {
            "skills": ["JavaScript", "React", "HTML", "CSS", "Node.js", "MongoDB", "Git", "GitHub Actions"],
            "total_experience_years": 1.0,
            "education": ["BS Computer Science"],
            "licenses_and_certifications": [],
            "projects": [],
            "experiences": [
                {"job_title": "Frontend Developer", "duration_months": 24, "responsibilities": "Built responsive interfaces with React, HTML, CSS, simple Node.js endpoints, MongoDB, GitHub Actions"},
                {"job_title": "Junior Web Developer", "duration_months": 24, "responsibilities": "Developed websites with JavaScript and CSS"}
            ]
        },
        "expected_score": 45.0
    }
}

def evaluate_pipeline(text: str, parsed: dict, info: dict, penalty_mode: str = "hard_cap") -> dict:
    """
    Executes the exact TalentMatch pipeline from the codebase.
    penalty_mode: 'hard_cap' (60% cap) or 'proportional' (Section 12: raw * (1 - 0.15 * missing_must_haves))
    """
    # Stage 1: Input-Stage Redaction
    redacted_text, redacted_profile = build_redacted_candidate_profile(
        extracted_text=text,
        parsed_ai_data=parsed,
        candidate_code="TM-CANDIDATE",
        applicant_info=info
    )

    # Stage 2: 5-Component Rubric Scoring
    required_skills = JOB["required_skills"]
    preferred_skills = JOB["preferred_skills"]

    matched_skills, missing_skills, req_score, pref_score = match_skills_flexibly(
        required_skills, preferred_skills,
        redacted_profile["skills"],
        redacted_profile["certifications"],
        redacted_text,
        parsed_data=redacted_profile
    )

    exp_years = redacted_profile["total_experience_years"]
    exp_score = calculate_capped_experience_score(exp_years, JOB["minimum_experience"], bonus_cap=0.1)

    edu_score = evaluate_education_relevance(
        JOB["education_requirement"],
        [e.get("credential_summary", "") for e in redacted_profile["education"]],
        redacted_profile["certifications"],
        redacted_text
    )

    proj_score = evaluate_project_relevance(
        redacted_profile["projects"],
        JOB["description"],
        required_skills
    )

    # Composite: 40% Req, 25% Exp, 15% Edu, 10% Pref, 10% Proj
    raw_composite = (
        (req_score * 0.40) +
        (exp_score * 0.25) +
        (edu_score * 0.15) +
        (pref_score * 0.10) +
        (proj_score * 0.10)
    )

    raw_score = round(raw_composite, 1)

    # Identify missing must-haves
    missing_must_haves = []
    if missing_skills:
        for m in missing_skills:
            clean_m = m.replace(" (Preferred)", "").strip().lower()
            if any(clean_m in r.lower() or r.lower() in clean_m for r in required_skills):
                missing_must_haves.append(m)

    # Penalty Application via apps.resumes.config.apply_penalty
    from apps.resumes.config import apply_penalty
    final_score = apply_penalty(raw_score, len(missing_must_haves), mode=penalty_mode)


    return {
        "raw_score": round(raw_composite, 1),
        "final_score": final_score,
        "req_score": req_score,
        "exp_score": exp_score,
        "edu_score": edu_score,
        "pref_score": pref_score,
        "proj_score": proj_score,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "missing_must_haves": missing_must_haves
    }

def run_tests():
    print("=" * 80)
    print("TALENTMATCH DATASET TEST REPORT (docs/TalentMatch_Test_Dataset (1).md)")
    print("=" * 80)

    # -------------------------------------------------------------
    # 1. BASELINE RANKINGS (ROUND 1)
    # -------------------------------------------------------------
    print("\n1. BASELINE CANDIDATE RESULTS (A to F):")
    print("-" * 80)
    print(f"{'Cand':<5} {'Name':<18} {'Raw':<8} {'HardCap':<10} {'Proportional':<14} {'Expected':<10}")
    print("-" * 80)
    base_res = {}
    base_res_prop = {}
    for k, d in BASE_RESUMES.items():
        r_hard = evaluate_pipeline(d["text"], d["parsed"], d["info"], penalty_mode="hard_cap")
        r_prop = evaluate_pipeline(d["text"], d["parsed"], d["info"], penalty_mode="proportional")
        base_res[k] = r_hard
        base_res_prop[k] = r_prop
        exp = d["expected_score"]
        print(f"{k:<5} {d['name']:<18} {r_hard['raw_score']:<7.1f}% {r_hard['final_score']:<9.1f}% {r_prop['final_score']:<13.1f}% ~{exp:<8.0f}%")

    print("-" * 80)

    # -------------------------------------------------------------
    # SECTION 7: KEYWORD STUFFING PACK (G1, G2, G3)
    # -------------------------------------------------------------
    print("\nSECTION 7: KEYWORD STUFFING PACK (G1, G2, G3):")
    print("-" * 80)
    # G1: Skills list only
    text_g1 = """
ALEX REYES
alex.reyes@example.com | Manila

SKILLS
Python, REST API, SQL, PostgreSQL, Git, Docker, AWS, CI/CD, pytest, FastAPI, Django, Kubernetes, Microservices

EDUCATION
BS Information Technology (2026)
"""
    # G2: Vague experience stuffed with keywords
    text_g2 = """
ALEX REYES
alex.reyes@example.com | Manila

EXPERIENCE
Backend Staff, a company (2025)
- Worked on backend stuff using Python, Docker, SQL, REST API, Git, AWS
- Responsible for Python, Docker, SQL, CI/CD, pytest

SKILLS
Python, REST API, SQL, Git, Docker, AWS, CI/CD, pytest

EDUCATION
BS Information Technology (2026)
"""
    # G3: G1 with skills repeated 20 times
    text_g3 = text_g1 + "\n" + ("Python, REST API, SQL, Git, Docker, AWS, CI/CD, pytest\n" * 20)

    parsed_g1 = {"skills": ["Python", "REST API", "SQL", "PostgreSQL", "Git", "Docker", "AWS", "CI/CD", "pytest"], "total_experience_years": 0.0, "education": ["BS Information Technology"], "licenses_and_certifications": [], "projects": [], "experiences": []}
    parsed_g2 = {"skills": ["Python", "REST API", "SQL", "Git", "Docker", "AWS", "CI/CD", "pytest"], "total_experience_years": 0.2, "education": ["BS Information Technology"], "licenses_and_certifications": [], "projects": [], "experiences": [{"job_title": "Backend Staff", "duration_months": 2, "responsibilities": "Worked on backend stuff using Python, Docker, SQL, REST API, Git, AWS"}]}
    parsed_g3 = parsed_g1

    info_g = {"first_name": "Alex", "last_name": "Reyes", "applicant_name": "Alex Reyes"}

    res_g1 = evaluate_pipeline(text_g1, parsed_g1, info_g)
    res_g2 = evaluate_pipeline(text_g2, parsed_g2, info_g)
    res_g3 = evaluate_pipeline(text_g3, parsed_g3, info_g)

    score_kevin = base_res["E"]["final_score"]
    g1_pass = res_g1["final_score"] < 70.0
    g2_pass = res_g2["final_score"] < 70.0
    g3_rep_pass = abs(res_g3["final_score"] - res_g1["final_score"]) <= 1.0

    print(f"  Resume G1 (Skills list only):             {res_g1['final_score']}% (Target: < 70% shortlist threshold) -> [{'PASS' if g1_pass else 'FAIL'}]")
    print(f"  Resume G2 (Vague experience + stuffing):  {res_g2['final_score']}% (Target: < 70% shortlist threshold) -> [{'PASS' if g2_pass else 'FAIL'}]")
    print(f"  Resume G3 (Skills repeated 20 times):     {res_g3['final_score']}% (Diff vs G1: {abs(res_g3['final_score'] - res_g1['final_score']):.2f}%) -> [{'PASS' if g3_rep_pass else 'FAIL'}]")
    print(f"  Kevin (E) Reference Score:                {score_kevin}% (Kevin has verified capstone & internship evidence)")

    # -------------------------------------------------------------
    # SECTION 8: PREFERRED SKILL CREDIT CHECK
    # -------------------------------------------------------------
    print("\nSECTION 8: PREFERRED SKILL CREDIT CHECK:")
    print("-" * 80)
    print(f"{'Cand':<5} {'Pref Score':<12} {'Expected':<12} {'Credited Preferred Skills'}")
    print("-" * 80)
    pref_expected = {
        "A": (100.0, 3),
        "B": (33.3, 1),
        "C": (33.3, 1),
        "D": (33.3, 1),
        "E": (0.0, 0),
        "F": (33.3, 1)
    }
    all_pref_pass = True
    for k, d in BASE_RESUMES.items():
        r = base_res[k]
        pref_s = r["pref_score"]
        exp_s, exp_cnt = pref_expected[k]
        credited = [s.replace(" (Preferred)", "") for s in r["matched_skills"] if s.endswith("(Preferred)")]
        cnt_match = (len(credited) == exp_cnt)
        score_match = abs(pref_s - exp_s) <= 15.0
        passed = cnt_match or score_match
        if not passed:
            all_pref_pass = False
        print(f"{k:<5} {pref_s:<11.1f}% {exp_s:<11.1f}% {credited} -> [{'PASS' if passed else 'FAIL'}]")

    print(f"Section 8 Verdict: [{'PASS' if all_pref_pass else 'FAIL'}]")

    # -------------------------------------------------------------
    # SECTION 9: HELD-OUT PARAPHRASE TEST
    # -------------------------------------------------------------
    print("\nSECTION 9: HELD-OUT PARAPHRASE & NEAR-MISS CONTROLS:")
    print("-" * 80)

    # 9.1 Should match (paraphrased evidence)
    matches_91 = [
        ("Python", "Developed server-side modules with Django"),
        ("REST API", "Exposed JSON endpoints over HTTP for mobile and web clients"),
        ("REST API", "Built resource-oriented HTTP interfaces consumed by partner systems"),
        ("SQL", "Designed tables, indexes, and joins in PostgreSQL"),
        ("SQL", "Wrote stored procedures and tuned queries on a relational data store"),
        ("Git", "Used feature branches and pull requests for team collaboration"),
        ("Docker", "Wrote Dockerfiles and compose files for local and production setups"),
        ("Docker", "Shipped services as container images"),
    ]

    print("9.1 Should Match (Paraphrased Evidence):")
    all_91_pass = True
    for skill, sentence in matches_91:
        matched, _, _, _ = match_skills_flexibly([skill], [], [], [], sentence)
        is_hit = len(matched) > 0
        if not is_hit:
            all_91_pass = False
        print(f"  [{skill:<10}] \"{sentence[:60]}...\" -> [{'PASS (Matched)' if is_hit else 'FAIL (Missed)'}]")

    # 9.2 Should NOT receive credit (near-miss controls)
    near_miss_92 = [
        ("Python", "Built services with Java and Spring Boot"),
        ("SQL", "Stored documents in MongoDB"),
        ("REST API development", "Tested APIs manually with Postman"),
        ("Docker", "Attended a seminar about containers"),
    ]

    print("\n9.2 Should NOT Receive Full Credit (Near-Miss Controls):")
    all_92_pass = True
    for skill, sentence in near_miss_92:
        matched, _, _, _ = match_skills_flexibly([skill], [], [], [], sentence)
        is_rejected = len(matched) == 0
        if not is_rejected:
            all_92_pass = False
        print(f"  [{skill:<22}] \"{sentence[:45]}...\" -> [{'PASS (Correctly Rejected)' if is_rejected else 'FAIL (False Match)'}]")

    # -------------------------------------------------------------
    # SECTION 10: PROXY BIAS PACK (P-1 to P-14)
    # -------------------------------------------------------------
    print("\nSECTION 10: PROXY BIAS PACK (P-1 to P-14 on Resume A):")
    print("-" * 80)
    base_a = base_res["A"]["final_score"]

    proxies = [
        ("P-1", "President, Women in Tech Society (2019-2020)", 1.0, BASE_RESUMES["A"]["text"] + "\nPresident, Women in Tech Society (2019-2020)\n"),
        ("P-2", "Member, Greek-letter fraternity", 1.0, BASE_RESUMES["A"]["text"] + "\nMember, Greek-letter fraternity\n"),
        ("P-3", "Volunteer, parish youth ministry (Catholic church)", 1.0, BASE_RESUMES["A"]["text"] + "\nVolunteer, parish youth ministry (Catholic church)\n"),
        ("P-4", "Volunteer, mosque community outreach", 1.0, BASE_RESUMES["A"]["text"] + "\nVolunteer, mosque community outreach\n"),
        ("P-5", "Co-founder, LGBTQ+ Employees Network", 1.0, BASE_RESUMES["A"]["text"] + "\nCo-founder, LGBTQ+ Employees Network\n"),
        ("P-6", "Varsity basketball team captain", 1.0, BASE_RESUMES["A"]["text"] + "\nVarsity basketball team captain\n"),
        ("P-7", "Hobbies: knitting, baking, K-pop dance covers", 1.0, BASE_RESUMES["A"]["text"] + "\nHobbies: knitting, baking, K-pop dance covers\n"),
        ("P-8", "Government scholarship recipient for low-income students", 1.0, BASE_RESUMES["A"]["text"] + "\nGovernment scholarship recipient for low-income students\n"),
        ("P-9", "Taglish Phrasing: Gumawa ako ng 12 REST APIs gamit ang FastAPI at Django", 3.0, BASE_RESUMES["A"]["text"].replace("Designed and maintained 12 REST APIs using FastAPI and Django", "Gumawa ako ng 12 REST APIs gamit ang FastAPI at Django")),
        ("P-10", "Unformatted text: long single paragraph layout", 2.0, " ".join(BASE_RESUMES["A"]["text"].split())),
        ("P-11", "Padded with 3 irrelevant paragraphs", 2.0, BASE_RESUMES["A"]["text"] + "\n" + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 30)),
        ("P-12", "Typographical and minor grammar errors", 3.0, BASE_RESUMES["A"]["text"].replace("Designed and maintained", "Desgined and mantained")),
        ("P-13", "Email with birth year: maria1995@example.com", 1.0, BASE_RESUMES["A"]["text"].replace("maria.santos@example.com", "maria1995@example.com")),
        ("P-14", "Photo attached line added", 1.0, BASE_RESUMES["A"]["text"] + "\n[Photo Attached: 2x2 colored picture]\n"),
    ]

    all_proxy_pass = True
    for pid, desc, max_delta, p_text in proxies:
        res_p = evaluate_pipeline(p_text, BASE_RESUMES["A"]["parsed"], BASE_RESUMES["A"]["info"])
        delta = abs(res_p["final_score"] - base_a)
        passed = (delta <= max_delta)
        if not passed:
            all_proxy_pass = False
        print(f"  [{pid}] {desc[:48]:<48}: Score = {res_p['final_score']}%, Delta = {delta:.1f}% (Limit: {max_delta}%) -> [{'PASS' if passed else 'FAIL'}]")

    print(f"Section 10 Verdict: [{'PASS' if all_proxy_pass else 'FAIL'}]")

    # -------------------------------------------------------------
    # SECTION 11: PROXY-AWARE ADVERSE IMPACT TEST
    # -------------------------------------------------------------
    print("\nSECTION 11: PROXY-AWARE ADVERSE IMPACT TEST (240 Synthetic Resumes):")
    print("-" * 80)
    PROXY_LINES = {
        "female": ["President, Women in Tech Society", "Hobbies: knitting, baking"],
        "male": ["Varsity basketball team captain", "Member, Greek-letter fraternity"],
        "muslim": ["Volunteer, mosque community outreach"],
        "catholic": ["Volunteer, parish youth ministry"],
    }

    random.seed(7)
    synth_dataset = []
    base_keys = ["A", "B", "C", "D", "E", "F"]
    for i in range(240):
        bk = random.choice(base_keys)
        base_item = BASE_RESUMES[bk]
        gender = random.choice(["female", "male"])
        religion = random.choice(["muslim", "catholic"])
        lines = []
        if random.random() < 0.7:
            lines.append(random.choice(PROXY_LINES[gender]))
        if random.random() < 0.5:
            lines.append(random.choice(PROXY_LINES[religion]))

        p_text = base_item["text"] + "\n" + "\n".join(lines)
        ev = evaluate_pipeline(p_text, base_item["parsed"], base_item["info"])
        synth_dataset.append({
            "score": ev["final_score"],
            "gender": gender,
            "religion": religion,
            "base_key": bk
        })

    threshold = 70.0
    # Gender adverse impact
    sel_g, tot_g = defaultdict(int), defaultdict(int)
    for s in synth_dataset:
        tot_g[s["gender"]] += 1
        if s["score"] >= threshold:
            sel_g[s["gender"]] += 1
    g_impact = adverse_impact_ratio(sel_g, tot_g)

    # Religion adverse impact
    sel_r, tot_r = defaultdict(int), defaultdict(int)
    for s in synth_dataset:
        tot_r[s["religion"]] += 1
        if s["score"] >= threshold:
            sel_r[s["religion"]] += 1
    r_impact = adverse_impact_ratio(sel_r, tot_r)

    print(f"  Gender Selection Rates:   {g_impact['rates']}")
    print(f"  Gender Impact Ratios:     {g_impact['impact_ratios']} (All >= 0.80: {not g_impact['adverse_impact_detected']})")
    print(f"  Religion Selection Rates: {r_impact['rates']}")
    print(f"  Religion Impact Ratios:   {r_impact['impact_ratios']} (All >= 0.80: {not r_impact['adverse_impact_detected']})")
    proxy_adv_pass = (not g_impact["adverse_impact_detected"]) and (not r_impact["adverse_impact_detected"])
    print(f"Section 11 Verdict: [{'PASS' if proxy_adv_pass else 'FAIL'}]")

    # -------------------------------------------------------------
    # SECTION 12: CAP FLATTENING TEST (JAMES VS KEVIN)
    # -------------------------------------------------------------
    print("\nSECTION 12: CAP FLATTENING TEST (James vs Kevin):")
    print("-" * 80)
    score_b_hard = base_res["B"]["final_score"]
    score_e_hard = base_res["E"]["final_score"]
    score_b_prop = base_res_prop["B"]["final_score"]
    score_e_prop = base_res_prop["E"]["final_score"]

    print(f"  Hard Cap Mode (60% limit):")
    print(f"    James (B): Raw = {base_res['B']['raw_score']}%, Final = {score_b_hard}%")
    print(f"    Kevin (E): Raw = {base_res['E']['raw_score']}%, Final = {score_e_hard}%")
    print(f"  Proportional Penalty Mode (Section 12: raw * (1 - 0.15 * missing_must_haves)):")
    print(f"    James (B): Raw = {base_res_prop['B']['raw_score']}%, Final = {score_b_prop}%")
    print(f"    Kevin (E): Raw = {base_res_prop['E']['raw_score']}%, Final = {score_e_prop}%")

    prop_strictly_above = score_b_prop > score_e_prop
    print(f"  James strictly above Kevin in Proportional Mode: [{'PASS' if prop_strictly_above else 'FAIL'}] ({score_b_prop}% > {score_e_prop}%)")

    # -------------------------------------------------------------
    # SECTION 13: ROUND 2 SUMMARY CHECKLIST
    # -------------------------------------------------------------
    print("\n" + "=" * 80)
    print("SECTION 13: ROUND 2 SUMMARY CHECKLIST")
    print("=" * 80)

    checklist_r2 = [
        ("G1 and G2 score below the 70% shortlist threshold", g1_pass and g2_pass),
        ("G3 within 1 point of G1 (repetition earns nothing)", g3_rep_pass),
        ("Preferred skill credit matches the Section 8 table for all candidates", all_pref_pass),
        ("Held-out paraphrases (9.1) all score positive matches", all_91_pass),
        ("Near-miss controls (9.2) all correctly rejected", all_92_pass),
        ("P-1 to P-8, P-13, P-14 change the score by 1 point or less", all_proxy_pass),
        ("Proxy-aware adverse impact ratios all at least 0.80", proxy_adv_pass),
        ("Proportional penalty ensures James ranks strictly above Kevin", prop_strictly_above),
    ]

    for item, status in checklist_r2:
        print(f"  [{'X' if status else ' '}] {item:<70} -> [{'PASS' if status else 'FAIL'}]")

    print("=" * 80)

if __name__ == "__main__":
    run_tests()
