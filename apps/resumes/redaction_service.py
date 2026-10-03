import re
import json

# Protected attribute & PII regex patterns for input-stage redaction
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE_PATTERN = re.compile(r'(?:\+?63|0)[\s.-]?9\d{2}[\s.-]?\d{3}[\s.-]?\d{4}|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b')
URL_PATTERN = re.compile(r'https?://[^\s]+|www\.[^\s]+|(?:linkedin\.com|github\.com)/[^\s]+', re.IGNORECASE)

# Demographics & Protected Attributes
GENDER_PRONOUN_PATTERN = re.compile(
    r'\b(?:gender|sex)\s*[:\-]\s*(?:male|female|non-binary|other)\b|'
    r'\b(?:pronouns?)\s*[:\-]\s*(?:he/him|she/her|they/them)\b|'
    r'\b(?:he/him|she/her|they/them|he/his|she/hers)\b|'
    r'\b(?:male|female)\b',
    re.IGNORECASE
)
MARITAL_NATIONALITY_PATTERN = re.compile(
    r'\b(?:civil\s+status|marital\s+status)\s*[:\-]\s*(?:single|married|widowed|divorced)\b|'
    r'\b(?:nationality|citizenship)\s*[:\-]\s*[A-Za-z\s]{3,20}\b|'
    r'\b(?:religion|faith)\s*[:\-]\s*[A-Za-z\s]{3,20}\b',
    re.IGNORECASE
)
AGE_DOB_PATTERN = re.compile(
    r'\b(?:age|date\s+of\s+birth|dob|birthdate)\s*[:\-]\s*(?:\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}\s*(?:years?\s*old)?)\b|'
    r'\b\d{1,2}\s+years\s+old\b',
    re.IGNORECASE
)
ADDRESS_PATTERN = re.compile(
    r'\b(?:address|location|residence)\s*[:\-]\s*[^\n\r]+',
    re.IGNORECASE
)

# School & Prestige patterns to mask
SCHOOL_KEYWORDS = [
    r'University\s+of\s+[A-Za-z\s]+',
    r'[A-Za-z\s]+(?:\s+State)?\s+University',
    r'[A-Za-z\s]+\s+College(?:\s+of\s+[A-Za-z\s]+)?',
    r'[A-Za-z\s]+\s+Institute\s+of\s+Technology',
    r'Ateneo\s+de\s+[A-Za-z\s]+',
    r'De\s+La\s+Salle\s+[A-Za-z\s]*',
    r'Polytechnic\s+University\s+of\s+the\s+Philippines',
    r'University\s+of\s+Santo\s+Tomas',
    r'University\s+of\s+the\s+East',
    r'Far\s+Eastern\s+University',
    r'Map[uú]a\s+University',
    r'San\s+Beda\s+University',
    r'AMA\s+University',
    r'STI\s+College',
]
SCHOOL_REGEX = re.compile(r'\b(?:' + '|'.join(SCHOOL_KEYWORDS) + r')\b', re.IGNORECASE)

# Graduation years pattern (e.g. "Graduated 2020", "2016 - 2020", "Class of 2018")
GRAD_YEAR_PATTERN = re.compile(
    r'(?:graduated|graduating|class\s+of|batch)\s*(?:in\s+)?(?:\d{4})|'
    r'\b(?:19|20)\d{2}\s*[-–—to]+\s*(?:19|20)\d{2}\b',
    re.IGNORECASE
)

# Degree level standardizer
DEGREE_LEVELS = [
    ("Doctorate", ["phd", "doctor of philosophy", "doctorate", "doctor of medicine", "md"]),
    ("Master", ["master of science", "master of arts", "mba", "ms", "ma", "master's", "masters"]),
    ("Bachelor", ["bachelor of science", "bachelor of arts", "bs", "ba", "bachelor's", "bachelors", "bsn", "bsa", "bsce", "bsit", "bscs", "bsba", "beed", "bsed"]),
    ("Associate / Diploma", ["associate", "diploma"]),
]

def sanitize_education_credentials(raw_education: list[str]) -> list[dict]:
    """
    Strips institution names and graduation years, retaining only:
    - education_level (Bachelor, Master, Doctorate, Associate)
    - education_field (e.g. Computer Science, Nursing, Accountancy)
    """
    structured_edu = []
    for edu_str in raw_education:
        if not edu_str or not isinstance(edu_str, str):
            continue
        # Remove school mentions if present
        clean_edu = SCHOOL_REGEX.sub('', edu_str)
        clean_edu = GRAD_YEAR_PATTERN.sub('', clean_edu)
        clean_edu = re.sub(r'[\(\)\[\],]', ' ', clean_edu)
        clean_edu = re.sub(r'\s+', ' ', clean_edu).strip()

        # Detect level
        level = "Bachelor"
        lower_edu = edu_str.lower()
        for lvl_name, terms in DEGREE_LEVELS:
            if any(re.search(r'\b' + re.escape(t) + r'\b', lower_edu) for t in terms):
                level = lvl_name
                break

        # Extract field
        field_match = re.search(r'(?:in|major\s+in)\s+([A-Za-z\s]+)', edu_str, re.IGNORECASE)
        if field_match:
            field = field_match.group(1).strip().title()
        else:
            field = clean_edu.title() if clean_edu else "General Studies"

        structured_edu.append({
            "level": level,
            "field": field,
            "credential_summary": f"{level} in {field}" if field and not field.startswith(level) else (clean_edu or level)
        })

    return structured_edu

def redact_raw_resume_text(text: str, applicant_info: dict | None = None) -> str:
    """
    Performs comprehensive text-stage redaction before scoring or LLM evaluation:
    - Redacts applicant direct name, email, phone, web links
    - Masks demographic variables (age, dob, gender, pronouns, marital status, nationality)
    - Masks university / school brand names
    - Masks graduation batch years
    """
    if not text:
        return ""

    sanitized = text
    # Scrub email, phone, links, and addresses FIRST before name replacement
    sanitized = EMAIL_PATTERN.sub('[EMAIL REDACTED]', sanitized)
    sanitized = PHONE_PATTERN.sub('[PHONE REDACTED]', sanitized)
    sanitized = URL_PATTERN.sub('[LINK REDACTED]', sanitized)
    sanitized = ADDRESS_PATTERN.sub('[ADDRESS REDACTED]', sanitized)

    # Redact explicit applicant personal info if provided
    if applicant_info:
        email = applicant_info.get("email")
        if email:
            sanitized = re.sub(re.escape(email), '[EMAIL REDACTED]', sanitized, flags=re.IGNORECASE)

        phone = applicant_info.get("phone")
        if phone and len(phone) >= 7:
            sanitized = re.sub(re.escape(phone), '[PHONE REDACTED]', sanitized)

        first_name = (applicant_info.get("first_name") or "").strip()
        last_name = (applicant_info.get("last_name") or "").strip()
        full_name = (applicant_info.get("applicant_name") or f"{first_name} {last_name}").strip()

        for name_part in [full_name, first_name, last_name]:
            if name_part and len(name_part) >= 2:
                pattern = re.compile(r'\b' + re.escape(name_part) + r'\b', re.IGNORECASE)
                sanitized = pattern.sub('[CANDIDATE NAME]', sanitized)

    # Demographic & protected attribute scrubbing
    sanitized = GENDER_PRONOUN_PATTERN.sub('[GENDER REDACTED]', sanitized)
    sanitized = MARITAL_NATIONALITY_PATTERN.sub('[DEMOGRAPHIC REDACTED]', sanitized)
    sanitized = AGE_DOB_PATTERN.sub('[AGE/DOB REDACTED]', sanitized)
    sanitized = SCHOOL_REGEX.sub('[ACADEMIC INSTITUTION]', sanitized)
    sanitized = GRAD_YEAR_PATTERN.sub('[YEAR REDACTED]', sanitized)

    # Clean double spaces/newlines
    sanitized = re.sub(r'[ \t]+', ' ', sanitized)
    return sanitized.strip()

def build_redacted_candidate_profile(
    extracted_text: str,
    parsed_ai_data: dict | None,
    candidate_code: str,
    applicant_info: dict | None = None
) -> tuple[str, dict]:
    """
    Produces:
    1. A fully sanitized resume text representation.
    2. A structured, bias-reduced candidate JSON payload conforming to Section 2:
       {
         "candidate_code": "TM-0001",
         "skills": [...],
         "skills_with_evidence": [...],
         "experience": [{"title": "...", "duration_years": 2.0, "duties": [...]}, ...],
         "total_experience_years": 2.0,
         "education": [{"level": "...", "field": "..."}, ...],
         "certifications": [...],
         "projects": [...]
       }
    """
    redacted_text = redact_raw_resume_text(extracted_text, applicant_info)

    # Experience redaction: mask company brand prestige
    redacted_experiences = []
    raw_experiences = (parsed_ai_data.get("experiences") if parsed_ai_data else []) or []

    for idx, exp in enumerate(raw_experiences):
        title = exp.get("job_title", "Professional Role")
        duration_months = exp.get("duration_months", 0)
        duration_years = round(duration_months / 12.0, 1) if duration_months else 1.0
        duties = exp.get("responsibilities", "")
        # Mask specific company name with anonymized token
        anonymized_org = f"Organization #{idx + 1}"

        # Clean duties of PII
        clean_duties = redact_raw_resume_text(duties if isinstance(duties, str) else json.dumps(duties))

        redacted_experiences.append({
            "title": title,
            "duration_years": duration_years,
            "organization": anonymized_org,
            "duties": clean_duties
        })

    # Education redaction: strip school names, keep degree level and field
    raw_education = (parsed_ai_data.get("education") if parsed_ai_data else []) or []
    sanitized_education = sanitize_education_credentials(raw_education)

    skills = (parsed_ai_data.get("skills") if parsed_ai_data else []) or []
    skills_with_evidence = (parsed_ai_data.get("skills_with_evidence") if parsed_ai_data else []) or []
    licenses = (parsed_ai_data.get("licenses_and_certifications") if parsed_ai_data else []) or []
    total_exp_years = float(parsed_ai_data.get("total_experience_years", 0.0)) if parsed_ai_data else 0.0

    # Extract projects from parsed data or text
    projects = parsed_ai_data.get("projects", []) if parsed_ai_data else []
    if not projects:
        # Heuristic detection for project entries in resume text
        proj_matches = re.findall(r'(?:Project|Key Project|Achievement)[\s:\-]+([^\n\.]{10,80})', redacted_text, re.IGNORECASE)
        projects = [p.strip() for p in proj_matches[:4] if p.strip()]

    structured_profile = {
        "candidate_code": candidate_code,
        "skills": skills,
        "skills_with_evidence": skills_with_evidence,
        "experience": redacted_experiences,
        "total_experience_years": total_exp_years,
        "education": sanitized_education,
        "certifications": licenses,
        "projects": projects,
        "fields_used": ["skills", "experience", "education_level", "certifications", "projects"],
        "fields_excluded": ["name", "gender", "age", "address", "school_prestige", "employer_prestige", "graduation_year"]
    }

    return redacted_text, structured_profile
