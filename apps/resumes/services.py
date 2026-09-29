import os
import re
import datetime
from django.utils import timezone
from apps.resumes.gemini_service import analyze_resume_with_gemini, generate_recruiter_insights_with_gemini

# Cross-Industry Competency & Tool Dictionary for Local Fallback
KNOWN_UNIVERSAL_SKILLS = [
    # Information Technology & Software
    "python", "javascript", "typescript", "java", "c++", "c#", "php", "sql", "html", "css",
    "django", "react", "next.js", "node.js", "docker", "redis", "postgresql", "mysql", "git", "aws",
    # Healthcare & Nursing
    "iv therapy", "iv cannulation", "bls", "acls", "patient care", "triage", "medication administration",
    "vital signs monitoring", "wound dressing", "phlebotomy", "charting", "icu care", "infection control",
    # Finance, Accounting & Bookkeeping
    "quickbooks", "sap", "xero", "tax preparation", "financial reporting", "auditing", "general ledger",
    "accounts payable", "accounts receivable", "bank reconciliation", "payroll", "excel", "vlookup",
    # Customer Service & BPO
    "customer service", "inbound calls", "outbound calling", "zendesk", "salesforce", "crm", "email support",
    "chat support", "ticket resolution", "bilingual", "active listening", "troubleshooting",
    # Engineering & Architecture
    "autocad", "solidworks", "revit", "staad pro", "project estimation", "site supervision",
    "qa/qc inspection", "bosh", "cosh", "structural analysis", "cost estimation",
    # Sales & Marketing
    "b2b sales", "lead generation", "cold calling", "seo", "social media marketing", "google analytics",
    "content creation", "canva", "pipeline management", "account management", "negotiation",
    # General & Operations
    "project management", "inventory management", "supply chain", "logistics", "data entry", "vs code"
]

MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12
}

def extract_text_from_file(file_path: str) -> str:
    """Extract clean text from PDF, DOCX, or TXT file."""
    if not os.path.exists(file_path):
        return ""

    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            import pymupdf
            doc = pymupdf.open(file_path)
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
        elif ext in ('.docx', '.doc'):
            import docx
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        text += cell.text + " "
                    text += "\n"
        else:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
    except Exception as e:
        print(f"Error extracting text from {file_path}: {e}")
        text = ""

    return text.strip()

def extract_skills_from_text(text: str) -> list[str]:
    """Fallback cross-industry competency finder."""
    text_lower = " " + text.lower() + " "
    found_skills = set()

    for skill in KNOWN_UNIVERSAL_SKILLS:
        pattern = r'(?:\b|\W)' + re.escape(skill) + r'(?:\b|\W)'
        if re.search(pattern, text_lower):
            if skill in ("javascript", "typescript", "postgresql", "mongodb", "quickbooks", "zendesk", "salesforce", "autocad"):
                standard_name = skill.title()
                if skill == "javascript": standard_name = "JavaScript"
                if skill == "typescript": standard_name = "TypeScript"
                if skill == "quickbooks": standard_name = "QuickBooks"
                if skill == "autocad": standard_name = "AutoCAD"
            elif skill in ("aws", "gcp", "html", "css", "sql", "php", "bls", "acls", "icu care", "bosh", "cosh", "seo", "crm"):
                standard_name = skill.upper()
            else:
                standard_name = skill.title()
            found_skills.add(standard_name)

    return sorted(list(found_skills))

def extract_licenses_from_text(text: str) -> list[str]:
    """Detect Philippine professional licenses and certifications."""
    licenses = []
    license_patterns = [
        r'\b(PRC\s+(?:Registered\s+)?(?:Nurse|RN|CPA|Accountant|Civil\s+Engineer|Electrical\s+Engineer|Mechanical\s+Engineer|Teacher|LPT|Pharmacist|Medical\s+Technologist|RMT))\b',
        r'\b(Certified\s+Public\s+Accountant|Licensed\s+Civil\s+Engineer|Licensed\s+Professional\s+Teacher|Registered\s+Nurse)\b',
        r'\b(Civil\s+Service\s+(?:Professional|Sub-Professional|Eligible))\b',
        r'\b(TESDA\s+NC\s+(?:I|II|III|IV)\b[^\n,.]*)',
        r'\b(BOSH|COSH|DOLE\s+Safety\s+Officer\s+(?:1|2|3|SO1|SO2|SO3))\b',
        r'\b(BLS|ACLS|Basic\s+Life\s+Support|Advanced\s+Cardiac\s+Life\s+Support)\b',
    ]

    for pat in license_patterns:
        matches = re.finditer(pat, text, re.IGNORECASE)
        for m in matches:
            val = m.group(0).strip()
            if val and len(val) < 60:
                licenses.append(val)

    return list(set(licenses))

def extract_education_from_text(text: str) -> list[str]:
    """Extract degrees across all disciplines (Healthcare, Business, Engineering, IT, Education)."""
    degrees = []
    edu_patterns = [
        r"\b(?:Bachelor of Science in Nursing|BSN|Bachelor of Science in Accountancy|BSA|Bachelor of Science in Civil Engineering|BSCE|Bachelor of Science in Information Technology|BSIT|Bachelor of Science in Computer Science|BSCS|Bachelor of Science in Business Administration|BSBA|Bachelor of Elementary Education|BEED|Bachelor of Secondary Education|BSED)\b",
        r"\b(?:Bachelor of Science|BS|B\.S\.|Bachelor of Arts|BA|Bachelor's)\b\s+(?:degree\s+)?(?:in\s+)?([A-Za-z\s]+?)(?=[,\.\n\(\)]|$)",
        r"\b(?:Master of Science|MS|M\.S\.|Master of Arts|MA|MBA|Master's)\b\s+(?:degree\s+)?(?:in\s+)?([A-Za-z\s]+?)(?=[,\.\n\(\)]|$)",
        r"\b(?:Doctor of Philosophy|PhD|Doctor of Medicine|MD)\b\s+(?:in\s+)?([A-Za-z\s]+?)(?=[,\.\n\(\)]|$)",
        r"\b(?:Associate Degree|Associate in|Diploma in)\b\s+([A-Za-z\s]+?)(?=[,\.\n\(\)]|$)",
    ]

    for pat in edu_patterns:
        matches = re.finditer(pat, text, re.IGNORECASE)
        for m in matches:
            full_match = m.group(0).strip()
            if full_match and 3 < len(full_match) < 60:
                degrees.append(full_match)

    return list(set(degrees))

def parse_month_year(date_str: str, default_to_current: bool = False) -> tuple[int, int] | None:
    """Parse dates like 'Feb 2026', '02/2026', '2024', 'Present' into (year, month)."""
    date_str = date_str.strip().lower()
    now = datetime.datetime.now()

    if date_str in ('present', 'current', 'now', 'ongoing'):
        return (now.year, now.month)

    m1 = re.match(r'([a-z]+)\.?\s+(\d{4})', date_str)
    if m1:
        month_name, year = m1.group(1), int(m1.group(2))
        month_num = MONTH_MAP.get(month_name, 1)
        return (year, month_num)

    m2 = re.match(r'(\d{1,2})[\/\-](\d{4})', date_str)
    if m2:
        month_num, year = int(m2.group(1)), int(m2.group(2))
        return (year, min(max(month_num, 1), 12))

    m3 = re.match(r'(\d{4})', date_str)
    if m3:
        year = int(m3.group(1))
        return (year, 12 if default_to_current else 1)

    return None

def extract_experience_years(text: str) -> float:
    """Section-aware multi-industry work tenure extractor."""
    exp_matches = re.findall(r'(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:professional|relevant|clinical|work)?\s*experience', text, re.IGNORECASE)
    if exp_matches:
        try:
            years = float(exp_matches[0])
            if 0 < years <= 40:
                return years
        except ValueError:
            pass

    work_header_pat = re.compile(
        r'(?:^|\n)\s*(?:PROFESSIONAL\s+EXPERIENCE|WORK\s+EXPERIENCE|EMPLOYMENT\s+HISTORY|WORK\s+HISTORY|CLINICAL\s+EXPERIENCE|EXPERIENCE|INTERNSHIP\s+EXPERIENCE|PRACTICUM)\s*[:\n]',
        re.IGNORECASE
    )
    other_header_pat = re.compile(
        r'\n\s*(?:EDUCATION|ACADEMIC\s+BACKGROUND|SEMINARS|WEBINARS|WORKSHOPS|ACHIEVEMENTS|CERTIFICATIONS|LICENSES|ORGANIZATIONS|PROJECTS|AFFILIATIONS|REFERENCES|CAREER\s+OBJECTIVES|TECHNICAL\s+SKILLS|KEY\s+SKILLS|CLINICAL\s+COMPETENCIES)\s*[:\n]',
        re.IGNORECASE
    )

    work_match = work_header_pat.search(text)
    if not work_match:
        return 0.0

    rest_of_text = text[work_match.end():]
    end_match = other_header_pat.search(rest_of_text)
    work_section_text = rest_of_text[:end_match.start()] if end_match else rest_of_text[:2500]

    date_range_pat = re.compile(
        r'(?:([A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[\/\-]\d{4}|\d{4}))'
        r'\s*[-–—to]+\s*'
        r'(?:([A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[\/\-]\d{4}|\d{4}|present|current|now))',
        re.IGNORECASE
    )

    matches = date_range_pat.findall(work_section_text)
    if not matches:
        return 0.5 if any(w in work_section_text.lower() for w in ("intern", "clinical", "trainee")) else 0.0

    month_intervals = []
    for start_str, end_str in matches:
        start_dt = parse_month_year(start_str, default_to_current=False)
        end_dt = parse_month_year(end_str, default_to_current=True)

        if start_dt and end_dt:
            start_idx = start_dt[0] * 12 + start_dt[1]
            end_idx = end_dt[0] * 12 + end_dt[1]

            if end_idx >= start_idx and start_dt[0] >= 1970:
                month_intervals.append((start_idx, end_idx))

    if not month_intervals:
        return 0.0

    month_intervals.sort(key=lambda x: x[0])
    merged = []
    for start, end in month_intervals:
        if not merged:
            merged.append([start, end])
        else:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1][1] = max(prev_end, end)
            else:
                merged.append([start, end])

    total_months = sum((end - start + 1) for start, end in merged)
    total_years = round(total_months / 12.0, 1)

    return min(max(total_years, 0.1), 40.0)

def evaluate_education_relevance(required_edu: str, candidate_degrees: list[str]) -> float:
    """
    Evaluates degree relevance across disciplines:
    - If job requires Accountancy / CPA and candidate has IT -> 0%
    - If job requires Nursing and candidate has IT -> 0%
    - If candidate has related degree -> 100%
    """
    if not required_edu or not candidate_degrees:
        return 0.0

    req_lower = required_edu.lower()
    cand_str = " ".join(candidate_degrees).lower()

    # Define domain clusters
    domains = {
        "accounting": ["accountan", "cpa", "audit", "finance", "banking", "commerce"],
        "nursing": ["nursing", "bsn", "nurse", "medical", "clinical"],
        "engineering": ["civil engineer", "electrical engineer", "mechanical engineer", "engineering", "bsce", "bsee", "bsme"],
        "it": ["computer science", "information technology", "software", "cs", "it", "bscs", "bsit", "bse"],
        "bpo_sales": ["business", "communication", "marketing", "management", "arts", "bsba", "commerce"],
    }

    req_domain = None
    for domain, keywords in domains.items():
        if any(k in req_lower for k in keywords):
            req_domain = domain
            break

    if not req_domain:
        # Generic role without strict academic discipline
        return 80.0

    cand_domain_match = any(k in cand_str for k in domains[req_domain])
    if cand_domain_match:
        return 100.0
    return 0.0  # Totally unrelated discipline

def process_resume_and_calculate_match(application):
    """
    Universal Dynamic AI Pipeline with Domain Gating:
    1. Extracts clean text from resume file (PDF/DOCX/TXT)
    2. Dynamic zero-shot extraction using Gemini AI across any industry
    3. Evaluates with Philippine Market Deterministic Formula & Qualification Gating:
       - 0% Required Competencies Matched -> 0.0% Overall Score (Unqualified / Ineligible)
       - 40% Experience Fit (Tenure & relevant industry duties)
       - 35% Competencies, Tools & Practical Skills Match
       - 15% Semantic / Responsibility Alignment
       - 10% Education & Licensure Relevance (0% for unrelated degrees)
    4. Generates evidence-grounded recruiter insights
    """
    from apps.resumes.models import Resume, MatchResult

    try:
        resume = application.resume
    except Resume.DoesNotExist:
        return None

    resume.processing_status = 'PROCESSING'
    resume.save(update_fields=['processing_status'])

    file_path = resume.file.path
    extracted_text = extract_text_from_file(file_path)
    resume.extracted_text = extracted_text

    # Dynamic Gemini AI Extraction
    gemini_data = analyze_resume_with_gemini(extracted_text)

    if gemini_data:
        skills = gemini_data.get("skills", [])
        licenses = gemini_data.get("licenses_and_certifications", [])
        education = gemini_data.get("education", [])
        exp_years = float(gemini_data.get("total_experience_years", 0.0))
        if gemini_data.get("is_fresh_graduate", False) and exp_years == 0:
            exp_years = 0.0
    else:
        skills = extract_skills_from_text(extracted_text)
        licenses = extract_licenses_from_text(extracted_text)
        education = extract_education_from_text(extracted_text)
        exp_years = extract_experience_years(extracted_text)

    resume.extracted_skills = skills
    resume.extracted_education = education + ([f"License: {l}" for l in licenses] if licenses else [])
    resume.extracted_experience_years = exp_years
    resume.processing_status = 'COMPLETED'
    resume.processed_at = timezone.now()
    resume.save()

    # Universal Matching Logic
    job = application.job
    required_skills = [s.strip().lower() for s in (job.required_skills or []) if s.strip()]
    preferred_skills = [s.strip().lower() for s in (job.preferred_skills or []) if s.strip()]
    all_candidate_competencies = [s.lower() for s in (skills + licenses)]

    matched_skills = []
    missing_skills = []

    # 1. Required Competencies & Licenses Check
    req_matched_count = 0
    for req in required_skills:
        if any(req in c or c in req for c in all_candidate_competencies):
            req_matched_count += 1
            matched_skills.append(req.title())
        else:
            missing_skills.append(req.title())

    # 2. Preferred Competencies Check
    pref_matched_count = 0
    for pref in preferred_skills:
        if any(pref in c or c in pref for c in all_candidate_competencies):
            pref_matched_count += 1
            matched_skills.append(f"{pref.title()} (Preferred)")

    total_req = max(len(required_skills), 1)
    req_ratio = req_matched_count / total_req

    # HARD QUALIFICATION GATE:
    # If 0 required competencies/licenses match, the candidate is fundamentally unqualified (e.g. IT student applying for Senior CPA)
    if total_req > 0 and req_matched_count == 0:
        skill_score = 0.0
        exp_score = 0.0
        edu_score = 0.0
        semantic_score = 0.0
        overall_score = 0.0

        explanation = (
            f"🚫 INELIGIBLE / UNQUALIFIED CANDIDATE\n"
            f"• 0 out of {total_req} required competencies or licenses were found in the resume.\n"
            f"• Required for this position: {', '.join([s.title() for s in required_skills])}.\n"
            f"• Candidate qualifications lie in an unrelated domain."
        )

        match_result, _ = MatchResult.objects.update_or_create(
            application=application,
            defaults={
                'match_score': 0.0,
                'skill_match_score': 0.0,
                'experience_match_score': 0.0,
                'education_match_score': 0.0,
                'semantic_match_score': 0.0,
                'matched_skills': [],
                'missing_skills': missing_skills,
                'explanation': explanation,
            }
        )
        if application.status == 'SUBMITTED':
            application.status = 'UNDER_REVIEW'
            application.save(update_fields=['status'])
        return match_result

    # Standard Competency Scoring
    base_skill_score = req_ratio * 100.0
    if preferred_skills and pref_matched_count > 0:
        base_skill_score = min(base_skill_score + (pref_matched_count / len(preferred_skills)) * 10.0, 100.0)
    skill_score = min(max(base_skill_score, 0.0), 100.0)

    # 3. Experience Score (40% Weight in Philippines Context)
    req_exp_match = re.search(r'(\d+(?:\.\d+)?)', str(job.minimum_experience))
    required_exp_years = float(req_exp_match.group(1)) if req_exp_match else 1.0

    if exp_years >= required_exp_years:
        exp_score = 100.0
    elif exp_years == 0.0:
        exp_score = 25.0 if required_exp_years <= 1.0 else 10.0
    else:
        exp_score = min(max((exp_years / required_exp_years) * 100.0, 15.0), 95.0)

    # 4. Education & Licensure Relevance (10% Weight)
    edu_score = evaluate_education_relevance(job.education_requirement, education + licenses)

    # 5. Semantic / Responsibility Alignment (15% Weight)
    job_desc_words = set(re.findall(r'\w{4,}', job.description.lower()))
    resume_words = set(re.findall(r'\w{4,}', extracted_text.lower()))
    if job_desc_words and resume_words:
        overlap = len(job_desc_words.intersection(resume_words))
        semantic_score = min(max((overlap / min(len(job_desc_words), 25)) * 100.0, 20.0), 98.0)
    else:
        semantic_score = 40.0

    # Composite Score with Gating Penalty if required skills match < 50%
    raw_composite = (
        (exp_score * 0.40) +
        (skill_score * 0.35) +
        (semantic_score * 0.15) +
        (edu_score * 0.10)
    )

    # Apply gating scaling factor based on required skills ratio
    if req_ratio < 0.5:
        overall_score = raw_composite * req_ratio
    else:
        overall_score = raw_composite

    overall_score = round(min(max(overall_score, 0.0), 99.0), 1)

    # Generate Recruiter Insights with Gemini
    job_data = {
        "title": job.title,
        "department": job.department,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "minimum_experience": job.minimum_experience,
        "education_requirement": job.education_requirement,
    }
    candidate_data = {
        "candidate_code": application.candidate_code,
        "skills": skills,
        "licenses": licenses,
        "experience_years": exp_years,
        "education": education,
    }
    scores_dict = {
        "overall_score": overall_score,
        "skill_score": skill_score,
        "exp_score": exp_score,
        "edu_score": edu_score,
        "semantic_score": semantic_score,
    }

    gemini_insight = generate_recruiter_insights_with_gemini(job_data, candidate_data, scores_dict)

    if gemini_insight:
        explanation = gemini_insight
    else:
        exp_display = "Fresh Graduate / Entry Level (0 years)" if exp_years == 0 else f"{exp_years:.1f} year(s)"
        explanation_parts = [
            f"• Experience Fit (40%): {exp_display} vs Required {job.minimum_experience} ({exp_score:.0f}% score).",
            f"• Competencies & Licenses (35%): Matched {len(matched_skills)} of {total_req} requirements ({skill_score:.0f}% score).",
            f"• Semantic & Duty Fit (15%): {semantic_score:.0f}% contextual alignment.",
            f"• Education & Discipline (10%): {edu_score:.0f}% discipline alignment.",
        ]
        if missing_skills:
            explanation_parts.append(f"• Missing required requirements: {', '.join(missing_skills)}.")
        explanation = "\n".join(explanation_parts)

    match_result, _ = MatchResult.objects.update_or_create(
        application=application,
        defaults={
            'match_score': overall_score,
            'skill_match_score': round(skill_score, 1),
            'experience_match_score': round(exp_score, 1),
            'education_match_score': round(edu_score, 1),
            'semantic_match_score': round(semantic_score, 1),
            'matched_skills': matched_skills,
            'missing_skills': missing_skills,
            'explanation': explanation,
        }
    )

    if application.status == 'SUBMITTED':
        application.status = 'UNDER_REVIEW'
        application.save(update_fields=['status'])

    return match_result
