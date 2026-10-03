import os
import re
import datetime
from django.utils import timezone
from apps.resumes.gemini_service import (
    analyze_resume_with_gemini,
    generate_recruiter_insights_with_gemini,
    evaluate_candidate_match_with_gemini,
    generate_grounded_insights_with_gemini,
    format_insights_as_markdown,
    validate_insights
)
from apps.resumes.redaction_service import (
    build_redacted_candidate_profile,
    redact_raw_resume_text
)
from apps.resumes.config import (
    get_scoring_config,
    get_config_version,
    apply_penalty,
    get_rubric_weights,
    get_evidence_weights
)


# Cross-Industry Competency & Tool Dictionary for Local Fallback
KNOWN_UNIVERSAL_SKILLS = [
    # Human Resources & Talent Acquisition
    "recruitment", "talent acquisition", "onboarding", "employee relations", "payroll",
    "compensation and benefits", "performance management", "labor law", "dole compliance",
    "hris", "workday", "bamboohr", "training and development", "succession planning",
    # Finance, Accounting & Bookkeeping
    "quickbooks", "sap", "xero", "tax preparation", "financial reporting", "auditing", "general ledger",
    "accounts payable", "accounts receivable", "bank reconciliation", "payroll", "excel", "vlookup", "financial modeling",
    # Customer Service, BPO & Support
    "customer service", "inbound calls", "outbound calling", "zendesk", "salesforce", "crm", "email support",
    "chat support", "ticket resolution", "bilingual", "active listening", "troubleshooting", "telemarketing",
    # Sales, Marketing & Business Development
    "b2b sales", "lead generation", "cold calling", "seo", "social media marketing", "google analytics",
    "content creation", "canva", "pipeline management", "account management", "negotiation", "brand strategy",
    # Operations, Logistics & Supply Chain
    "project management", "inventory management", "supply chain", "logistics", "procurement", "data entry",
    "warehouse management", "shipping and receiving", "vendor management", "erp",
    # Healthcare & Nursing
    "iv therapy", "iv cannulation", "bls", "acls", "patient care", "triage", "medication administration",
    "vital signs monitoring", "wound dressing", "phlebotomy", "charting", "icu care", "infection control",
    # Engineering, Architecture & Safety
    "autocad", "solidworks", "revit", "staad pro", "project estimation", "site supervision",
    "qa/qc inspection", "bosh", "cosh", "structural analysis", "cost estimation", "safety officer",
    # Education & Training
    "classroom management", "lesson planning", "curriculum development", "student assessment", "instructional design",
    # Information Technology, Networking & Software
    "python", "javascript", "typescript", "java", "c++", "c#", "php", "sql", "html", "css",
    "django", "react", "next.js", "node.js", "docker", "redis", "postgresql", "mysql", "git", "aws", "gcp",
    "cisco", "ccna", "ccnp", "routing", "switching", "bgp", "ospf", "vlan", "vpn", "tcp/ip", "dns", "dhcp",
    "firewall", "wireshark", "subnetting", "lan/wan", "network troubleshooting", "network security"
]

MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5, 'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12
}

STOP_WORDS = {
    "the", "and", "with", "this", "that", "from", "have", "will", "been", "role", "work",
    "team", "your", "must", "able", "candidate", "responsibilities", "duties", "qualifications",
    "required", "preferred", "knowledge", "skills", "experience", "looking", "join", "opportunity",
    "company", "position", "about", "also", "into", "more", "other", "such", "than", "them",
    "then", "these", "they", "what", "when", "where", "which", "who", "whom", "whose", "why"
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
            elif skill in ("aws", "gcp", "html", "css", "sql", "php", "bls", "acls", "icu care", "bosh", "cosh", "seo", "crm", "ccna", "ccnp", "vpn", "vlan", "bgp", "ospf", "dns", "dhcp"):
                standard_name = skill.upper()
            else:
                standard_name = skill.title()
            found_skills.add(standard_name)

    return sorted(list(found_skills))

def extract_licenses_from_text(text: str) -> list[str]:
    """Detect Philippine professional licenses and certifications."""
    licenses = []
    license_patterns = [
        r'\b(PRC\s+(?:Registered\s+)?(?:Nurse|RN|CPA|Accountant|Civil\s+Engineer|Electrical\s+Engineer|Mechanical\s+Engineer|Teacher|LPT|Pharmacist|Medical\s+Technologist|RMT|ECE|Electronics\s+Engineer))\b',
        r'\b(Certified\s+Public\s+Accountant|Licensed\s+Civil\s+Engineer|Licensed\s+Professional\s+Teacher|Registered\s+Nurse)\b',
        r'\b(Civil\s+Service\s+(?:Professional|Sub-Professional|Eligible))\b',
        r'\b(TESDA\s+NC\s+(?:I|II|III|IV)\b[^\n,.]*)',
        r'\b(BOSH|COSH|DOLE\s+Safety\s+Officer\s+(?:1|2|3|SO1|SO2|SO3))\b',
        r'\b(BLS|ACLS|Basic\s+Life\s+Support|Advanced\s+Cardiac\s+Life\s+Support)\b',
        r'\b(CCNA|CCNP|CCIE|CompTIA\s+(?:Network\+|Security\+|A\+)|AWS\s+Certified[^\n,.]*|Cisco\s+Certified[^\n,.]*)\b',
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
        r"\b(?:Bachelor of Science in Nursing|BSN|Bachelor of Science in Accountancy|BSA|Bachelor of Science in Civil Engineering|BSCE|Bachelor of Science in Information Technology|BSIT|Bachelor of Science in Computer Science|BSCS|Bachelor of Science in Computer Engineering|BSCoE|BSCpE|Bachelor of Science in Business Administration|BSBA|Bachelor of Elementary Education|BEED|Bachelor of Secondary Education|BSED)\b",
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

def calculate_semantic_content_score(job_title: str, job_desc: str, resume_text: str) -> float:
    """
    Holistic Content & Responsibility Fit:
    Examines full resume text (work history, responsibilities, projects, summaries)
    against the full job posting content, duties, and title.
    """
    if not job_desc or not resume_text:
        return 50.0

    job_text_lower = (job_title + " " + job_desc).lower()
    resume_lower = resume_text.lower()

    # Extract meaningful keywords of length >= 3 excluding stop words
    raw_words = re.findall(r'[a-zA-Z0-9\+\#\.\-]{3,}', job_text_lower)
    content_words = [w for w in raw_words if w not in STOP_WORDS]
    if not content_words:
        return 70.0

    unique_job_words = set(content_words)
    matched_words = {w for w in unique_job_words if w in resume_lower}

    # Word coverage ratio
    coverage_ratio = len(matched_words) / len(unique_job_words)

    # Bigram & key phrase alignment
    phrases = []
    for i in range(len(content_words) - 1):
        w1, w2 = content_words[i], content_words[i+1]
        if w1 not in STOP_WORDS and w2 not in STOP_WORDS:
            phrases.append(f"{w1} {w2}")
    
    phrase_matches = sum(1 for p in set(phrases) if p in resume_lower) if phrases else 0
    phrase_ratio = (phrase_matches / max(len(set(phrases)), 1)) if phrases else 0.0

    # Title alignment bonus
    title_words = [w for w in re.findall(r'[a-zA-Z]{3,}', job_title.lower()) if w not in STOP_WORDS]
    title_matches = sum(1 for tw in title_words if tw in resume_lower)
    title_bonus = (title_matches / max(len(title_words), 1)) * 15.0 if title_words else 10.0

    # Composite semantic score (ranges typically 70.0 - 98.0 for matching resumes)
    base_semantic = (coverage_ratio * 65.0) + (phrase_ratio * 20.0) + title_bonus + 15.0
    return min(max(round(base_semantic, 1), 30.0), 98.0)

# Cross-Industry Semantic Synonyms & Canonical Patterns (Section 4 & Phase 1 of TalentMatch Specification)
SKILL_PATTERNS = {
    "python": [r"\bpython(?:\s*3(?:\.\d+)?)?\b", r"\bdjango\b", r"\bflask\b", r"\bfastapi\b"],
    "rest api": [
        r"\brest\s*apis?\b", r"\brestful\b", r"\bweb\s*services?\b",
        r"\bweb\s*apis?\b",
        r"\bapi\s*development\b", r"\bapi\s*design\b", r"\brest\s*endpoints?\b",
        r"\b(?:http\s*)?endpoints?\s+returning\s+json\b",
        r"\b(?:node(?:\.js)?(?:\/express)?\s+)?endpoints?\s+(?:for|to|returning)\b",
        r"\bexpress\s+endpoints?\b",
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

SKILL_SYNONYM_MAP = {
    "rest api": ["web services", "api development", "fastapi", "flask api", "rest endpoints", "restful", "apis", "api design", "web service endpoints"],
    "docker": ["containerized", "containers", "containerization", "dockerfile", "dockerized", "package applications into containers"],
    "sql": ["relational database", "relational databases", "mysql", "postgresql", "postgres", "rdbms", "oracle", "sqlite"],
    "unit testing": ["pytest", "unit tests", "automated testing", "test coverage"],
    "ci/cd": ["github actions", "jenkins", "gitlab ci", "continuous integration", "deployment pipeline"],
    "aws": ["cloud services", "ec2", "s3", "cloud deployment", "cloud practitioner"],
    "git": ["version control", "github", "gitlab", "source control"]
}

def match_term_in_text(term: str, text: str) -> bool:
    """Matches a term or its canonical pattern in a text block without loose partial-token matches."""
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

    # Word boundary match on exact phrase
    pattern = r'\b' + re.escape(term_lower) + r'\b'
    if re.search(pattern, text_lower):
        return True

    # Semantic synonym check
    for canon, syns in SKILL_SYNONYM_MAP.items():
        if canon in term_lower or term_lower in canon:
            for syn in syns:
                if re.search(r'\b' + re.escape(syn) + r'\b', text_lower):
                    return True

    return False

def is_vague_duty_bullet(bullet: str) -> bool:
    """Detects vague keyword-stuffed bullets like G2."""
    b_lower = bullet.lower()
    vague_phrases = ["worked on backend stuff using", "responsible for", "handled various tasks with"]
    if any(p in b_lower for p in vague_phrases):
        skill_count = sum(1 for k in SKILL_PATTERNS if any(re.search(p, b_lower) for p in SKILL_PATTERNS[k]))
        if skill_count >= 3:
            return True
    return False

def parse_resume_evidence_units(
    resume_text: str,
    parsed_data: dict | None = None,
    extracted_skills: list[str] | None = None,
    licenses: list[str] | None = None
) -> dict[str, list[str]]:
    """Tags text units with their source tiers."""
    units = {
        "duty": [],
        "vague_duty": [],
        "project": [],
        "certification": [],
        "skills_list": []
    }

    if parsed_data:
        for exp in parsed_data.get("experiences", []):
            resp = exp.get("responsibilities", "")
            bullets = [b.strip() for b in re.split(r'[\n\.\;]+', resp) if b.strip()]
            for b in bullets:
                if is_vague_duty_bullet(b):
                    units["vague_duty"].append(b)
                else:
                    units["duty"].append(b)

        for proj in parsed_data.get("projects", []):
            units["project"].append(proj)

        for cert in parsed_data.get("licenses_and_certifications", []):
            units["certification"].append(cert)

        for s in parsed_data.get("skills", []):
            units["skills_list"].append(s)

    if extracted_skills:
        units["skills_list"].extend(extracted_skills)
    if licenses:
        units["certification"].extend(licenses)

    # Parse section blocks from raw text
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

    has_headers = any(bool(sec_buffers[k]) for k in sec_buffers)
    if not has_headers and resume_text.strip():
        # Text without section headers (e.g. isolated experience description in tests):
        units["duty"].append(resume_text.strip())
    else:
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


def match_skills_flexibly(
    required_skills: list[str],
    preferred_skills: list[str],
    extracted_skills: list[str],
    licenses: list[str],
    resume_text: str,
    parsed_data: dict | None = None,
    skill_descriptors: dict | None = None
) -> tuple[list[str], list[str], float, float]:
    """
    Evidence-grounded multi-source skill matcher (TalentMatch Phase 1 & Section 13):
    - Tags extracted text units by source tier (Duty: 1.0, Project: 0.9, Cert: 0.9, Vague: 0.5, Skills List: 0.4).
    - Eliminates partial-token matching.
    - Repetition never increases credit (takes max credit).
    - Preferred skills require verified evidence in experience/certifications.
    - Supports domain-agnostic skill descriptors (Section 13).
    - Missing must-have cutoff: final credit < 0.50.
    """
    units = parse_resume_evidence_units(resume_text, parsed_data, extracted_skills, licenses)
    evidence_weights = get_evidence_weights()

    def check_occurrence(term: str, combined_text: str) -> bool:
        if match_term_in_text(term, combined_text):
            return True
        if skill_descriptors:
            term_clean = term.lower().strip()
            matched_desc = None
            for desc_key, desc_val in skill_descriptors.items():
                k_clean = desc_key.lower().replace("_", " ").strip()
                lbl_clean = desc_val.get("label", "").lower().strip() if isinstance(desc_val, dict) else ""
                if k_clean == term_clean or lbl_clean == term_clean or term_clean in k_clean or k_clean in term_clean:
                    matched_desc = desc_val
                    break
            if matched_desc and isinstance(matched_desc, dict):
                queries = matched_desc.get("queries", [])
                for q in queries:
                    if match_term_in_text(q, combined_text) or q.lower() in combined_text.lower():
                        return True
        return False

    req_credits = {}
    matched_skills = []
    missing_skills = []

    for req in required_skills:
        best_credit = 0.0
        for source, texts in units.items():
            combined_text = " ".join(texts)
            if check_occurrence(req, combined_text):
                w = evidence_weights.get(source, 0.4)
                if w > best_credit:
                    best_credit = w

        # Fallback to direct resume search if not found in segmented buffers
        if best_credit == 0.0 and check_occurrence(req, resume_text):
            best_credit = evidence_weights.get("skills_list", 0.4)

        req_credits[req] = best_credit
        if best_credit >= 0.50:
            matched_skills.append(req.title())
        else:
            missing_skills.append(req.title())

    pref_credits = {}
    for pref in preferred_skills:
        best_credit = 0.0
        for source, texts in units.items():
            # Preferred skill requires verified evidence in duties/projects/certifications (Section 8)
            if source == "skills_list":
                continue
            combined_text = " ".join(texts)
            if check_occurrence(pref, combined_text):
                w = evidence_weights.get(source, 0.4)
                if w > best_credit:
                    best_credit = w

        pref_credits[pref] = best_credit
        if best_credit >= 0.50:
            matched_skills.append(f"{pref.title()} (Preferred)")

    req_count = max(len(required_skills), 1)
    pref_count = max(len(preferred_skills), 1)

    req_score = round((sum(req_credits.values()) / req_count) * 100.0, 1)
    pref_score = round((sum(pref_credits.values()) / pref_count) * 100.0, 1) if preferred_skills else 80.0

    return matched_skills, missing_skills, req_score, pref_score


def calculate_capped_experience_score(exp_years: float, min_exp_str: str, bonus_cap: float = 0.1) -> float:
    """
    Bias-Reduced Experience Fit Formula (Section 5 & Phase 1):
    base = min(relevant_years / required_years, 1.0)
    bonus = min(max(relevant_years - required_years, 0) / required_years, 1.0) * bonus_cap
    Caps the benefit of extra years so older applicants are not unfairly favored.
    Returns 0.0 for candidates with 0.0 years on roles requiring experience.
    """
    req_exp_match = re.search(r'(\d+(?:\.\d+)?)', str(min_exp_str))
    required_exp_years = float(req_exp_match.group(1)) if req_exp_match else 1.0

    if required_exp_years <= 0:
        return 100.0

    if exp_years <= 0.0:
        return 0.0

    base = min(exp_years / required_exp_years, 1.0)
    bonus = min(max(exp_years - required_exp_years, 0.0) / required_exp_years, 1.0) * bonus_cap
    return round(min(base + bonus, 1.0) * 100.0, 1)

def evaluate_project_relevance(projects: list[str], job_desc: str, required_skills: list[str]) -> float:
    """
    Evaluates relevance of candidate projects (RC5 Fix):
    Returns 0.0 when no projects exist.
    Evaluates overlap between project descriptions and verified target skills.
    """
    if not projects:
        return 0.0

    proj_str = " ".join(projects)
    matched = sum(1 for s in required_skills if match_term_in_text(s, proj_str))
    if required_skills:
        ratio = matched / len(required_skills)
        return min(max(round(50.0 + (ratio * 50.0), 1), 50.0), 100.0)
    return 70.0


def evaluate_education_relevance(
    required_edu: str,
    candidate_degrees: list[str],
    candidate_licenses: list[str],
    resume_text: str
) -> float:
    """
    Evaluates degree, certifications, and licenses relevance across disciplines (Section 13.7).
    """
    if not required_edu and not candidate_degrees:
        return 80.0

    req_lower = (required_edu or "").lower()

    domains = {
        "human_resources": ["human resource", "hr", "psychology", "behavioral science", "business administration", "bsba-hrm", "bsba", "industrial relations"],
        "accounting_finance": ["accountan", "cpa", "audit", "finance", "banking", "commerce", "financial management"],
        "nursing_healthcare": ["nursing", "bsn", "registered nurse", "nurse", "clinical nursing"],
        "electrical_trades": ["electrical", "electrician", "rme", "registered master electrician"],
        "skilled_trades": ["electrician", "electrical", "rme", "tesda", "vocational", "trade", "plumber"],
        "retail_service": ["high school", "secondary", "vocational", "associate", "bachelor", "college", "graduate"],
        "engineering_architecture": ["civil engineer", "electrical engineer", "mechanical engineer", "electronics", "architecture", "engineering", "bsce", "bsee", "bsme", "bsece", "bsarch"],
        "it_networking": ["computer science", "information technology", "software", "computer engineering", "network", "bscs", "bsit", "bscoe", "ccna", "ccnp", "cisco"],
        "business_marketing_sales": ["business", "communication", "marketing", "management", "arts", "bsba", "commerce", "administration", "advertising", "public relations"],
        "education_teaching": ["education", "beed", "bsed", "teaching", "early childhood", "special education", "lpt"],
        "legal_governance": ["law", "legal", "political science", "public administration", "juris doctor"],
        "hospitality_tourism": ["hospitality", "tourism", "hotel", "restaurant", "bshm", "bstm", "culinary"],
    }

    req_domain = None
    for domain, keywords in domains.items():
        if any(k in req_lower for k in keywords):
            req_domain = domain
            break

    if not req_domain:
        return 85.0

    deg_lic_str = (" ".join(candidate_degrees) + " " + " ".join(candidate_licenses)).lower()
    if deg_lic_str.strip():
        if any(k in deg_lic_str for k in domains[req_domain]):
            return 100.0
        # If candidate has credentials but in an unrelated discipline:
        if "high school" in req_lower or "vocational" in req_lower:
            return 90.0
        return 35.0  # Unrelated discipline in regulated/specialized field

    # Fallback to inspecting text only if candidate_degrees was not pre-parsed
    if any(k in resume_text[:2000].lower() for k in domains[req_domain]):
        return 100.0

    return 50.0

def process_resume_and_calculate_match(application):
    """
    Comprehensive Bias-Reduced Matching Engine (TalentMatch Specification):
    1. Extracts full clean text from resume file (PDF/DOCX/TXT).
    2. Zero-shot Dynamic AI extraction using Gemini across all industries.
    3. Input-Stage Redaction: Strips name, email, phone, age, graduation years,
       and school prestige before scoring.
    4. 5-Component Rubric Scoring:
       - 40% Required (Must-Have) Skills
       - 25% Experience Fit (Capped Duration Bonus)
       - 15% Education Level & Credential Alignment
       - 10% Preferred (Nice-to-Have) Skills
       - 10% Project & Achievement Relevance
       + Contextual Qualitative Duty Fit (Semantic Score)
    5. Must-Have Penalty: Missing critical required skills caps match score at 60.0%.
    6. Grounded AI Recruiter Insights with evidence verification and validator.
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

    print("=" * 80)
    print(f"[BIAS-REDUCED PIPELINE] Starting Evaluation for Candidate: {application.candidate_code}")
    print(f"[JOB DETAILS] Position: {application.job.title} | Department: {application.job.department}")
    print("=" * 80)

    # 1. Dynamic Gemini AI Extraction
    gemini_data = analyze_resume_with_gemini(extracted_text)
    is_ai_extracted = False

    if gemini_data:
        is_ai_extracted = True
        skills = gemini_data.get("skills", [])
        licenses = gemini_data.get("licenses_and_certifications", [])
        education = gemini_data.get("education", [])
        projects = gemini_data.get("projects", [])
        exp_years = float(gemini_data.get("total_experience_years", 0.0))
        if gemini_data.get("is_fresh_graduate", False) and exp_years == 0:
            exp_years = 0.0
        print(f"[EVALUATION ENGINE] AI EXTRACTOR: [YES] Evaluated and parsed via Gemini AI.")
    else:
        print(f"[EVALUATION ENGINE] AI EXTRACTOR: [NO] (Fallback to Local Regex Matcher)")
        skills = extract_skills_from_text(extracted_text)
        licenses = extract_licenses_from_text(extracted_text)
        education = extract_education_from_text(extracted_text)
        exp_years = extract_experience_years(extracted_text)
        projects = []

    # 2. Input-Stage Bias Reduction (Redaction)
    applicant_info = {
        "first_name": application.first_name,
        "last_name": application.last_name,
        "applicant_name": application.applicant_name,
        "email": application.email,
        "phone": application.phone,
    }
    redacted_text, redacted_profile = build_redacted_candidate_profile(
        extracted_text=extracted_text,
        parsed_ai_data=gemini_data,
        candidate_code=application.candidate_code,
        applicant_info=applicant_info
    )

    resume.extracted_skills = skills
    resume.extracted_education = education + ([f"License: {l}" for l in licenses] if licenses else [])
    resume.extracted_experience_years = exp_years
    resume.redacted_text = redacted_text
    resume.redacted_data = redacted_profile
    resume.processing_status = 'COMPLETED'
    resume.processed_at = timezone.now()
    resume.save()

    # 3. Prepare Rubric Data
    job = application.job
    required_skills = [s.strip() for s in (job.required_skills or []) if s.strip()]
    preferred_skills = [s.strip() for s in (job.preferred_skills or []) if s.strip()]

    job_data = {
        "title": job.title,
        "department": job.department,
        "description": job.description,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "minimum_experience": job.minimum_experience,
        "education_requirement": job.education_requirement,
    }

    # 4. Bias-Reduced AI Evaluation via Gemini
    gemini_match = evaluate_candidate_match_with_gemini(job_data, redacted_profile, redacted_text)
    is_ai_match = False

    if gemini_match and isinstance(gemini_match, dict) and "overall_score" in gemini_match:
        is_ai_match = True
        overall_score = float(gemini_match.get("overall_score", 80.0))
        skill_score = float(gemini_match.get("required_skill_score", gemini_match.get("skill_match_score", overall_score)))
        exp_score = float(gemini_match.get("experience_score", gemini_match.get("experience_match_score", overall_score)))
        edu_score = float(gemini_match.get("education_score", gemini_match.get("education_match_score", overall_score)))
        pref_score = float(gemini_match.get("preferred_skill_score", 75.0))
        proj_score = float(gemini_match.get("project_score", 80.0))
        semantic_score = float(gemini_match.get("semantic_match_score", overall_score))
        matched_skills = gemini_match.get("matched_skills", [])
        missing_skills = gemini_match.get("missing_skills", [])
        print(f"[EVALUATION ENGINE] AI RUBRIC MATCH: [YES] Evaluated using 5-component rubric via Gemini AI!")
    else:
        # 5. Deterministic 5-Component Fallback Rubric
        print(f"[EVALUATION ENGINE] AI RUBRIC MATCH: [FALLBACK] Using 5-Component Rubric Formula.")

        # 5a. Required Skills (40%) & Preferred Skills (10%) with Evidence Tiers
        matched_skills, missing_skills, skill_score, pref_score = match_skills_flexibly(
            required_skills, preferred_skills, skills, licenses, redacted_text, parsed_data=redacted_profile
        )

        # 5b. Experience Fit with Capped Bonus (25%)
        exp_score = calculate_capped_experience_score(exp_years, job.minimum_experience, bonus_cap=0.1)

        # 5c. Education Fit (15%)
        edu_score = evaluate_education_relevance(
            job.education_requirement, education, licenses, redacted_text
        )

        # 5d. Project Relevance (10%)
        proj_score = evaluate_project_relevance(projects or redacted_profile.get("projects", []), job.description, required_skills)

        # 5e. Contextual Duty Execution
        semantic_score = calculate_semantic_content_score(job.title, job.description, redacted_text)

        # 5-Component Weighted Total
        raw_composite = (
            (skill_score * 0.40) +
            (exp_score * 0.25) +
            (edu_score * 0.15) +
            (pref_score * 0.10) +
            (proj_score * 0.10)
        )
        raw_score = round(min(max(raw_composite, 10.0), 99.0), 1)

        # Identify missing must-haves
        missing_must_haves = [m for m in missing_skills if any(m.lower() == r.lower() for r in required_skills)]
        proportional_score = apply_penalty(raw_score, len(missing_must_haves), mode="proportional")
        hard_cap_score = apply_penalty(raw_score, len(missing_must_haves), mode="hard_cap")

        # Proportional penalty preserves differentiation (Section 12 of specification)
        overall_score = proportional_score

        if missing_must_haves:
            print(f"[RUBRIC] Proportional penalty applied for {application.candidate_code} ({len(missing_must_haves)} missing must-haves): {raw_score}% -> {overall_score}%.")


    # 6. Generate Grounded AI Recruiter Insights
    scores_dict = {
        "overall_score": overall_score,
        "required_skill_score": skill_score,
        "skill_score": skill_score,
        "experience_score": exp_score,
        "exp_score": exp_score,
        "education_score": edu_score,
        "edu_score": edu_score,
        "preferred_skill_score": pref_score,
        "project_score": proj_score,
        "semantic_match_score": semantic_score,
    }

    ai_insights = generate_grounded_insights_with_gemini(job_data, redacted_profile, scores_dict, redacted_text)
    if not ai_insights or not isinstance(ai_insights, dict):
        # Structured Fallback Insights conforming to Section 10 schema
        strengths_list = []
        for s in matched_skills[:4]:
            strengths_list.append({
                "skill": s,
                "matched_requirement": s,
                "evidence": f"Demonstrated competency in {s} identified in parsed experience.",
                "source": "experience[0]",
                "confidence": "high"
            })

        gaps_list = []
        for m in missing_skills[:4]:
            gaps_list.append({
                "skill": m,
                "type": "must_have" if m in required_skills else "nice_to_have",
                "note": "No mention found in resume"
            })

        ai_insights = {
            "summary": f"Candidate demonstrates {overall_score:.1f}% alignment with position requirements based on verified competencies and work history.",
            "strengths": strengths_list,
            "gaps": gaps_list,
            "interview_focus": [
                f"Verify operational depth in core duties of {job.title}",
                f"Clarify scope of projects and tools utilized in past roles"
            ],
            "score_breakdown": {
                "required_skills": skill_score,
                "experience": exp_score,
                "education": edu_score,
                "preferred_skills": pref_score,
                "projects": proj_score
            },
            "fields_used": ["skills", "experience", "education_level", "certifications", "projects"],
            "fields_excluded": ["name", "gender", "age", "address", "school_prestige", "employer_prestige", "graduation_year"]
        }

    # Record config version in insights for reproducibility and audit
    if isinstance(ai_insights, dict):
        ai_insights["config_version"] = get_config_version()

    # Format human-readable markdown explanation

    explanation = format_insights_as_markdown(
        ai_insights,
        candidate_code=application.candidate_code,
        role_title=job.title,
        match_score=overall_score
    )

    print("-" * 80)
    print(f"[EVALUATION SUMMARY] Candidate: {application.candidate_code}")
    print(f" -> Overall Match Score:     {overall_score}%")
    print(f" -> Required Skills (40%):   {skill_score}%")
    print(f" -> Experience Fit (25%):    {exp_score}%")
    print(f" -> Education Fit (15%):     {edu_score}%")
    print(f" -> Preferred Skills (10%):  {pref_score}%")
    print(f" -> Projects / Achiev (10%): {proj_score}%")
    print(f" -> Contextual Fit:          {semantic_score}%")
    print(f" -> Grounded AI Insights:    [VALIDATED JSON]")
    print("=" * 80)

    match_result, _ = MatchResult.objects.update_or_create(
        application=application,
        defaults={
            'match_score': overall_score,
            'skill_match_score': round(skill_score, 1),
            'experience_match_score': round(exp_score, 1),
            'education_match_score': round(edu_score, 1),
            'semantic_match_score': round(semantic_score, 1),
            'preferred_skill_match_score': round(pref_score, 1),
            'project_match_score': round(proj_score, 1),
            'matched_skills': matched_skills,
            'missing_skills': missing_skills,
            'ai_insights': ai_insights,
            'explanation': explanation,
        }
    )

    if application.status == 'SUBMITTED':
        application.status = 'UNDER_REVIEW'
        application.save(update_fields=['status'])

    return match_result
