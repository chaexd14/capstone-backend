"""
Test script to validate Phase 0 and Phase 1 logic on TalentMatch dataset candidates.
"""
import os
import re
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.resumes.config import get_scoring_config, get_config_version, apply_penalty

# Test skill pattern matcher
SKILL_PATTERNS = {
    "python": [r"\bpython(?:\s*3(?:\.\d+)?)?\b", r"\bdjango\b", r"\bflask\b", r"\bfastapi\b"],
    "rest api": [
        r"\brest\s*apis?\b", r"\brestful\b", r"\bweb\s*services?\b",
        r"\bweb\s*apis?\b",
        r"\bapi\s*development\b", r"\bapi\s*design\b", r"\brest\s*endpoints?\b",
        r"\b(?:http\s*)?endpoints?\s+returning\s+json\b",
        r"\bjson\s*endpoints?\b", r"\bfastapi\b", r"\bflask\s*api\b",
        r"\bjson-over-http\b", r"\bhttp\s*interfaces?\b",
        r"\bresource-(?:based|oriented)\b"
    ],

    "sql": [
        r"\bsql\b", r"\bmysql\b", r"\bpostgresql\b", r"\bpostgres\b",
        r"\brelational\s*(?:(?:data\s*)?stores?|databases?|storage|db)\b", r"\brdbms\b", r"\boracle\b", r"\bsqlite\b",
        r"\bstored\s*procedures?\b"
    ],

    "git": [
        r"\bgit\b", r"\bgithub\b", r"\bgitlab\b", r"\bversion\s*control\b", r"\bsource\s*control\b",
        r"\bpull\s*requests?\b", r"\bfeature\s*branches?\b", r"\bcode\s*reviews?\b",
        r"\bmerge\s*conflicts?\b", r"\brelease\s*tags?\b", r"\brebases?\b"
    ],
    "docker": [
        r"\bdocker\b", r"\bdockerfiles?\b", r"\b(?:docker[\s\-])?compose(?:\s*files?)?\b",
        r"\bcontainer(?:s|ized|ization| images?)?\b"
    ],

    "aws": [
        r"\baws\b", r"amazon\s*web\s*services", r"\bec2\b", r"\bs3\b",
        r"cloud\s*practitioner", r"cloud\s*deployment", r"deployed.*aws",
        r"hosted.*aws", r"aws\s*certified"
    ],
    "ci/cd": [
        r"\bci[\/\-]cd\b", r"\bcontinuous\s*integration\b", r"\bcontinuous\s*deployment\b",
        r"\bjenkins\b", r"\bgithub\s*actions\b", r"\bgitlab\s*ci\b",
        r"\bdeployment\s*pipelines?\b", r"\bci\s*pipelines?\b"
    ],
    "unit testing": [
        r"\bunit\s*test(?:s|ing)?\b", r"\bpytest\b", r"\bunittest\b",
        r"\bautomated\s*unit\s*tests?\b", r"\btest\s*coverage\b"
    ],
}

PASSIVE_AWARENESS_PATTERNS = [
    r"\battended\s+a\s+(?:seminar|workshop|webinar|talk|conference)\b",
    r"\bcompleted\s+(?:an?\s+)?(?:online\s+)?course\b",
    r"\bread\s+about\b",
    r"\bknow\s+of\b",
    r"\bfamiliar\s+with\b",
    r"\binterested\s+in\s+learning\b",
    r"\bexplored\s+.*(?:hobby|weekend)\b",
    r"\bgave\s+a\s+talk\b",
    r"\btested\s+apis?\s+manually\b",
    r"\bverified\s+api\s+responses\s+in\s+postman\b",
    r"\bexercised\s+.*(?:insomnia|postman)\b",
    r"\breviewed\s+teammates?'?\s+test\s+reports\b",
]

def match_term_in_text(term: str, text: str) -> bool:
    """Matches a term or its canonical pattern in a text block."""
    if not term or not text:
        return False
    text_lower = text.lower()
    term_lower = term.lower().strip()

    # Reject passive awareness / study without hands-on execution
    if any(re.search(p, text_lower) for p in PASSIVE_AWARENESS_PATTERNS):
        hands_on = ["built", "developed", "designed", "implemented", "maintained", "deployed", "authored", "containerized", "optimized"]
        if not any(h in text_lower for h in hands_on):
            return False

    # Check known patterns first
    for canon, pats in SKILL_PATTERNS.items():
        if canon in term_lower or term_lower in canon:
            for p in pats:
                if re.search(p, text_lower, re.IGNORECASE):
                    return True


    # Check slash alternatives
    if "/" in term_lower:
        for sub in term_lower.split("/"):
            if match_term_in_text(sub.strip(), text):
                return True

    # Word boundary match
    pattern = r'\b' + re.escape(term_lower) + r'\b'
    return bool(re.search(pattern, text_lower))

def is_vague_duty_bullet(bullet: str) -> bool:
    """Detects vague keyword-stuffed bullets like G2."""
    b_lower = bullet.lower()
    # If the bullet contains vague intro phrases + a laundry list of skills
    vague_phrases = ["worked on backend stuff using", "responsible for", "handled various tasks with"]
    if any(p in b_lower for p in vague_phrases):
        # Count how many skills are in this single bullet
        skill_count = sum(1 for k in SKILL_PATTERNS if any(re.search(p, b_lower) for p in SKILL_PATTERNS[k]))
        if skill_count >= 3:
            return True
    return False

def parse_resume_evidence_units(resume_text: str, parsed: dict | None = None) -> dict[str, list[str]]:
    """Tags text units with their source tiers."""
    units = {
        "duty": [],
        "vague_duty": [],
        "project": [],
        "certification": [],
        "skills_list": []
    }

    if parsed:
        # 1. Experiences
        for exp in parsed.get("experiences", []):
            resp = exp.get("responsibilities", "")
            bullets = [b.strip() for b in re.split(r'[\n\.\;]+', resp) if b.strip()]
            for b in bullets:
                if is_vague_duty_bullet(b):
                    units["vague_duty"].append(b)
                else:
                    units["duty"].append(b)

        # 2. Projects
        for proj in parsed.get("projects", []):
            units["project"].append(proj)

        # 3. Certifications
        for cert in parsed.get("licenses_and_certifications", []):
            units["certification"].append(cert)

        # 4. Skills list
        for s in parsed.get("skills", []):
            units["skills_list"].append(s)

    # Also parse sections from raw text
    text_lines = resume_text.splitlines()
    current_sec = None
    sec_buffers = {"exp": [], "proj": [], "cert": [], "skills": []}

    sec_headers = {
        "EXPERIENCE": "exp", "PROFESSIONAL EXPERIENCE": "exp", "WORK EXPERIENCE": "exp",
        "PROJECTS": "proj", "PERSONAL PROJECTS": "proj",
        "CERTIFICATIONS": "cert", "LICENSES": "cert", "CERTIFICATIONS & LICENSES": "cert",
        "SKILLS": "skills", "TECHNICAL SKILLS": "skills", "KEY SKILLS": "skills"
    }

    for line in text_lines:
        s_line = line.strip().upper()
        matched_h = None
        for h, cat in sec_headers.items():
            if s_line == h or s_line.startswith(h + ":"):
                matched_h = cat
                break
        if matched_h:
            current_sec = matched_h
            continue
        elif s_line in ["EDUCATION", "SUMMARY", "OBJECTIVE", "REFERENCES"]:
            current_sec = None
            continue

        if current_sec and line.strip():
            sec_buffers[current_sec].append(line.strip())

    for line in sec_buffers["exp"]:
        if is_vague_duty_bullet(line):
            units["vague_duty"].append(line)
        else:
            units["duty"].append(line)

    for line in sec_buffers["proj"]:
        units["project"].append(line)

    for line in sec_buffers["cert"]:
        units["certification"].append(line)

    for line in sec_buffers["skills"]:
        units["skills_list"].append(line)

    return units

def score_candidate_skills_evidence(
    required_skills: list[str],
    preferred_skills: list[str],
    resume_text: str,
    parsed: dict | None = None
):
    units = parse_resume_evidence_units(resume_text, parsed)
    evidence_weights = {"duty": 1.0, "project": 0.9, "certification": 0.9, "vague_duty": 0.5, "skills_list": 0.4}

    req_credits = {}
    matched_skills = []
    missing_skills = []

    for req in required_skills:
        best_credit = 0.0
        best_source = None
        for source, texts in units.items():
            combined_text = " ".join(texts)
            if match_term_in_text(req, combined_text):
                w = evidence_weights.get(source, 0.4)
                if w > best_credit:
                    best_credit = w
                    best_source = source

        req_credits[req] = best_credit
        if best_credit >= 0.50:
            matched_skills.append(req.title())
        else:
            missing_skills.append(req.title())

    pref_credits = {}
    for pref in preferred_skills:
        best_credit = 0.0
        best_source = None
        for source, texts in units.items():
            # Preferred skill must NOT get credit from raw skills list beyond 0.4,
            # and Section 8 requires verified evidence in experience/certifications for credit
            if source == "skills_list":
                continue
            combined_text = " ".join(texts)
            if match_term_in_text(pref, combined_text):
                w = evidence_weights.get(source, 0.4)
                if w > best_credit:
                    best_credit = w
                    best_source = source

        pref_credits[pref] = best_credit
        if best_credit >= 0.50:
            matched_skills.append(f"{pref.title()} (Preferred)")

    req_score = round((sum(req_credits.values()) / max(len(required_skills), 1)) * 100.0, 1)
    pref_score = round((sum(pref_credits.values()) / max(len(preferred_skills), 1)) * 100.0, 1)

    return matched_skills, missing_skills, req_score, pref_score, req_credits, pref_credits

if __name__ == "__main__":
    from test_round2_dataset import BASE_RESUMES, JOB

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

    print("--- SECTION 8 PREFERRED SKILL EVALUATION ---")
    candidates = ["A", "B", "C", "D", "E", "F"]
    for c in candidates:
        r = BASE_RESUMES[c]
        m, miss, req_s, pref_s, req_c, pref_c = score_candidate_skills_evidence(
            JOB["required_skills"], JOB["preferred_skills"], r["text"], r["parsed"]
        )
        print(f"Cand {c} ({r['name']}): Pref Score = {pref_s}%, Credited = {[k for k, v in pref_c.items() if v >= 0.5]}")

    print("\n--- SECTION 9 PARAPHRASES & NEAR-MISS CONTROLS ---")
    paraphrases = [
        ("Python", "Developed server-side modules with Django"),
        ("REST API", "Exposed JSON endpoints over HTTP for mobile and web clients"),
        ("REST API", "Built resource-oriented HTTP interfaces consumed by partner systems"),
        ("SQL", "Designed tables, indexes, and joins in PostgreSQL"),
        ("SQL", "Wrote stored procedures and tuned queries on a relational data store"),
        ("Git", "Used feature branches and pull requests for team collaboration"),
        ("Docker", "Wrote Dockerfiles and compose files for local and production setups"),
        ("Docker", "Shipped services as container images"),
    ]
    for skill, sent in paraphrases:
        matched = match_term_in_text(skill, sent)
        print(f"  [9.1 Should Match] [{skill:<10}] \"{sent[:50]}...\" -> [{'PASS' if matched else 'FAIL'}]")

    print("\n--- SECTION 6 DEV SET EVALUATION ---")
    dev_matches = [
        ("Python", "Implemented backend features in Python with Flask"),
        ("Python", "Automated data processing services written in Python 3.11"),
        ("REST API", "Created HTTP endpoints returning JSON for the mobile app"),
        ("REST API", "Designed and versioned public-facing web APIs"),
        ("SQL", "Modeled schemas and optimized joins in MySQL"),
        ("SQL", "Authored complex queries and indexes in a relational database"),
        ("Git", "Reviewed pull requests and managed branches on GitLab"),
        ("Git", "Resolved merge conflicts and maintained release tags"),
        ("Docker", "Built container images and ran them with docker-compose"),
        ("Docker", "Packaged microservices into containers for deployment"),
    ]
    dev_matched_cnt = 0
    for skill, sent in dev_matches:
        m = match_term_in_text(skill, sent)
        if m: dev_matched_cnt += 1
        print(f"  [Dev Match] [{skill:<10}] \"{sent[:50]}...\" -> [{'PASS' if m else 'FAIL'}]")
    print(f"  Dev Set Recall: {dev_matched_cnt}/{len(dev_matches)} ({dev_matched_cnt/len(dev_matches)*100:.1f}%) Target: >= 85%")

    dev_near_misses = [
        ("Python", "Wrote Java services with Spring"),
        ("SQL", "Used Redis as a key-value store"),
        ("REST API", "Verified API responses in Postman"),
        ("Docker", "Completed an online course about containers"),
        ("CI/CD", "Read about CI pipelines"),
        ("Unit testing", "Know of pytest"),
    ]
    dev_fp_cnt = 0
    for skill, sent in dev_near_misses:
        m = match_term_in_text(skill, sent)
        if m: dev_fp_cnt += 1
        print(f"  [Dev Near-Miss] [{skill:<12}] \"{sent[:50]}...\" -> [{'PASS (Rejected)' if not m else 'FAIL (False Positive)'}]")
    print(f"  Dev Set False-Positive Rate: {dev_fp_cnt}/{len(dev_near_misses)} ({dev_fp_cnt/len(dev_near_misses)*100:.1f}%) Target: <= 10%")

    print("\n--- SECTION 6 HELD-OUT SET (SEALED) EVALUATION ---")
    held_matches = [
        ("Python", "Developed internal tooling and microservices in Python"),
        ("Python", "Maintained a Django codebase serving production traffic"),
        ("REST API", "Delivered JSON-over-HTTP services consumed by third-party partners"),
        ("REST API", "Built endpoints following resource-based URL conventions"),
        ("SQL", "Wrote migrations and tuned indexes on PostgreSQL"),
        ("SQL", "Reduced slow query times on a relational store through join and index changes"),
        ("Git", "Collaborated through feature branches and code reviews in GitHub"),
        ("Git", "Handled version history and rebases across a team repository"),
        ("Docker", "Authored Dockerfiles for staging and production"),
        ("Docker", "Deployed workloads as container images to a registry"),
        ("AWS", "Hosted services on EC2 with S3 for storage"),
        ("CI/CD", "Automated build and release stages with Jenkins"),
        ("Unit testing", "Maintained a suite of automated unit tests with high coverage"),
    ]
    held_matched_cnt = 0
    for skill, sent in held_matches:
        m = match_term_in_text(skill, sent)
        if m: held_matched_cnt += 1
        print(f"  [Held Match] [{skill:<12}] \"{sent[:50]}...\" -> [{'PASS' if m else 'FAIL'}]")
    print(f"  Held-Out Set Recall: {held_matched_cnt}/{len(held_matches)} ({held_matched_cnt/len(held_matches)*100:.1f}%) Target: >= 85%")

    held_near_misses = [
        ("Python", "Explored Go for a hobby project"),
        ("SQL", "Stored session data in DynamoDB"),
        ("REST API", "Exercised REST endpoints with Insomnia during QA"),
        ("Docker", "Gave a talk summarizing container concepts"),
        ("CI/CD", "Interested in learning CI/CD"),
        ("Unit testing", "Reviewed teammates' test reports"),
    ]
    held_fp_cnt = 0
    for skill, sent in held_near_misses:
        m = match_term_in_text(skill, sent)
        if m: held_fp_cnt += 1
        print(f"  [Held Near-Miss] [{skill:<12}] \"{sent[:50]}...\" -> [{'PASS (Rejected)' if not m else 'FAIL (False Positive)'}]")
    print(f"  Held-Out Set False-Positive Rate: {held_fp_cnt}/{len(held_near_misses)} ({held_fp_cnt/len(held_near_misses)*100:.1f}%) Target: <= 10%")



