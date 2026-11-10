import re
import json

# Protected attribute & PII regex patterns for input-stage redaction
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE_PATTERN = re.compile(
    r'(?:\+?63|0)[\s.-]?9\d{2}[\s.-]?\d{3}[\s.-]?\d{4}|'
    r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b'
)
URL_PATTERN = re.compile(
    r'https?://[^\s]+|www\.[^\s]+|(?:linkedin\.com|github\.com|facebook\.com|instagram\.com|twitter\.com|x\.com)/[^\s]+',
    re.IGNORECASE
)

# Labeled Name & Honorific patterns
LABELED_NAME_PATTERN = re.compile(
    r'\b(?:full[ \t]+name|applicant[ \t]+name|candidate[ \t]+name|name)[ \t]*[:\-][ \t]*[^\n\r|•]+',
    re.IGNORECASE
)
HONORIFIC_NAME_PATTERN = re.compile(
    r'\b(?:Mr|Ms|Mrs|Miss|Mx)\.?\s+[A-Z][a-z]+(?:[ \t]+(?:[A-Z]\.?|[A-Z][a-z]+)){0,3}\b'
)

# Demographics & Protected Attributes (Gender, Pronouns, Age, DOB, Marital Status, Religion, Nationality, Physical traits)
GENDER_PRONOUN_PATTERN = re.compile(
    r'\b(?:gender|sex)[ \t]*[:\-]?[ \t]*(?:male|female|non-binary|transgender|other|m|f)\b|'
    r'\b(?:pronouns?)[ \t]*[:\-]?[ \t]*(?:he/him|she/her|they/them|he/they|she/they|he/his|she/hers)\b|'
    r'\b(?:he/him|she/her|they/them|he/his|she/hers|him/his|her/hers)\b|'
    r'\b(?:male|female|fraternity|sorority)\b',
    re.IGNORECASE
)
MARITAL_NATIONALITY_PATTERN = re.compile(
    r'\b(?:civil[ \t]+status|marital[ \t]+status|status)[ \t]*[:\-][ \t]*(?:single|married|widowed|separated|divorced|annulled)\b|'
    r'\b(?:nationality|citizenship|ethnicity|race)[ \t]*[:\-][ \t]*[A-Za-z ]{3,25}\b|'
    r'\b(?:religion|faith)[ \t]*[:\-][ \t]*[A-Za-z .\-]{3,30}\b|'
    r'\b(?:height|weight|blood[ \t]+type)[ \t]*[:\-][ \t]*[^\n\r|•]+|'
    r'\b(?:father\'?s?[ \t]+name|mother\'?s?[ \t]+name|spouse\'?s?[ \t]+name|parents\'?[ \t]+name|emergency[ \t]+contact)[ \t]*[:\-][ \t]*[^\n\r]+|'
    r'\b(?:Filipino[ \t]+Citizen|Filipino|Roman[ \t]+Catholic|Iglesia[ \t]+ni[ \t]+Cristo)\b|'
    r'\b(?:Single|Married|Widowed|Divorced)(?![ \t\-]+(?:sign|point|page|source|thread|session|instance|window|channel|tier|day|shift|hand|click|user|tenant|minded|handedly|precision|phase|mode|line|file|table|query|view))\b',
    re.IGNORECASE
)
AGE_DOB_PATTERN = re.compile(
    r'\b(?:age|date[ \t]+of[ \t]+birth|dob|d\.o\.b\.|birthdate|born[ \t]+on|born[ \t]+in)[ \t]*[:\-]?[ \t]*'
    r'(?:\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|[A-Za-z]+[ \t]+\d{1,2},?[ \t]+\d{4}|\d{1,2}[ \t]+[A-Za-z]+[ \t]+\d{4}|\d{4}|\d{1,2}[ \t]*(?:years?[ \t]*old|yrs?\.?[ \t]*old|y/o)?)\b|'
    r'\b\d{1,2}[ \t]+(?:years?[ \t]+old|yrs?\.?[ \t]+old|y/o)\b',
    re.IGNORECASE
)

# Comprehensive Geographic & Location Redaction (Labeled addresses, Street addresses, Barangays, PH cities/provinces, US 'City, ST', Countries)
LABELED_ADDRESS_PATTERN = re.compile(
    r'\b(?:present[ \t]+address|permanent[ \t]+address|home[ \t]+address|city[ \t]+address|provincial[ \t]+address|'
    r'current[ \t]+location|place[ \t]+of[ \t]+birth|birthplace|address|location|residence)[ \t]*[:\-][ \t]*[^\n\r|•]+',
    re.IGNORECASE
)
STREET_BARANGAY_PATTERN = re.compile(
    r'\b(?:Blk\.?[ \t]*\d+[ \t,]+)?(?:Lot\.?[ \t]*\d+[ \t,]+)?(?:Phase[ \t]*\d+[ \t,]+)?'
    r'(?:Brgy\.?|Barangay|Sitio|Purok)[ \t]+[A-Za-z0-9 .,\-]+?(?=\n|\r|[|•]|$)|'
    r'\b\d{1,5}[ \t]+[A-Z][A-Za-z0-9 .\-]{2,35}[ \t]+(?:Street|St\.|Avenue|Ave\.|Boulevard|Blvd\.|Road|Rd\.|Drive|Dr\.|Lane|Ln\.|Highway|Hwy\.|Subdivision|Subd\.|Village)\b(?:[ \t]*,[ \t]*[A-Za-z .]+)?',
    re.IGNORECASE
)
# Case-sensitive US State abbreviation after comma (e.g. "Portland, OR", "Seattle, WA 98101", "New York, NY")
CITY_STATE_ZIP_PATTERN = re.compile(
    r'\b[A-Z][A-Za-z .\'-]{1,25},[ \t]*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b(?:[ \t]+\d{5}(?:-\d{4})?)?'
)
PH_AND_GLOBAL_LOCATIONS_PATTERN = re.compile(
    r'\b(?:'
    r'Metro[ \t]+Manila|National[ \t]+Capital[ \t]+Region|NCR|'
    r'Quezon[ \t]+City|Makati(?:[ \t]+City)?|Taguig(?:[ \t]+City)?|Bonifacio[ \t]+Global[ \t]+City|BGC|'
    r'Pasig(?:[ \t]+City)?|Mandaluyong(?:[ \t]+City)?|Manila(?:[ \t]+City)?|Pasay(?:[ \t]+City)?|'
    r'Para[ñn]aque(?:[ \t]+City)?|Las[ \t]+Pi[ñn]as(?:[ \t]+City)?|Muntinlupa(?:[ \t]+City)?|'
    r'Marikina(?:[ \t]+City)?|Caloocan(?:[ \t]+City)?|Valenzuela(?:[ \t]+City)?|Malabon(?:[ \t]+City)?|'
    r'Navotas(?:[ \t]+City)?|San[ \t]+Juan(?:[ \t]+City)?|Pateros|'
    r'Cebu(?:[ \t]+City)?|Mandaue(?:[ \t]+City)?|Lapu-Lapu(?:[ \t]+City)?|Davao(?:[ \t]+City)?|'
    r'Iloilo(?:[ \t]+City)?|Bacolod(?:[ \t]+City)?|Baguio(?:[ \t]+City)?|Cagayan[ \t]+de[ \t]+Oro|'
    r'Zamboanga(?:[ \t]+City)?|General[ \t]+Santos|Antipolo(?:[ \t]+City)?|Dasmari[ñn]as(?:[ \t]+City)?|'
    r'Imus(?:[ \t]+City)?|Bacoor(?:[ \t]+City)?|Calamba(?:[ \t]+City)?|Santa[ \t]+Rosa(?:[ \t]+City)?|'
    r'Bi[ñn]an(?:[ \t]+City)?|San[ \t]+Pedro(?:[ \t]+City)?|Cabuyao(?:[ \t]+City)?|'
    r'Meycauayan(?:[ \t]+City)?|Malolos(?:[ \t]+City)?|San[ \t]+Jose[ \t]+del[ \t]+Monte|'
    r'Angeles(?:[ \t]+City)?|Olongapo(?:[ \t]+City)?|Batangas(?:[ \t]+City)?|Lipa(?:[ \t]+City)?|'
    r'Lucena(?:[ \t]+City)?|Naga(?:[ \t]+City)?|Legazpi(?:[ \t]+City)?|Tacloban(?:[ \t]+City)?|'
    r'Cavite|Laguna|Rizal|Bulacan|Pampanga|Tarlac|Nueva[ \t]+Ecija|Zambales|Bataan|Pangasinan|'
    r'La[ \t]+Union|Ilocos[ \t]+Norte|Ilocos[ \t]+Sur|Benguet|Albay|Camarines[ \t]+Sur|'
    r'Palawan|Negros[ \t]+Occidental|Negros[ \t]+Oriental|Bohol|Leyte|Bukidnon|'
    r'Philippines'
    r')(?:[ \t]*,[ \t]*(?:Metro[ \t]+Manila|Philippines|\d{4}))?\b',
    re.IGNORECASE
)

IDENTIFIER_PATTERN = re.compile(
    r'\b(?:license|licence|passport|national[ \t]+id|sss|tin|philhealth|pag-ibig|umid|driver\'?s?[ \t]+license)'
    r'[ \t]*(?:number|no\.?|#|id)?[ \t]*[:\-][ \t]*[A-Z0-9][A-Z0-9\- ]*',
    re.IGNORECASE,
)
REFERENCE_SECTION = re.compile(
    r'^\s*(?:character[ \t]+|professional[ \t]+)?references(?:\s*available\s*upon\s*request)?\s*:?\s*$',
    re.IGNORECASE | re.MULTILINE
)

# School & Prestige patterns to mask (strictly single-line horizontal whitespace so headers/degrees are never consumed)
SCHOOL_KEYWORDS = [
    r'University[ \t]+of[ \t]+[A-Za-z]+(?:[ \t]+[A-Za-z]+){0,4}',
    r'(?:[A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+){0,3}[ \t]+)(?:State[ \t]+)?University',
    r'(?:[A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+){0,3}[ \t]+)College(?:[ \t]+of[ \t]+[A-Za-z ]+)?',
    r'(?:[A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+){0,3}[ \t]+)Institute[ \t]+of[ \t]+Technology',
    r'Ateneo[ \t]+de[ \t]+[A-Za-z ]+',
    r'De[ \t]+La[ \t]+Salle[ \t]+[A-Za-z ]*',
    r'Polytechnic[ \t]+University[ \t]+of[ \t]+the[ \t]+Philippines',
    r'University[ \t]+of[ \t]+Santo[ \t]+Tomas',
    r'University[ \t]+of[ \t]+the[ \t]+East',
    r'Far[ \t]+Eastern[ \t]+University',
    r'Map[uú]a[ \t]+University',
    r'San[ \t]+Beda[ \t]+University',
    r'AMA[ \t]+(?:Computer[ \t]+)?(?:University|College)',
    r'STI[ \t]+College',
]
SCHOOL_REGEX = re.compile(r'\b(?:' + '|'.join(SCHOOL_KEYWORDS) + r')\b', re.IGNORECASE)

# Words that indicate a degree or section header rather than a school name
_DEGREE_OR_HEADER_WORDS = re.compile(
    r'\b(?:education|bachelor|master|doctor|doctorate|associate|diploma|degree|communication|science|arts|engineering|nursing|accountancy|member|active|club)\b',
    re.IGNORECASE
)

# Words that never belong in a candidate's personal name header line
_NON_NAME_HEADER_WORDS = {
    "resume", "curriculum", "vitae", "cv", "summary", "profile", "objective",
    "experience", "work", "employment", "history", "education", "academic",
    "skills", "competencies", "technical", "certifications", "licenses",
    "projects", "achievements", "awards", "languages", "references", "contact",
    "details", "personal", "information", "qualifications", "training",
    "seminars", "affiliations", "memberships", "representative", "agent",
    "specialist", "engineer", "developer", "manager", "director", "analyst",
    "associate", "assistant", "officer", "coordinator", "consultant",
    "administrator", "nurse", "accountant", "teacher", "technician", "designer",
    "architect", "support", "service", "customer", "software", "data", "senior",
    "junior", "lead", "intern", "professional", "certified", "registered",
    "licensed", "bachelor", "master", "doctor", "science", "arts", "high",
    "school", "university", "college", "institute", "candidate", "redacted",
}

def _mask_school_match(match: re.Match) -> str:
    matched_str = match.group(0)
    if _DEGREE_OR_HEADER_WORDS.search(matched_str) and not re.search(
        r'\b(?:University\s+of|De\s+La\s+Salle|Ateneo|Santo\s+Tomas|Far\s+Eastern|Map[uú]a|San\s+Beda|AMA|STI|State\s+University)\b',
        matched_str,
        re.IGNORECASE
    ):
        return re.sub(r'\bUniversity[ \t]+of[ \t]+[A-Za-z ]+\b', '[ACADEMIC INSTITUTION]', matched_str, flags=re.IGNORECASE)
    return '[ACADEMIC INSTITUTION]'


def _detect_and_redact_header_name(text: str) -> tuple[str, list[str]]:
    """
    Inspects the first 3 non-empty lines of a resume to automatically detect and redact
    an unlabeled candidate name header (including middle names, initials, nicknames, or suffixes),
    while preserving any trailing professional credential (e.g. ', CPA', ', RN') and never touching
    section headers or job titles.
    """
    lines = text.splitlines()
    checked = 0
    detected_tokens: list[str] = []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        checked += 1
        if checked > 3:
            break

        # Skip lines that already have redaction markers, digits, emails, pipes, or colons
        if any(ch in stripped for ch in ("@", ":", "|", "•", "[")) or re.search(r'\d', stripped):
            continue

        # Preserve trailing professional credential suffix if attached (e.g. "Maria Santos, CPA")
        cred_suffix = ""
        base_line = stripped
        cred_match = re.search(r',[ \t]*((?:CPA|RN|LPT|MD|PhD|MBA|PMP|CCSP|CCNA|ECE|CE|ME|EE)(?:[ \t,]+(?:CPA|RN|LPT|MD|PhD|MBA))*)$', stripped, re.IGNORECASE)
        if cred_match:
            cred_suffix = f", {cred_match.group(1)}"
            base_line = stripped[:cred_match.start()].strip()

        tokens = [t.strip(".,'\"()-\t ") for t in base_line.split() if t.strip(".,'\"()-\t ")]
        if not (2 <= len(tokens) <= 5):
            continue

        # Check all tokens look like alphabetic name words and none are job/section words
        is_name_line = True
        for tok in tokens:
            tok_clean = tok.lower().rstrip(".")
            if tok_clean in _NON_NAME_HEADER_WORDS:
                is_name_line = False
                break
            if not re.match(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]*\.?$", tok):
                is_name_line = False
                break
            # Must be Title Case or ALL CAPS
            if not (tok.isupper() or tok[0].isupper()):
                is_name_line = False
                break

        if is_name_line:
            lines[idx] = f"[CANDIDATE NAME]{cred_suffix}"
            for tok in tokens:
                tok_alpha = tok.strip(".")
                if len(tok_alpha) >= 3 and tok_alpha.lower() not in {"jr", "sr", "iii", "iv"}:
                    detected_tokens.append(tok_alpha)
            break

    redacted = "\n".join(lines)
    for tok in detected_tokens:
        redacted = re.sub(r'\b' + re.escape(tok) + r'\b', '[CANDIDATE NAME]', redacted, flags=re.IGNORECASE)
    return redacted, detected_tokens


# Graduation years pattern (e.g. "Graduated 2020", "Class of 2018")
GRAD_YEAR_PATTERN = re.compile(
    r'(?:graduated|graduating|class[ \t]+of|batch)[ \t]*(?:in[ \t]+)?(?:\d{4})',
    re.IGNORECASE
)

# Degree level standardizer
DEGREE_LEVELS = [
    ("Doctorate", ["phd", "doctor of philosophy", "doctorate", "doctor of medicine", "md"]),
    ("Master", ["master of science", "master of arts", "mba", "ms", "ma", "master's", "masters"]),
    ("Bachelor", ["bachelor of science", "bachelor of arts", "bs", "ba", "bachelor's", "bachelors", "bsn", "bsa", "bsce", "bsit", "bscs", "bsba", "beed", "bsed"]),
    ("Associate / Diploma", ["associate", "diploma", "vocational", "tesda"]),
    ("High School", ["high school", "secondary education", "senior high"]),
]

def sanitize_education_credentials(raw_education: list[str]) -> list[dict]:
    """
    Strips institution names, locations, and graduation years, retaining only:
    - education_level (Bachelor, Master, Doctorate, Associate, High School)
    - education_field (e.g. Communication, Computer Science, Nursing, Accountancy)
    """
    structured_edu = []
    for edu_str in raw_education:
        if not edu_str or not isinstance(edu_str, str):
            continue
        # Remove school and location mentions if present
        clean_edu = SCHOOL_REGEX.sub(_mask_school_match, edu_str)
        clean_edu = CITY_STATE_ZIP_PATTERN.sub('[LOCATION REDACTED]', clean_edu)
        clean_edu = PH_AND_GLOBAL_LOCATIONS_PATTERN.sub('[LOCATION REDACTED]', clean_edu)
        clean_edu = clean_edu.replace('[ACADEMIC INSTITUTION]', '').replace('[LOCATION REDACTED]', '')
        clean_edu = GRAD_YEAR_PATTERN.sub('', clean_edu)
        clean_edu = re.sub(r'\b(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:(?:19|20)\d{2}|present|current)\b', '', clean_edu, flags=re.IGNORECASE)
        clean_edu = re.sub(r'\b(?:19|20)\d{2}\b', '', clean_edu)
        clean_edu = re.sub(r'[\(\)\[\],|]', ' ', clean_edu)
        clean_edu = re.sub(r'\s+', ' ', clean_edu).strip()

        # Detect level
        level = "Bachelor"
        lower_edu = edu_str.lower()
        for lvl_name, terms in DEGREE_LEVELS:
            if any(re.search(r'\b' + re.escape(t) + r'\b', lower_edu) for t in terms):
                level = lvl_name
                break

        # Extract field
        field_match = re.search(r'(?:in|major\s+in)\s+([A-Za-z][A-Za-z &/\-]{2,60})', clean_edu, re.IGNORECASE)
        if field_match:
            field = re.sub(r'\b(?:University|College|Institute|School|Campus)\b.*$', '', field_match.group(1), flags=re.IGNORECASE).strip().title()
        else:
            field = clean_edu.title() if clean_edu else "General Studies"

        credential_summary = f"{level} in {field}" if field and not field.lower().startswith(level.lower()) else (clean_edu or level)
        structured_edu.append({
            "level": level,
            "field": field,
            "credential_summary": credential_summary
        })

    return structured_edu

def redact_raw_resume_text(text: str, applicant_info: dict | None = None) -> str:
    """
    Performs comprehensive Zero-PII & Bias-Reduction text redaction before scoring or LLM evaluation:
    - Strips Character References sections
    - Redacts applicant direct name, header name, middle names, honorifics, email, phone, web links
    - Masks all geographic locations (street addresses, barangays, cities, provinces, 'City, ST', countries)
    - Masks demographic variables (age, dob, gender, pronouns, marital status, nationality, religion, physical traits, gov IDs)
    - Masks university / school brand names while preserving degree titles and headers
    - Masks graduation batch years
    """
    if not text:
        return ""

    sanitized = REFERENCE_SECTION.split(text, maxsplit=1)[0]

    # 1. Scrub email, phone, links, identifiers, and labeled personal fields FIRST
    sanitized = EMAIL_PATTERN.sub('[EMAIL REDACTED]', sanitized)
    sanitized = PHONE_PATTERN.sub('[PHONE REDACTED]', sanitized)
    sanitized = URL_PATTERN.sub('[LINK REDACTED]', sanitized)
    sanitized = LABELED_ADDRESS_PATTERN.sub('[LOCATION REDACTED]', sanitized)
    sanitized = LABELED_NAME_PATTERN.sub('[CANDIDATE NAME]', sanitized)
    sanitized = HONORIFIC_NAME_PATTERN.sub('[CANDIDATE NAME]', sanitized)
    sanitized = IDENTIFIER_PATTERN.sub('[IDENTIFIER REDACTED]', sanitized)

    # 2. Redact explicit applicant personal info if provided
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

        # Also split full_name into individual word tokens (catching middle names if supplied)
        name_tokens = [full_name, first_name, last_name]
        for tok in re.split(r'[\s,.\-]+', full_name):
            if len(tok) >= 3 and tok.lower() not in _NON_NAME_HEADER_WORDS:
                name_tokens.append(tok)

        for name_part in dict.fromkeys(name_tokens):
            if name_part and len(name_part) >= 2:
                pattern = re.compile(r'\b' + re.escape(name_part) + r'\b', re.IGNORECASE)
                sanitized = pattern.sub('[CANDIDATE NAME]', sanitized)

    # 3. Auto-detect and redact top-of-resume header name (catching middle names, nicknames, or unprovided names)
    sanitized, _ = _detect_and_redact_header_name(sanitized)

    # 4. School prestige redaction (run before location redaction so "Ateneo de Manila" -> [ACADEMIC INSTITUTION])
    sanitized = SCHOOL_REGEX.sub(_mask_school_match, sanitized)

    # 5. Unlabeled Geographic & Location scrubbing (streets, barangays, "City, ST", PH cities/provinces/countries)
    sanitized = STREET_BARANGAY_PATTERN.sub('[LOCATION REDACTED]', sanitized)
    sanitized = CITY_STATE_ZIP_PATTERN.sub('[LOCATION REDACTED]', sanitized)
    sanitized = PH_AND_GLOBAL_LOCATIONS_PATTERN.sub('[LOCATION REDACTED]', sanitized)

    # 6. Demographic & protected attribute scrubbing
    sanitized = GENDER_PRONOUN_PATTERN.sub('[GENDER REDACTED]', sanitized)
    sanitized = MARITAL_NATIONALITY_PATTERN.sub('[DEMOGRAPHIC REDACTED]', sanitized)
    sanitized = AGE_DOB_PATTERN.sub('[AGE/DOB REDACTED]', sanitized)
    sanitized = GRAD_YEAR_PATTERN.sub('[YEAR REDACTED]', sanitized)

    # Collapse repeated adjacent [CANDIDATE NAME] tokens and clean double horizontal spaces
    sanitized = re.sub(r'(?:\[CANDIDATE NAME\][ \t,.]*){2,}', '[CANDIDATE NAME]', sanitized)
    sanitized = re.sub(r'[ \t]+', ' ', sanitized)
    return sanitized.strip()


def redact_candidate_fields(value, applicant_info=None):
    if isinstance(value, str):
        return redact_raw_resume_text(value, applicant_info)
    if isinstance(value, list):
        return [redact_candidate_fields(item, applicant_info) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                item if key in ("schema_version", "candidate_code", "fields_used", "fields_excluded")
                else redact_candidate_fields(item, applicant_info)
            )
            for key, item in value.items()
        }
    return value


def ensure_zero_pii_payload(
    text: str = "",
    profile: dict | None = None,
    applicant_info: dict | None = None
) -> tuple[str, dict | None]:
    """
    Mandatory pre-AI sanitization gate called inside gemini_service.py and semantic_matcher.py
    immediately before constructing any LLM or embedding request.
    Guarantees zero personal data, names, demographics, or locations ever leave the application boundary.
    """
    merged_info = dict(applicant_info) if isinstance(applicant_info, dict) else {}
    if text:
        _, header_tokens = _detect_and_redact_header_name(text)
        if header_tokens:
            existing_name = merged_info.get("applicant_name") or ""
            merged_info["applicant_name"] = f"{existing_name} {' '.join(header_tokens)}".strip()

    clean_text = redact_raw_resume_text(text, merged_info) if text else ""
    clean_profile = redact_candidate_fields(profile, merged_info) if isinstance(profile, dict) else profile
    if isinstance(clean_profile, dict):
        # Ensure experience entries never expose raw company names or locations
        raw_exps = clean_profile.get("experience") or clean_profile.get("experiences") or []
        if isinstance(raw_exps, list):
            for idx, exp in enumerate(raw_exps):
                if isinstance(exp, dict):
                    exp.pop("company", None)
                    exp.pop("location", None)
                    exp["organization"] = f"Organization #{idx + 1}"
    return clean_text, clean_profile


def build_redacted_candidate_profile(
    extracted_text: str,
    parsed_ai_data: dict | None,
    candidate_code: str,
    applicant_info: dict | None = None
) -> tuple[str, dict]:
    """
    Produces:
    1. A fully sanitized resume text representation with zero PII, demographic, or location data.
    2. A structured, bias-reduced candidate JSON payload preserving all job-relevant sections:
       skills, experience, education, certifications, projects, achievements, awards, languages, summary.
    """
    redacted_text = redact_raw_resume_text(extracted_text, applicant_info)

    # Experience redaction: mask company brand prestige & locations
    redacted_experiences = []
    raw_experiences = (parsed_ai_data.get("experiences") if parsed_ai_data else []) or []

    for idx, exp in enumerate(raw_experiences):
        title = redact_raw_resume_text(str(exp.get("job_title", "Professional Role")), applicant_info)
        duration_months = exp.get("duration_months", 0)
        duration_years = round(duration_months / 12.0, 1) if duration_months else 1.0
        duties = exp.get("responsibilities", "")
        anonymized_org = f"Organization #{idx + 1}"

        clean_duties = redact_raw_resume_text(
            duties if isinstance(duties, str) else json.dumps(duties),
            applicant_info
        )

        redacted_experiences.append({
            "title": title,
            "duration_years": duration_years,
            "organization": anonymized_org,
            "duties": clean_duties
        })

    # Education redaction: strip school names and locations, keep degree level and field
    raw_education = (parsed_ai_data.get("education") if parsed_ai_data else []) or []
    if not raw_education and redacted_text:
        deg_matches = re.findall(
            r'((?:Bachelor|Master|Doctor|Associate|Diploma|BS|BA|MS|MA|MBA|PhD)[^\n\r]{3,90})',
            redacted_text,
            re.IGNORECASE
        )
        raw_education = [m.strip() for m in deg_matches[:3] if m.strip()]
    sanitized_education = sanitize_education_credentials(raw_education)

    skills = [redact_raw_resume_text(str(s), applicant_info) for s in ((parsed_ai_data.get("skills") if parsed_ai_data else []) or [])]
    skills_with_evidence = redact_candidate_fields(
        (parsed_ai_data.get("skills_with_evidence") if parsed_ai_data else []) or [],
        applicant_info
    )
    licenses = [redact_raw_resume_text(str(l), applicant_info) for l in ((parsed_ai_data.get("licenses_and_certifications") if parsed_ai_data else []) or [])]
    total_exp_years = float(parsed_ai_data.get("total_experience_years", 0.0)) if parsed_ai_data else 0.0

    # Extract projects and achievements from parsed data or text
    projects = list(parsed_ai_data.get("projects", [])) if parsed_ai_data and parsed_ai_data.get("projects") else []
    if not projects:
        proj_matches = re.findall(
            r'(?:Key\s+Achievement|Achievement|Project|Key\s+Project)[\s:\-]+([^\n]{10,160})',
            redacted_text,
            re.IGNORECASE
        )
        projects = [p.strip() for p in proj_matches[:6] if p.strip()]
    projects = [redact_raw_resume_text(str(p), applicant_info) for p in projects]

    # Extract awards, languages, and summary from parsed data or resume text
    awards = list(parsed_ai_data.get("awards", [])) if parsed_ai_data and parsed_ai_data.get("awards") else []
    if not awards:
        award_matches = re.findall(
            r'([^\n•]*\b(?:Award|Winner|Honor|Dean\'s List|Cum Laude|Recognition)\b[^\n]{0,100})',
            redacted_text,
            re.IGNORECASE
        )
        awards = [a.strip(" •-\t") for a in award_matches[:5] if a.strip(" •-\t")]
    awards = [redact_raw_resume_text(str(a), applicant_info) for a in awards]

    languages = list(parsed_ai_data.get("languages", [])) if parsed_ai_data and parsed_ai_data.get("languages") else []
    if not languages:
        lang_sec = re.search(
            r'(?:^|\n)\s*Languages?\s*(?:\n|:)\s*([\s\S]{3,200}?)(?:\n\s*(?:Education|Experience|Skills|Certific|Awards|Memberships|Projects)|$)',
            redacted_text,
            re.IGNORECASE
        )
        if lang_sec:
            languages = [ln.strip(" •-\t") for ln in re.split(r'[\n,;]+', lang_sec.group(1)) if ln.strip(" •-\t")]
    languages = [redact_raw_resume_text(str(l), applicant_info) for l in languages]

    structured_profile = {
        "schema_version": "2.2",
        "candidate_code": candidate_code,
        "skills": skills,
        "skills_with_evidence": skills_with_evidence,
        "experience": redacted_experiences,
        "total_experience_years": total_exp_years,
        "education": sanitized_education,
        "certifications": licenses,
        "projects": projects,
        "awards": awards,
        "languages": languages,
        "fields_used": ["skills", "experience", "education_level", "certifications", "projects", "awards", "languages"],
        "fields_excluded": ["name", "gender", "age", "address", "location", "school_prestige", "employer_prestige", "graduation_year"]
    }

    return redacted_text, structured_profile
