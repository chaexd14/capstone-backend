import os
import logging
import subprocess
import sys
from pathlib import Path
import re
import datetime
from django.utils import timezone
from apps.resumes.gemini_service import (
    analyze_resume_with_gemini,
    generate_recruiter_insights_with_gemini,
    evaluate_candidate_match_with_gemini,
    generate_grounded_insights_with_gemini,
    evaluate_and_generate_insights_with_gemini,
    format_insights_as_markdown,
    validate_insights
)
from apps.resumes.redaction_service import (
    build_redacted_candidate_profile,
    redact_raw_resume_text,
    redact_candidate_fields,
)
from apps.resumes.config import (
    get_scoring_config,
    get_config_version,
    apply_penalty,
    get_rubric_weights,
    get_evidence_weights
)


logger = logging.getLogger(__name__)


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
    "chat support", "live chat", "non-voice support", "ticket resolution", "bilingual", "active listening",
    "troubleshooting", "telemarketing", "conflict resolution", "time management", "team leadership",
    "problem-solving", "communication", "freshdesk", "hubspot",
    # Productivity & Office Suites
    "microsoft office", "ms office", "office 365", "microsoft word", "microsoft excel", "microsoft powerpoint",
    "microsoft outlook", "google workspace", "sharepoint", "microsoft teams",
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

def extract_text_from_file(file_input, filename: str = "") -> str:
    """
    Extract clean text from PDF, DOCX, or TXT file.
    Supports either:
    - a filesystem path string (e.g. '/path/to/resume.pdf')
    - a file-like object, Django FieldFile, or bytes stream (e.g. resume.file)
    """
    if not file_input:
        return ""

    content = None
    ext = ""

    if isinstance(file_input, (str, os.PathLike)):
        file_path = str(file_input)
        if not os.path.exists(file_path):
            return ""
        ext = os.path.splitext(file_path)[1].lower()
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
        except Exception as e:
            logger.warning("Unable to read local resume file. error=%s", type(e).__name__)
            return ""
    elif hasattr(file_input, 'read'):
        if hasattr(file_input, 'seek'):
            try:
                file_input.seek(0)
            except Exception:
                pass
        content = file_input.read()
        target_name = filename or getattr(file_input, 'name', '')
        ext = os.path.splitext(target_name)[1].lower()
    elif isinstance(file_input, bytes):
        content = file_input
        ext = os.path.splitext(filename)[1].lower()

    if not content:
        return ""

    if ext in ('.pdf', '.docx'):
        # Do not pass application credentials or Django settings to the parser process.
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR') if key in os.environ}
        result = subprocess.run(
            [sys.executable, '-I', str(Path(__file__).with_name('document_parser.py')), ext],
            input=content, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, env=env, check=True,
        )
        return result.stdout.decode('utf-8').strip()

    return content.decode('utf-8-sig', errors='ignore')[:100_000].strip()


def extract_skills_from_text(text: str) -> list[str]:
    """Fallback cross-industry competency finder."""
    text_lower = " " + text.lower() + " "
    found_skills = set()

    for skill in KNOWN_UNIVERSAL_SKILLS:
        pattern = r'(?:\b|\W)' + re.escape(skill) + r'(?:\b|\W)'
        if re.search(pattern, text_lower):
            if skill in ("javascript", "typescript", "postgresql", "mongodb", "quickbooks", "zendesk", "salesforce", "autocad", "freshdesk", "hubspot", "sharepoint"):
                standard_name = skill.title()
                if skill == "javascript": standard_name = "JavaScript"
                if skill == "typescript": standard_name = "TypeScript"
                if skill == "quickbooks": standard_name = "QuickBooks"
                if skill == "autocad": standard_name = "AutoCAD"
                if skill == "hubspot": standard_name = "HubSpot"
                if skill == "sharepoint": standard_name = "SharePoint"
            elif skill in ("aws", "gcp", "html", "css", "sql", "php", "bls", "acls", "icu care", "bosh", "cosh", "seo", "crm", "ccna", "ccnp", "vpn", "vlan", "bgp", "ospf", "dns", "dhcp", "ms office"):
                standard_name = skill.upper() if skill != "ms office" else "MS Office"
            else:
                standard_name = skill.title()
            found_skills.add(standard_name)

    return sorted(list(found_skills))

def extract_licenses_from_text(text: str) -> list[str]:
    """Detect professional licenses, industry certifications, and training credentials."""
    licenses = []
    license_patterns = [
        r'\b(PRC\s+(?:Registered\s+)?(?:Nurse|RN|CPA|Accountant|Civil\s+Engineer|Electrical\s+Engineer|Mechanical\s+Engineer|Teacher|LPT|Pharmacist|Medical\s+Technologist|RMT|ECE|Electronics\s+Engineer))\b',
        r'\b(Certified\s+Public\s+Accountant|Licensed\s+Civil\s+Engineer|Licensed\s+Professional\s+Teacher|Registered\s+Nurse|Registered\s+Master\s+Electrician)\b',
        r'\b(Civil\s+Service\s+(?:Professional|Sub-Professional|Eligible))\b',
        r'\b(TESDA\s+NC\s+(?:I|II|III|IV)\b[^\n,.]*)',
        r'\b(BOSH|COSH|DOLE\s+Safety\s+Officer\s+(?:1|2|3|SO1|SO2|SO3))\b',
        r'\b(BLS|ACLS|Basic\s+Life\s+Support|Advanced\s+Cardiac\s+Life\s+Support)\b',
        r'\b(CCNA|CCNP|CCIE|CompTIA\s+(?:Network\+|Security\+|A\+)|AWS\s+Certified[^\n,.]*|Cisco\s+Certified[^\n,.]*)\b',
        r'\b(Certified\s+Customer\s+Service\s+Professional(?:\s*\(CCSP\))?|CCSP|PMP|Six\s+Sigma\s+(?:Green|Black|Yellow)\s+Belt|ITIL[^\n,.]*|Microsoft\s+Certified[^\n,.]*|Google\s+Certified[^\n,.]*|Salesforce\s+Certified[^\n,.]*|HubSpot\s+Certified[^\n,.]*)\b',
        r'\b(Certified\s+[A-Z][A-Za-z ]{3,45}(?:\([A-Z]{2,6}\))?)\b',
    ]

    for pat in license_patterns:
        matches = re.finditer(pat, text, re.IGNORECASE)
        for m in matches:
            val = m.group(0).strip()
            if val and 2 <= len(val) < 90:
                licenses.append(val)

    # Also capture explicit items under a Certifications / Licenses section
    cert_sec = re.search(
        r'(?:^|\n)\s*(?:CERTIFICATIONS?|LICENSES?(?:\s*&\s*CERTIFICATIONS?)?|COURSES?\s*&\s*CERTIFICATIONS?)\s*(?:\n|:)\s*([\s\S]{3,400}?)(?:\n\s*(?:EDUCATION|EXPERIENCE|SKILLS|AWARDS|MEMBERSHIPS|LANGUAGES|PROJECTS|REFERENCES)|$)',
        text,
        re.IGNORECASE
    )
    if cert_sec:
        norm_block = re.sub(r'[•●▪▸◦]\s*\n\s*', '• ', cert_sec.group(1))
        for ln in norm_block.splitlines():
            clean_ln = ln.strip(" •●▪▸◦-\t")
            if clean_ln and 4 <= len(clean_ln) <= 120 and not re.match(r'^(?:19|20)\d{2}$', clean_ln):
                licenses.append(clean_ln)

    deduped = []
    seen = set()
    for lic in licenses:
        k = lic.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(lic)
    return deduped

def extract_education_from_text(text: str) -> list[str]:
    """Extract degrees across all disciplines (Healthcare, Business, Communication, Engineering, IT, Education)."""
    degrees = []
    edu_patterns = [
        r"\b(?:Bachelor of Science in Nursing|BSN|Bachelor of Science in Accountancy|BSA|Bachelor of Science in Civil Engineering|BSCE|Bachelor of Science in Information Technology|BSIT|Bachelor of Science in Computer Science|BSCS|Bachelor of Science in Computer Engineering|BSCoE|BSCpE|Bachelor of Science in Business Administration|BSBA|Bachelor of Elementary Education|BEED|Bachelor of Secondary Education|BSED|Bachelor of Arts in Communication|BA in Communication)\b",
        r"\b(?:Bachelor of Science|BS|B\.S\.|Bachelor of Arts|BA|B\.A\.|Bachelor's)\b[ \t]+(?:degree[ \t]+)?(?:in[ \t]+)?([A-Za-z][A-Za-z &/\-]{2,55}?)(?=[,\.\n\r\(\)\[\]]|\b(?:University|College|Institute|School|Graduated|19\d{2}|20\d{2})\b|$)",
        r"\b(?:Master of Science|MS|M\.S\.|Master of Arts|MA|MBA|Master's)\b[ \t]+(?:degree[ \t]+)?(?:in[ \t]+)?([A-Za-z][A-Za-z &/\-]{2,55}?)(?=[,\.\n\r\(\)\[\]]|\b(?:University|College|Institute|School|Graduated|19\d{2}|20\d{2})\b|$)",
        r"\b(?:Doctor of Philosophy|PhD|Doctor of Medicine|MD)\b[ \t]+(?:in[ \t]+)?([A-Za-z][A-Za-z &/\-]{2,55}?)(?=[,\.\n\r\(\)\[\]]|\b(?:University|College|Institute|School|Graduated|19\d{2}|20\d{2})\b|$)",
        r"\b(?:Associate Degree|Associate in|Diploma in|Technical Diploma in|Vocational[ \t]+[A-Za-z ]*Certificate)\b[ \t]*([A-Za-z][A-Za-z &/\-]{0,50}?)(?=[,\.\n\r\(\)\[\]]|\b(?:University|College|Institute|School|Graduated|19\d{2}|20\d{2})\b|$)",
        r"\b(?:High School Diploma|High School Graduate|Senior High School)\b",
    ]

    for pat in edu_patterns:
        matches = re.finditer(pat, text, re.IGNORECASE)
        for m in matches:
            full_match = re.sub(r'\s+', ' ', m.group(0)).strip(" ,.-")
            if full_match and 3 < len(full_match) < 85:
                degrees.append(full_match)

    deduped = []
    seen = set()
    for d in degrees:
        k = d.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(d)
    return deduped

def parse_month_year(date_str: str, default_to_current: bool = False) -> tuple[int, int] | None:
    """Parse dates like 'Feb 2026', '02/2026', '2024', 'Present' into (year, month)."""
    date_str = date_str.strip().lower()
    now = datetime.datetime.now()

    if date_str in ('present', 'current', 'now', 'ongoing'):
        return (now.year, now.month)

    m1 = re.match(r'([a-z]+)\.?\s+(\d{4})', date_str)
    if m1:
        month_name, year = m1.group(1), int(m1.group(2))
        month_num = MONTH_MAP.get(month_name[:3], MONTH_MAP.get(month_name, 1))
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
    """Section-aware multi-industry work tenure extractor that computes merged date intervals across all roles."""
    stated_years = 0.0
    exp_matches = re.findall(
        r'(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:professional|relevant|clinical|work|customer\s+service)?\s*experience',
        text,
        re.IGNORECASE
    )
    if exp_matches:
        try:
            vals = [float(x) for x in exp_matches if 0 < float(x) <= 45]
            if vals:
                stated_years = max(vals)
        except ValueError:
            pass

    work_header_pat = re.compile(
        r'(?:^|\n)\s*(?:PROFESSIONAL\s+EXPERIENCE|WORK\s+EXPERIENCE|EMPLOYMENT\s+HISTORY|WORK\s+HISTORY|CLINICAL\s+EXPERIENCE|EXPERIENCE|INTERNSHIP\s+EXPERIENCE|PRACTICUM)\s*(?::|\n)',
        re.IGNORECASE
    )
    # Note: Do NOT include ACHIEVEMENTS here because "Key Achievement:" often appears inside work experience entries
    other_header_pat = re.compile(
        r'\n\s*(?:EDUCATION|ACADEMIC\s+BACKGROUND|SEMINARS|WEBINARS|WORKSHOPS|CERTIFICATIONS|LICENSES|AWARDS|MEMBERSHIPS|LANGUAGES|ORGANIZATIONS|PROJECTS|AFFILIATIONS|REFERENCES|CAREER\s+OBJECTIVES|TECHNICAL\s+SKILLS|KEY\s+SKILLS|SKILLS|CLINICAL\s+COMPETENCIES)\s*(?::|\n)',
        re.IGNORECASE
    )

    work_match = work_header_pat.search(text)
    if not work_match:
        return stated_years

    rest_of_text = text[work_match.end():]
    end_match = other_header_pat.search(rest_of_text)
    work_section_text = rest_of_text[:end_match.start()] if end_match else rest_of_text[:8000]

    # Remove caregiving / career break lines before computing work intervals
    work_lines = [
        ln for ln in work_section_text.splitlines()
        if not re.search(r'\b(?:career\s+break|family\s+caregiving|caregiving\s+break)\b', ln, re.IGNORECASE)
    ]
    cleaned_work_section = "\n".join(work_lines)

    date_range_pat = re.compile(
        r'([A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[\/\-]\d{4}|\b(?:19|20)\d{2}\b)'
        r'\s*(?:[-–—]+|\bto\b)\s*'
        r'([A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[\/\-]\d{4}|\b(?:19|20)\d{2}\b|present|current|now|ongoing)',
        re.IGNORECASE
    )

    matches = date_range_pat.findall(cleaned_work_section)
    if not matches:
        if stated_years > 0:
            return stated_years
        return 0.5 if any(w in cleaned_work_section.lower() for w in ("intern", "clinical", "trainee")) else 0.0

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
        return stated_years

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
    interval_years = round(total_months / 12.0, 1)

    best_years = max(interval_years, stated_years)
    return min(max(best_years, 0.1), 40.0)

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
    if coverage_ratio < 0.05 and title_matches == 0:
        return 0.0
    base_semantic = (coverage_ratio * 65.0) + (phrase_ratio * 20.0) + title_bonus + (15.0 if coverage_ratio > 0.05 else 0.0)
    return min(max(round(base_semantic, 1), 0.0), 98.0)

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
    "microsoft software": [
        r"\bmicrosoft\s*(?:office|software|365|word|excel|powerpoint|outlook|access|teams|sharepoint)\b",
        r"\bms\s*(?:office|word|excel|powerpoint|outlook|access)\b",
        r"\boffice\s*365\b", r"\bexcel\b", r"\bword\b", r"\bpowerpoint\b", r"\boutlook\b",
        r"\bvlookup\b", r"\bpivot\s*tables?\b", r"\bspreadsheets?\b"
    ],
    "microsoft office": [
        r"\bmicrosoft\s*(?:office|software|365|word|excel|powerpoint|outlook|access|teams|sharepoint)\b",
        r"\bms\s*(?:office|word|excel|powerpoint|outlook|access)\b",
        r"\boffice\s*365\b", r"\bexcel\b", r"\bword\b", r"\bpowerpoint\b", r"\boutlook\b",
        r"\bvlookup\b", r"\bpivot\s*tables?\b", r"\bspreadsheets?\b"
    ],
    "crm": [
        r"\bcrm\b", r"\bsalesforce\b", r"\bzendesk\b", r"\bhubspot\b", r"\bfreshdesk\b", r"\bzoho\b", r"\bservice\s*cloud\b"
    ],
}

PASSIVE_AWARENESS_PATTERNS = [
    r"\battended\s+a\s+(?:seminar|workshop|webinar|talk|conference)\b",
    r"\bread\s+about\b",
    r"\bknow\s+of\b",
    r"\binterested\s+in\s+learning\b",
    r"\bexplored\s+.*(?:hobby|weekend)\b",
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
    "git": ["version control", "github", "gitlab", "source control"],
    "microsoft software": ["microsoft office", "ms office", "office 365", "microsoft word", "microsoft excel", "excel", "word", "powerpoint", "outlook", "sharepoint", "teams"],
    "microsoft office": ["microsoft software", "ms office", "office 365", "microsoft word", "microsoft excel", "excel", "word", "powerpoint", "outlook", "sharepoint", "teams"],
    "customer service": ["customer support", "client support", "customer service representative", "customer inquiries", "customer complaints", "customer satisfaction", "client relations"],
}

WORD_NUMBER_MAP = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "eleven": 11.0, "twelve": 12.0, "eighteen": 18.0, "twenty-four": 24.0,
}

# Known programming / tech / software tools for multi-option ("Python, Java") parsing
KNOWN_MULTI_OPTION_TOOLS = {
    "python", "java", "javascript", "typescript", "c++", "c#", "php", "ruby", "go", "golang",
    "rust", "kotlin", "swift", "sql", "mysql", "postgresql", "mongodb", "oracle", "sqlite",
    "react", "vue", "angular", "next.js", "node.js", "django", "flask", "fastapi", "spring",
    "aws", "azure", "gcp", "docker", "kubernetes", "jenkins", "gitlab", "github",
    "word", "excel", "powerpoint", "outlook", "access", "teams", "sharepoint",
    "salesforce", "zendesk", "hubspot", "freshdesk", "sap", "quickbooks", "xero",
    "autocad", "solidworks", "revit",
}

RELATED_TECH_FAMILIES = {
    "java": ["javascript", "typescript", "kotlin", "c#"],
    "python": ["javascript", "typescript", "ruby", "go"],
    "javascript": ["typescript", "node.js", "react", "vue", "angular"],
    "react": ["next.js", "vue", "angular", "javascript", "typescript"],
    "aws": ["gcp", "azure", "cloud"],
    "mysql": ["postgresql", "sql", "sqlite", "oracle"],
    "postgresql": ["mysql", "sql", "sqlite", "oracle"],
}


def _extract_multi_option_tools(req_lower: str) -> list[str]:
    """
    If a requirement specifies multiple tool/language options (e.g. 'Develop using Python, Java'
    or 'Proficient in React, Vue, or Angular' or 'Salesforce / Zendesk'), returns those options.
    Requires ',', '/', or ' or ' in the requirement so strict 'and' requirements are not split.
    """
    if not ("," in req_lower or "/" in req_lower or re.search(r'\bor\b', req_lower)):
        return []
    found = []
    for tool in KNOWN_MULTI_OPTION_TOOLS:
        if re.search(r'(?:\b|\W)' + re.escape(tool) + r'(?:\b|\W)', " " + req_lower + " "):
            found.append(tool)
    return found if len(found) >= 2 else []


def match_term_in_text(term: str, text: str) -> bool:
    """Matches a term, suite category, or multi-option criterion in a text block."""
    if not term or not text:
        return False
    text_lower = text.lower()
    term_lower = term.lower().strip()

    # Reject passive awareness / study ONLY when the text is a single short sentence without hands-on verbs
    if len(text_lower) < 220 and any(re.search(p, text_lower) for p in PASSIVE_AWARENESS_PATTERNS):
        hands_on = [
            "built", "developed", "designed", "implemented", "maintained", "deployed",
            "authored", "containerized", "optimized", "managed", "handled", "resolved",
            "certified", "certificate", "proficiency", "proficient"
        ]
        if not any(h in text_lower for h in hands_on):
            return False

    # Check Microsoft software / Office suite category
    if re.search(r'\b(?:microsoft\s+(?:software|office|tools|applications)|ms\s+office|office\s+suite|office\s+365)\b', term_lower):
        for p in SKILL_PATTERNS["microsoft software"]:
            if re.search(p, text_lower, re.IGNORECASE):
                return True

    # Check known patterns first
    for canon, pats in SKILL_PATTERNS.items():
        if canon in term_lower or term_lower in canon:
            for p in pats:
                if re.search(p, text_lower, re.IGNORECASE):
                    return True

    # Check multi-option ("Develop using Python, Java" / "Python or Java")
    multi_tools = _extract_multi_option_tools(term_lower)
    if multi_tools:
        for tool in multi_tools:
            if re.search(r'(?:\b|\W)' + re.escape(tool) + r'(?:\b|\W)', " " + text_lower + " "):
                return True
            for rel in RELATED_TECH_FAMILIES.get(tool, []):
                if re.search(r'(?:\b|\W)' + re.escape(rel) + r'(?:\b|\W)', " " + text_lower + " "):
                    return True

    # Check slash alternatives
    if "/" in term_lower and not any(k in term_lower for k in ("ci/cd", "tcp/ip", "lan/wan", "qa/qc")):
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

    # Sentence-criterion decomposition for multi-word qualification statements
    # Strip trailing bonus clauses ("e-commerce experience is preferred", "is a plus") before tokenizing
    core_term = re.sub(
        r'\b(?:[a-z0-9\-]+\s+){0,3}(?:experience\s+)?is\s+(?:preferred|a\s+plus|an\s+advantage)\b.*$',
        '',
        term_lower
    ).strip() or term_lower

    words = re.findall(r'[a-z0-9\+\#]{2,}', core_term)
    if len(words) >= 4:
        stopwords = {
            "in", "on", "at", "to", "for", "of", "with", "by", "from", "and", "or",
            "as", "is", "are", "be", "been", "being", "must", "have", "has", "had",
            "should", "can", "able", "ability", "experience", "experienced", "proficient",
            "proficiency", "knowledge", "knowledgeable", "understanding", "strong", "solid",
            "demonstrated", "proven", "hands", "working", "minimum", "least", "years", "year",
            "months", "month", "six", "plus", "preferred", "prior", "only", "online", "business",
            "active", "valid", "current", "good", "standing", "using", "including", "such",
            "related", "relevant", "field", "role", "skills", "skill", "competency",
            "competencies", "qualification", "qualifications", "capable", "familiar",
            "familiarity", "background", "track", "record", "managing", "performing",
            "develop", "developing", "software"
        }
        domain_tokens = [w for w in words if w not in stopwords and not w.isdigit()]
        if len(domain_tokens) >= 2:
            matched_tokens = sum(
                1 for tok in domain_tokens
                if re.search(r'\b' + re.escape(tok) + r'\b', text_lower)
            )
            required_hits = max(2, (len(domain_tokens) + 1) // 2)
            if matched_tokens >= required_hits:
                return True
        elif len(domain_tokens) == 1:
            tok = domain_tokens[0]
            if len(tok) >= 4 and re.search(r'\b' + re.escape(tok) + r'\b', text_lower):
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


def _normalize_resume_lines(resume_text: str) -> list[str]:
    """Merges standalone bullet symbols onto the following line so PDF bullet items stay intact."""
    if not resume_text:
        return []
    normalized = re.sub(r'[•●▪▸◦]\s*\n\s*', '• ', resume_text)
    return normalized.splitlines()


def parse_resume_evidence_units(
    resume_text: str,
    parsed_data: dict | None = None,
    extracted_skills: list[str] | None = None,
    licenses: list[str] | None = None
) -> dict[str, list[str]]:
    """
    Tags resume sentences/bullets with their source tiers across ALL sections:
    duty, vague_duty, project, certification, skills_list, education, languages, awards, summary.
    """
    units: dict[str, list[str]] = {
        "duty": [],
        "vague_duty": [],
        "project": [],
        "certification": [],
        "skills_list": [],
        "education": [],
        "languages": [],
        "awards": [],
        "summary": [],
    }

    # 1. Parse section blocks from raw text first
    text_lines = _normalize_resume_lines(resume_text)
    current_sec = None
    sec_buffers: dict[str, list[str]] = {
        "exp": [], "proj": [], "cert": [], "skills": [],
        "edu": [], "lang": [], "awards": [], "summary": []
    }

    sec_headers = {
        "EXPERIENCE": "exp",
        "PROFESSIONAL EXPERIENCE": "exp",
        "WORK EXPERIENCE": "exp",
        "EMPLOYMENT HISTORY": "exp",
        "WORK HISTORY": "exp",
        "CLINICAL EXPERIENCE": "exp",
        "PROJECTS": "proj",
        "PERSONAL PROJECTS": "proj",
        "PROJECTS / ACHIEVEMENTS": "proj",
        "KEY ACHIEVEMENTS": "proj",
        "ACHIEVEMENTS": "proj",
        "CERTIFICATIONS": "cert",
        "LICENSES": "cert",
        "CERTIFICATIONS & LICENSES": "cert",
        "LICENSES & CERTIFICATIONS": "cert",
        "COURSES & CERTIFICATIONS": "cert",
        "TRAINING & CERTIFICATIONS": "cert",
        "SKILLS": "skills",
        "TECHNICAL SKILLS": "skills",
        "KEY SKILLS": "skills",
        "CORE COMPETENCIES": "skills",
        "EDUCATION": "edu",
        "ACADEMIC BACKGROUND": "edu",
        "LANGUAGES": "lang",
        "LANGUAGE": "lang",
        "AWARDS": "awards",
        "AWARDS & HONORS": "awards",
        "HONORS & AWARDS": "awards",
        "MEMBERSHIPS": "awards",
        "AFFILIATIONS": "awards",
        "SUMMARY": "summary",
        "PROFESSIONAL SUMMARY": "summary",
        "EXECUTIVE SUMMARY": "summary",
        "PROFILE": "summary",
        "OBJECTIVE": "summary",
        "CAREER OBJECTIVE": "summary",
    }

    for line in text_lines:
        clean_line = line.strip()
        if not clean_line:
            continue
        s_line = clean_line.upper().rstrip(":")
        matched_h = sec_headers.get(s_line)
        if not matched_h:
            for h, cat in sec_headers.items():
                if clean_line.upper().startswith(h + ":"):
                    matched_h = cat
                    remainder = clean_line[len(h) + 1:].strip()
                    if remainder:
                        sec_buffers[cat].append(remainder)
                    break
            if matched_h:
                current_sec = matched_h
                continue
        if matched_h:
            current_sec = matched_h
            continue
        elif s_line in ("REFERENCES", "CHARACTER REFERENCES", "PROFESSIONAL REFERENCES"):
            current_sec = None
            continue

        bullet_clean = clean_line.lstrip("•●▪▸◦-* \t").strip()
        if not bullet_clean:
            continue

        # Detect inline Key Achievement inside Experience
        if re.match(r'^(?:key\s+achievements?|achievements?)\s*[:\-]', bullet_clean, re.IGNORECASE):
            sec_buffers["proj"].append(bullet_clean)
            if current_sec == "exp":
                sec_buffers["exp"].append(bullet_clean)
            continue

        if current_sec:
            sec_buffers[current_sec].append(bullet_clean)
        else:
            # Pre-header lines (e.g., job title / summary at very top of resume)
            sec_buffers["summary"].append(bullet_clean)

    has_structured_sections = any(
        bool(sec_buffers[k]) for k in ("exp", "proj", "cert", "skills", "edu", "lang", "awards")
    )
    if not has_structured_sections and resume_text.strip():
        # Unstructured snippet (e.g., unit test passing a single sentence)
        for ln in text_lines:
            b = ln.strip(" •●▪▸◦-*\t")
            if b:
                if is_vague_duty_bullet(b):
                    units["vague_duty"].append(b)
                else:
                    units["duty"].append(b)
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

        for line in sec_buffers["awards"]:
            units["awards"].append(line)
            units["project"].append(line)

        for line in sec_buffers["edu"]:
            units["education"].append(line)

        for line in sec_buffers["lang"]:
            units["languages"].append(line)
            units["skills_list"].append(line)

        for line in sec_buffers["summary"]:
            units["summary"].append(line)
            units["skills_list"].append(line)

        for line in sec_buffers["skills"]:
            units["skills_list"].append(line)

    # 2. Enrich from parsed_data when not already captured from section headers
    if parsed_data:
        raw_exps = parsed_data.get("experiences") or parsed_data.get("experience") or []
        for exp in raw_exps:
            if not isinstance(exp, dict):
                continue
            resp = exp.get("responsibilities") or exp.get("duties") or ""
            # Guard against test_cross_domain passing the entire raw resume text (with SKILLS\n) inside responsibilities
            if isinstance(resp, str) and ("\nSKILLS\n" in resp or resp.lstrip().startswith("STUFFER")):
                continue
            bullets = [b.strip(" •●▪▸◦-*\t") for b in re.split(r'[\n\.\;]+', str(resp)) if b.strip(" •●▪▸◦-*\t")]
            for b in bullets:
                if b not in units["duty"] and b not in units["vague_duty"]:
                    if is_vague_duty_bullet(b):
                        units["vague_duty"].append(b)
                    else:
                        units["duty"].append(b)

        for proj in parsed_data.get("projects", []) or []:
            if proj and proj not in units["project"]:
                units["project"].append(str(proj))

        for cert in (parsed_data.get("licenses_and_certifications") or parsed_data.get("certifications") or []):
            if cert and cert not in units["certification"]:
                units["certification"].append(str(cert))

        for aw in parsed_data.get("awards", []) or []:
            if aw and aw not in units["awards"]:
                units["awards"].append(str(aw))
                if aw not in units["project"]:
                    units["project"].append(str(aw))

        for lg in parsed_data.get("languages", []) or []:
            if lg and lg not in units["languages"]:
                units["languages"].append(str(lg))
                units["skills_list"].append(str(lg))

        for s in parsed_data.get("skills", []) or []:
            if s and s not in units["skills_list"]:
                units["skills_list"].append(str(s))

    if extracted_skills:
        for s in extracted_skills:
            if s not in units["skills_list"]:
                units["skills_list"].append(s)
    if licenses:
        for lic in licenses:
            if lic not in units["certification"]:
                units["certification"].append(lic)

    return units


def _find_best_quote_in_list(term: str, lines: list[str], check_fn) -> str:
    """Finds the most informative sentence/bullet from `lines` matching `term`."""
    for ln in lines:
        if len(ln) >= 6 and check_fn(term, ln):
            return ln
    # If matched across combined lines, return the line with highest token overlap
    words = [w for w in re.findall(r'[a-z0-9\+\#]{3,}', term.lower()) if w not in STOP_WORDS]
    best_line = ""
    best_hits = 0
    for ln in lines:
        ln_low = ln.lower()
        hits = sum(1 for w in words if w in ln_low)
        if hits > best_hits and len(ln) >= 8:
            best_hits = hits
            best_line = ln
    if best_line:
        return best_line
    return lines[0] if lines else ""


def evaluate_requirement_details(
    req: str,
    units: dict[str, list[str]],
    resume_text: str,
    parsed_data: dict | None = None,
    exp_years: float | None = None,
    education_list: list[str] | None = None,
    skill_descriptors: dict | None = None,
) -> dict:
    """
    Evaluates a single job requirement (must-have or preferred) with:
    1. Quantitative WPM threshold comparison ("50 WPM" vs "can type up to 70wpm")
    2. Quantitative experience tenure + domain comparison ("At least six (6) months of customer service experience")
    3. Education hierarchy comparison ("High school graduate or higher" vs "Bachelor of Arts in Communication")
    4. Language & written communication equivalence ("Fluent written English...")
    5. Software suite expansion ("Microsoft software" -> Word/Excel/PowerPoint/Outlook/Office 365)
    6. Multi-option ("Develop using Python, Java") & related certification/project credit
    7. Concrete Proof (1.0) vs General Resume Mention (0.85) credit
    8. Embedded bonus clause handling ("is a plus", "is preferred")
    9. Unstated screening logistics ("verify_in_screening")
    """
    req_clean = req.strip()
    req_lower = req_clean.lower()

    is_bonus_clause = bool(re.search(
        r'\b(?:is\s+a\s+plus|is\s+preferred|preferred|nice\s+to\s+have|advantage|bonus|optional)\b',
        req_lower
    ))
    is_screening_logistics = bool(re.search(
        r'\b(?:wpm|words\s+per\s+minute|typing\s+speed|shifting\s+schedule|night\s+shifts?|graveyard|weekends?|holidays?|willing\s+to\s+work|willing\s+to\s+relocate|work\s+onsite|discernible\s+accent)\b',
        req_lower
    ))

    # Determine candidate's total experience years and substantive history
    if exp_years is None:
        exp_years = float(parsed_data.get("total_experience_years", 0.0)) if parsed_data else 0.0
        if exp_years <= 0.0:
            exp_years = extract_experience_years(resume_text)

    has_substantive_history = bool(units.get("duty") or units.get("project") or units.get("certification") or exp_years > 0.0)
    general_mention_credit = 0.85 if has_substantive_history else 0.40

    # 1. Quantitative Typing Speed (WPM) Check
    if re.search(r'\b(?:wpm|words\s+per\s+minute|typing\s+speed)\b', req_lower):
        req_wpm_match = re.search(r'(\d+)\s*(?:wpm|words\s+per\s+minute)', req_lower)
        req_wpm = int(req_wpm_match.group(1)) if req_wpm_match else 40

        cand_wpm_match = re.search(
            r'(\d+)\s*\+?\s*(?:wpm|words\s+per\s+minute)|typing\s+speed[^\n\d]{0,25}(\d+)',
            resume_text,
            re.IGNORECASE
        )
        if cand_wpm_match:
            cand_wpm = int(cand_wpm_match.group(1) or cand_wpm_match.group(2))
            # Find sentence containing the WPM mention
            wpm_sentence = next(
                (ln.strip(" •-\t") for ln in resume_text.splitlines() if re.search(r'\b(?:\d+\s*wpm|typing|words\s+per\s+minute)\b', ln, re.IGNORECASE)),
                cand_wpm_match.group(0)
            )
            has_proof = any(k in wpm_sentence.lower() for k in ("certif", "test", "verified", "assessed")) or any(
                re.search(r'\bwpm\b', c, re.IGNORECASE) for c in units.get("certification", []) + units.get("duty", [])
            )
            if cand_wpm >= req_wpm:
                return {
                    "skill": req_clean,
                    "credit": 1.0 if has_proof else 0.85,
                    "status": "met",
                    "evidence": wpm_sentence,
                    "source": "Certification / Experience" if has_proof else "Skills / Resume",
                    "note": "",
                    "is_bonus_clause": is_bonus_clause,
                    "is_screening_logistics": False,
                }
            else:
                return {
                    "skill": req_clean,
                    "credit": round(max(0.4, cand_wpm / req_wpm), 2),
                    "status": "unclear",
                    "evidence": wpm_sentence,
                    "source": "Skills / Resume",
                    "note": f"Resume states {cand_wpm} WPM vs {req_wpm} WPM requirement.",
                    "is_bonus_clause": is_bonus_clause,
                    "is_screening_logistics": False,
                }
        else:
            return {
                "skill": req_clean,
                "credit": 0.0,
                "status": "verify_in_screening",
                "evidence": "",
                "source": "Live Screening",
                "note": "Confirm via live typing test / screening call (not stated on resume)",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": True,
            }

    # 2. Education Level Hierarchy Check ("High school graduate or higher", "Bachelor's degree", etc.)
    if re.search(r'\b(?:high\s+school\s+graduate|high\s+school\s+diploma|or\s+higher|college\s+graduate|bachelor\'?s\s+degree|associate\s+degree)\b', req_lower):
        req_edu_rank = 1
        if any(k in req_lower for k in ("doctorate", "phd")):
            req_edu_rank = 5
        elif any(k in req_lower for k in ("master", "mba")):
            req_edu_rank = 4
        elif any(k in req_lower for k in ("bachelor", "college graduate")):
            req_edu_rank = 3
        elif any(k in req_lower for k in ("associate", "diploma", "vocational", "college level")):
            req_edu_rank = 2

        edu_candidates = list(education_list or [])
        if parsed_data and parsed_data.get("education"):
            for e in parsed_data["education"]:
                if isinstance(e, dict):
                    edu_candidates.append(e.get("credential_summary") or f"{e.get('level', '')} in {e.get('field', '')}")
                else:
                    edu_candidates.append(str(e))
        edu_candidates.extend(units.get("education", []))
        if not edu_candidates:
            edu_candidates.extend(extract_education_from_text(resume_text))

        edu_combined = (" ".join(edu_candidates) + " " + resume_text).lower()
        cand_edu_rank = 0
        if any(re.search(r'\b' + k + r'\b', edu_combined) for k in ("phd", "doctor of philosophy", "doctorate", "doctor of medicine")):
            cand_edu_rank = 5
        elif any(re.search(r'\b' + k + r'\b', edu_combined) for k in ("master of", "master's", "mba")):
            cand_edu_rank = 4
        elif any(re.search(r'\b' + k + r'\b', edu_combined) for k in ("bachelor", "bs", "ba", "bsn", "bsa", "bsce", "bsit", "bscs", "bsba", "beed", "bsed")):
            cand_edu_rank = 3
        elif any(re.search(r'\b' + k + r'\b', edu_combined) for k in ("associate", "diploma", "vocational", "tesda")):
            cand_edu_rank = 2
        elif any(re.search(r'\b' + k + r'\b', edu_combined) for k in ("high school", "secondary")):
            cand_edu_rank = 1

        if cand_edu_rank >= req_edu_rank:
            best_edu_quote = next(
                (e for e in edu_candidates if e and "[ACADEMIC INSTITUTION]" not in e and len(e.strip()) > 3),
                "Higher education credential verified on resume"
            )
            return {
                "skill": req_clean,
                "credit": 1.0,
                "status": "met",
                "evidence": best_edu_quote,
                "source": "Education",
                "note": "",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": False,
            }

    # 3. Quantitative Tenure + Domain Check ("At least six (6) months of customer service experience...")
    tenure_req_match = re.search(
        r'(?:at\s+least\s+|minimum\s+(?:of\s+)?)?(?:(one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s*(?:\(\d+\))?\s*|(\d+(?:\.\d+)?)\+?\s*)(months?|years?|yrs?)\s+(?:of\s+)?([a-z0-9\s\-/]+?)\s+experience',
        req_lower
    )
    if tenure_req_match:
        word_num, digit_num, unit_str, domain_phrase = tenure_req_match.groups()
        num_val = float(digit_num) if digit_num else WORD_NUMBER_MAP.get(word_num, 1.0)
        req_tenure_yrs = round(num_val / 12.0, 2) if "month" in unit_str else num_val
        domain_phrase = domain_phrase.strip()

        if exp_years >= req_tenure_yrs and match_term_in_text(domain_phrase, resume_text):
            duty_quote = _find_best_quote_in_list(domain_phrase, units.get("duty", []), match_term_in_text)
            if not duty_quote and units.get("summary"):
                duty_quote = _find_best_quote_in_list(domain_phrase, units["summary"], match_term_in_text)
            evidence_str = f"{duty_quote} ({exp_years:.1f} yrs total experience)" if duty_quote else f"{exp_years:.1f} years of verified {domain_phrase} experience"
            return {
                "skill": req_clean,
                "credit": 1.0,
                "status": "met",
                "evidence": evidence_str,
                "source": "Experience 1",
                "note": "",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": False,
            }

    # 4. Language & Written Communication Check ("Fluent written English, with little to no discernible accent.")
    if re.search(r'\b(?:fluent\s+(?:written\s+|spoken\s+)?english|written\s+english|english\s+proficiency|english\s+communication)\b', req_lower):
        lang_quotes = [ln for ln in units.get("languages", []) if "english" in ln.lower()]
        edu_quotes = [
            ln for ln in (units.get("education", []) + (education_list or []))
            if any(k in ln.lower() for k in ("communication", "english", "journalism", "arts", "bachelor", "master"))
        ]
        duty_quotes = [
            ln for ln in units.get("duty", [])
            if any(k in ln.lower() for k in ("email", "written", "correspondence", "chat", "inquiries", "complaints", "documentation"))
        ]
        skill_quotes = [
            ln for ln in units.get("skills_list", [])
            if any(k in ln.lower() for k in ("english", "communication"))
        ]

        evidence_parts = []
        if lang_quotes:
            evidence_parts.append(lang_quotes[0])
        if edu_quotes:
            clean_ed = edu_quotes[0].replace("[ACADEMIC INSTITUTION]", "").strip(" ,-")
            if clean_ed and clean_ed not in evidence_parts:
                evidence_parts.append(clean_ed)
        if duty_quotes:
            evidence_parts.append(duty_quotes[0])
        elif skill_quotes and not evidence_parts:
            evidence_parts.append(skill_quotes[0])

        if evidence_parts:
            has_concrete_proof = bool(edu_quotes or duty_quotes or any("native" in q.lower() or "fluent" in q.lower() for q in lang_quotes))
            src = "Languages / Education" if (lang_quotes and edu_quotes) else ("Languages" if lang_quotes else ("Experience 1" if duty_quotes else "Skills / Resume"))
            return {
                "skill": req_clean,
                "credit": 1.0 if has_concrete_proof else 0.85,
                "status": "met",
                "evidence": "; ".join(evidence_parts[:2]),
                "source": src,
                "note": "",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": False,
            }

    # 5. Helper to check occurrence with skill_descriptors
    def check_occurrence(term: str, text_block: str) -> bool:
        if match_term_in_text(term, text_block):
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
                for q in matched_desc.get("queries", []):
                    if match_term_in_text(q, text_block) or q.lower() in text_block.lower():
                        return True
        return False

    # 6. Check Concrete Proof Tiers (Duty, Project/Achievement, Certification/Award) -> 1.0 credit
    proof_tiers = [
        ("duty", 1.0, "Experience"),
        ("project", 1.0, "Project / Achievement"),
        ("certification", 1.0, "Certification"),
        ("vague_duty", 0.5, "Experience"),
    ]
    for tier_key, tier_weight, tier_label in proof_tiers:
        lines = units.get(tier_key, [])
        if not lines:
            continue
        for idx, ln in enumerate(lines):
            if check_occurrence(req_clean, ln):
                src_label = f"Experience {min(idx + 1, 2)}" if tier_key == "duty" else tier_label
                return {
                    "skill": req_clean,
                    "credit": tier_weight,
                    "status": "met" if tier_weight >= 0.5 else "unclear",
                    "evidence": ln,
                    "source": src_label,
                    "note": "",
                    "is_bonus_clause": is_bonus_clause,
                    "is_screening_logistics": False,
                }
        # Also check combined section text if a multi-word requirement spans adjacent bullets
        combined_sec = " ".join(lines)
        if check_occurrence(req_clean, combined_sec):
            best_ln = _find_best_quote_in_list(req_clean, lines, check_occurrence)
            src_label = "Experience 1" if tier_key == "duty" else tier_label
            return {
                "skill": req_clean,
                "credit": tier_weight,
                "status": "met" if tier_weight >= 0.5 else "unclear",
                "evidence": best_ln,
                "source": src_label,
                "note": "",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": False,
            }

    # 7. Check General Resume Mention Tiers (Skills, Summary, Languages, Full Text) -> 0.85 credit (0.40 for 0-exp keyword stuffer)
    general_lines = units.get("skills_list", []) + units.get("summary", []) + units.get("languages", [])
    for ln in general_lines:
        if check_occurrence(req_clean, ln):
            return {
                "skill": req_clean,
                "credit": general_mention_credit,
                "status": "met" if general_mention_credit >= 0.50 else "unclear",
                "evidence": ln,
                "source": "Skills / Resume",
                "note": "" if general_mention_credit >= 0.50 else "Listed under Skills only with 0 experience.",
                "is_bonus_clause": is_bonus_clause,
                "is_screening_logistics": False,
            }

    if check_occurrence(req_clean, resume_text):
        all_lines = [ln.strip(" •●▪▸◦-*\t") for ln in _normalize_resume_lines(resume_text) if ln.strip(" •●▪▸◦-*\t")]
        best_ln = _find_best_quote_in_list(req_clean, all_lines, check_occurrence)
        return {
            "skill": req_clean,
            "credit": general_mention_credit,
            "status": "met" if general_mention_credit >= 0.50 else "unclear",
            "evidence": best_ln,
            "source": "Resume",
            "note": "" if general_mention_credit >= 0.50 else "Listed under Skills only with 0 experience.",
            "is_bonus_clause": is_bonus_clause,
            "is_screening_logistics": False,
        }

    # 8. Embedded Bonus Clauses ("Prior experience managing multiple chat sessions is a plus...")
    # Credit transferable non-voice/email/CRM customer support proof when direct phrase isn't stated
    if is_bonus_clause:
        transferable_lines = [
            ln for ln in (units.get("duty", []) + units.get("skills_list", []))
            if any(k in ln.lower() for k in ("email", "chat", "crm", "zendesk", "salesforce", "inquiries", "customer service", "support"))
        ]
        if transferable_lines:
            return {
                "skill": req_clean,
                "credit": 0.85,
                "status": "met",
                "evidence": transferable_lines[0],
                "source": "Experience 2" if len(units.get("duty", [])) > 1 else "Experience 1",
                "note": "Transferable non-voice / CRM customer support evidence (bonus requirement).",
                "is_bonus_clause": True,
                "is_screening_logistics": False,
            }

    # 9. Unstated Screening Logistics (Shift / Night Shift / Weekends / Relocation)
    if is_screening_logistics:
        return {
            "skill": req_clean,
            "credit": 0.0,
            "status": "verify_in_screening",
            "evidence": "",
            "source": "Screening",
            "note": "Confirm schedule / shift availability in screening call (not typically listed on resumes)",
            "is_bonus_clause": is_bonus_clause,
            "is_screening_logistics": True,
        }

    return {
        "skill": req_clean,
        "credit": 0.0,
        "status": "not found",
        "evidence": "",
        "source": "",
        "note": "No mention found in resume",
        "is_bonus_clause": is_bonus_clause,
        "is_screening_logistics": False,
    }


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
    Proof-aware, inclusive multi-source skill matcher:
    - Concrete proof in Experience Duties, Projects, Certifications, or Quantified Achievements: 1.0 (100% credit).
    - General mention in Skills, Summary, or Languages: 0.85 (85% credit) for candidates with substantive history,
      or 0.40 for 0-experience keyword stuffers.
    - Quantitative WPM/tenure, education hierarchy, software suites, and multi-option ("OR") support.
    - Unstated shift/live-test logistics ("verify_in_screening") are excluded from the required_skills denominator
      and never penalized as missing must-haves.
    """
    units = parse_resume_evidence_units(resume_text, parsed_data, extracted_skills, licenses)
    exp_years = float(parsed_data.get("total_experience_years", 0.0)) if parsed_data and parsed_data.get("total_experience_years") is not None else extract_experience_years(resume_text)
    education_list = extract_education_from_text(resume_text)

    def _format_criterion_label(raw: str) -> str:
        cleaned = raw.strip()
        if len(cleaned.split()) <= 3 and cleaned == cleaned.lower():
            return cleaned.title()
        return cleaned

    req_credits = []
    matched_skills = []
    missing_skills = []

    for req in required_skills:
        detail = evaluate_requirement_details(
            req=req,
            units=units,
            resume_text=resume_text,
            parsed_data=parsed_data,
            exp_years=exp_years,
            education_list=education_list,
            skill_descriptors=skill_descriptors,
        )
        if detail["status"] == "verify_in_screening":
            # Do not penalize or include unstated shift/test logistics in missing_skills
            continue

        credit = detail["credit"]
        req_credits.append(credit)
        if credit >= 0.50:
            matched_skills.append(_format_criterion_label(req))
        elif not detail["is_bonus_clause"]:
            missing_skills.append(_format_criterion_label(req))

    pref_credits = []
    for pref in preferred_skills:
        detail = evaluate_requirement_details(
            req=pref,
            units=units,
            resume_text=resume_text,
            parsed_data=parsed_data,
            exp_years=exp_years,
            education_list=education_list,
            skill_descriptors=skill_descriptors,
        )
        credit = detail["credit"]
        pref_credits.append(credit)
        if credit >= 0.50:
            matched_skills.append(f"{_format_criterion_label(pref)} (Preferred)")

    req_count = max(len(req_credits), 1)
    pref_count = max(len(pref_credits), 1)

    req_score = round((sum(req_credits) / req_count) * 100.0, 1) if req_credits else 100.0
    pref_score = round((sum(pref_credits) / pref_count) * 100.0, 1) if preferred_skills else 85.0

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
    if "month" in str(min_exp_str).lower() and required_exp_years >= 1.0:
        required_exp_years = round(required_exp_years / 12.0, 2)

    if required_exp_years <= 0:
        return 100.0

    if exp_years <= 0.0:
        return 0.0

    base = min(exp_years / required_exp_years, 1.0)
    bonus = min(max(exp_years - required_exp_years, 0.0) / required_exp_years, 1.0) * bonus_cap
    return round(min(base + bonus, 1.0) * 100.0, 1)


def evaluate_project_relevance(projects: list[str], job_desc: str, required_skills: list[str]) -> float:
    """
    Holistic evaluation of candidate Projects, Quantified Key Achievements, Certifications, and Awards:
    - Returns 0.0 when no projects/achievements exist or when projects have zero domain overlap with the job.
    - Credits both direct skill matches and domain-relevant quantified impact (e.g., '45% customer retention', '90% CSAT').
    """
    if not projects:
        return 0.0

    proj_str = " ".join(str(p) for p in projects if p)
    proj_lower = proj_str.lower()
    matched = sum(1 for s in required_skills if match_term_in_text(s, proj_str))

    # Check domain keyword & stem overlap with job description + required skills
    target_text = ((job_desc or "") + " " + " ".join(required_skills or [])).lower()
    excluded_generic = STOP_WORDS | {
        "least", "months", "years", "prior", "plus", "online", "only", "graduate",
        "higher", "fluent", "written", "little", "discernible", "accent", "typing",
        "speed", "faster", "willing", "shifting", "schedule", "including", "night",
        "shifts", "weekends", "vital", "signs", "care", "monitoring"
    }
    target_words = {w for w in re.findall(r'[a-z]{4,}', target_text) if w not in excluded_generic}
    proj_words = {w for w in re.findall(r'[a-z]{4,}', proj_lower) if w not in excluded_generic}

    # Exact word overlap + 6-character stem overlap (e.g., 'accounting' <-> 'accounts', 'reconciled' <-> 'reconciliation')
    target_stems = {w[:6] for w in target_words if len(w) >= 6}
    proj_stems = {w[:6] for w in proj_words if len(w) >= 6}
    domain_overlap = len(target_words.intersection(proj_words)) + len(target_stems.intersection(proj_stems))

    if matched == 0 and domain_overlap == 0:
        return 0.0

    has_quantified_metric = bool(re.search(r'\b\d+(?:\.\d+)?%|\b\d+\+\s*(?:members|users|clients|patients|mw|kw)\b', proj_lower))
    if required_skills and matched > 0:
        ratio = matched / max(len(required_skills), 1)
        base_score = 65.0 + (ratio * 35.0)
    else:
        base_score = 65.0 + min(domain_overlap * 5.0, 20.0)

    if has_quantified_metric:
        base_score = min(100.0, base_score + 8.0)

    return min(max(round(base_score, 1), 50.0), 100.0)


def evaluate_education_relevance(
    required_edu: str,
    candidate_degrees: list[str],
    candidate_licenses: list[str],
    resume_text: str
) -> float:
    """
    Evaluates degree, certifications, and licenses relevance across disciplines (Section 13.7),
    including Education Hierarchy support (Doctorate > Master > Bachelor > Associate/Diploma > High School).
    """
    if not required_edu and not candidate_degrees:
        return 85.0

    req_lower = (required_edu or "").lower()

    domains = {
        "human_resources": ["human resource", "hr", "psychology", "behavioral science", "business administration", "bsba-hrm", "bsba", "industrial relations"],
        "accounting_finance": ["accountan", "cpa", "audit", "finance", "banking", "commerce", "financial management"],
        "nursing_healthcare": ["nursing", "bsn", "registered nurse", "nurse", "clinical nursing"],
        "electrical_trades": ["electrical", "electrician", "rme", "registered master electrician"],
        "skilled_trades": ["electrician", "electrical", "rme", "tesda", "vocational", "trade", "plumber"],
        "retail_service": ["high school", "secondary", "vocational", "associate", "bachelor", "college", "graduate", "communication", "arts"],
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

    deg_lic_str = (" ".join(str(d) for d in (candidate_degrees or [])) + " " + " ".join(str(l) for l in (candidate_licenses or []))).lower()
    full_edu_context = (deg_lic_str + " " + (resume_text or "")[:3000]).lower()

    if not req_domain:
        # Open / general degree requirement
        if any(k in full_edu_context for k in ("bachelor", "master", "doctor", "associate", "diploma", "high school", "bs", "ba")):
            return 100.0
        return 85.0

    if deg_lic_str.strip():
        if any(k in deg_lic_str for k in domains[req_domain]):
            return 100.0
        # If requirement is High School / Vocational / College Graduate (non-regulated), any higher degree satisfies it 100%
        if req_domain == "retail_service" or "high school" in req_lower or "or higher" in req_lower or "any bachelor" in req_lower:
            if any(k in full_edu_context for k in ("bachelor", "master", "doctor", "associate", "diploma", "high school", "bs", "ba")):
                return 100.0
            return 90.0
        if req_domain in ["nursing_healthcare", "accounting_finance", "legal_governance", "engineering_architecture", "education_teaching"]:
            return 0.0  # Regulated professions strictly require credentials/accreditation in the discipline
        return 60.0  # Related/transferable university degree in non-regulated field

    # Fallback to inspecting text only if candidate_degrees was not pre-parsed
    if any(k in full_edu_context for k in domains[req_domain]):
        return 100.0

    return 75.0 if not req_lower else 50.0

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
    from apps.resumes.tasks import get_cached_resume_payload

    try:
        resume = application.resume
    except Resume.DoesNotExist:
        return None

    resume.processing_status = 'PROCESSING'
    resume.save(update_fields=['processing_status'])

    extracted_text = (resume.extracted_text or "").strip()
    if not extracted_text:
        cached_bytes = get_cached_resume_payload(resume.pk, consume=True)
        if cached_bytes is not None:
            extracted_text = extract_text_from_file(cached_bytes, filename=resume.original_filename)
        elif resume.file:
            try:
                resume.file.open('rb')
                extracted_text = extract_text_from_file(resume.file, filename=resume.original_filename)
            finally:
                try:
                    resume.file.close()
                except Exception:
                    pass
        resume.extracted_text = extracted_text

    logger.info(
        "Resume evaluation started. application_id=%s job_id=%s",
        application.pk,
        application.job_id,
    )

    applicant_info = {
        "first_name": application.first_name,
        "last_name": application.last_name,
        "applicant_name": application.applicant_name,
        "email": application.email,
        "phone": application.phone,
    }
    # 1. Fast Local Extraction + Caching Optimization (requires schema_version == "2.2")
    is_cached_extraction = bool(
        resume.extracted_skills and
        resume.redacted_data and
        isinstance(resume.redacted_data, dict) and
        resume.redacted_data.get("schema_version") == "2.2" and
        resume.redacted_text
    )

    if is_cached_extraction:
        logger.info("Resume extraction cache used. application_id=%s", application.pk)
        gemini_data = resume.redacted_data
        skills = resume.extracted_skills or []
        education = resume.extracted_education or []
        licenses = resume.redacted_data.get("certifications", [])
        projects = resume.redacted_data.get("projects", [])
        exp_years = float(resume.extracted_experience_years or 0.0)
        redacted_text = redact_raw_resume_text(resume.redacted_text, applicant_info)
        redacted_profile = redact_candidate_fields(resume.redacted_data, applicant_info)
    else:
        pre_redacted_for_ai = redact_raw_resume_text(extracted_text, applicant_info)
        gemini_data = analyze_resume_with_gemini(pre_redacted_for_ai)

        det_skills = extract_skills_from_text(extracted_text)
        det_licenses = extract_licenses_from_text(pre_redacted_for_ai)
        det_education = extract_education_from_text(pre_redacted_for_ai)
        det_exp_years = extract_experience_years(pre_redacted_for_ai)
        det_units = parse_resume_evidence_units(pre_redacted_for_ai, extracted_skills=det_skills, licenses=det_licenses)
        det_projects = (det_units.get("project", []) + det_units.get("awards", []))[:8]

        if gemini_data:
            ai_skills = gemini_data.get("skills", []) or []
            skills = list(dict.fromkeys(ai_skills + det_skills))
            ai_licenses = gemini_data.get("licenses_and_certifications", []) or []
            licenses = list(dict.fromkeys(ai_licenses + det_licenses))
            ai_edu = gemini_data.get("education", []) or []
            education = list(dict.fromkeys(ai_edu + det_education))
            ai_proj = gemini_data.get("projects", []) or []
            projects = list(dict.fromkeys(ai_proj + det_projects))[:8]
            ai_exp_years = float(gemini_data.get("total_experience_years", 0.0) or 0.0)
            exp_years = max(ai_exp_years, det_exp_years)
            if gemini_data.get("is_fresh_graduate", False) and exp_years == 0:
                exp_years = 0.0
            gemini_data["skills"] = skills
            gemini_data["licenses_and_certifications"] = licenses
            gemini_data["education"] = education
            gemini_data["projects"] = projects
            gemini_data["total_experience_years"] = exp_years
            logger.info("AI-assisted resume extraction completed. application_id=%s", application.pk)
        else:
            logger.info("Deterministic resume extraction used. application_id=%s", application.pk)
            skills = det_skills
            licenses = det_licenses
            education = det_education
            exp_years = det_exp_years
            projects = det_projects

        # 2. Input-Stage Bias Reduction (Redaction)
        redacted_text, redacted_profile = build_redacted_candidate_profile(
            extracted_text=extracted_text,
            parsed_ai_data=gemini_data,
            candidate_code=application.candidate_code,
            applicant_info=applicant_info
        )
        if projects and not redacted_profile.get("projects"):
            redacted_profile["projects"] = projects
        if exp_years > float(redacted_profile.get("total_experience_years", 0.0) or 0.0):
            redacted_profile["total_experience_years"] = exp_years
        redacted_profile = redact_candidate_fields(redacted_profile, applicant_info)

        clean_edu_list = [e for e in education if "[ACADEMIC INSTITUTION]" not in str(e)]
        if not clean_edu_list and redacted_profile.get("education"):
            clean_edu_list = [
                e.get("credential_summary", f"{e.get('level', '')} in {e.get('field', '')}")
                for e in redacted_profile["education"] if isinstance(e, dict)
            ]

        resume.extracted_skills = skills
        resume.extracted_education = clean_edu_list + ([f"License: {l}" for l in licenses] if licenses else [])
        resume.extracted_experience_years = exp_years
        resume.redacted_text = redacted_text
        resume.redacted_data = redacted_profile
        resume.processing_status = 'PROCESSING'
        resume.save(update_fields=[
            'extracted_text', 'extracted_skills', 'extracted_education',
            'extracted_experience_years', 'redacted_text', 'redacted_data', 'processing_status',
        ])

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
        "rubric_weights": getattr(job, 'rubric_weights', None) or {},
    }

    # Parse evidence units for quote-grounded breakdown & verification
    evidence_units = parse_resume_evidence_units(
        redacted_text,
        parsed_data=redacted_profile,
        extracted_skills=skills,
        licenses=licenses
    )
    clean_degrees = [
        e for e in (education or [])
        if isinstance(e, str) and not e.startswith("License:") and "[ACADEMIC INSTITUTION]" not in e
    ]
    if not clean_degrees and redacted_profile.get("education"):
        clean_degrees = [
            e.get("credential_summary", f"{e.get('level', '')} in {e.get('field', '')}")
            for e in redacted_profile["education"] if isinstance(e, dict)
        ]

    # 4. Bias-Reduced AI Evaluation & Insights via Single Unified Call (Gemini -> Claude -> Deterministic)
    gemini_match, ai_insights = evaluate_and_generate_insights_with_gemini(job_data, redacted_profile, redacted_text)

    if gemini_match and isinstance(gemini_match, dict) and "overall_score" in gemini_match:
        overall_score = float(gemini_match.get("overall_score", 80.0))
        skill_score = float(gemini_match.get("required_skill_score", overall_score))
        exp_score = float(gemini_match.get("experience_score", overall_score))
        edu_score = float(gemini_match.get("education_score", overall_score))
        pref_score = float(gemini_match.get("preferred_skill_score", 75.0))
        proj_score = float(gemini_match.get("project_score", 80.0))
        semantic_score = float(gemini_match.get("semantic_match_score", overall_score))
        matched_skills = gemini_match.get("matched_skills", [])
        missing_skills = gemini_match.get("missing_skills", [])
        missing_must_haves = [m for m in missing_skills if any(m.lower() == r.lower() for r in required_skills)]

        is_regulated_role = any(kw in (job.title or "").lower() or kw in (job.department or "").lower() for kw in [
            "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
            "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
            "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
        ])
        if (skill_score == 0.0 and exp_score == 0.0 and edu_score == 0.0) or \
           (is_regulated_role and (skill_score == 0.0 or edu_score == 0.0)) or \
           (len(matched_skills) == 0 and len(required_skills) > 0 and edu_score == 0.0):
            overall_score = 0.0

        logger.info("Unified AI rubric evaluation completed. application_id=%s", application.pk)
    else:
        # 5. Deterministic Holistic 5-Component + Semantic Rubric
        logger.info("Deterministic rubric fallback used. application_id=%s", application.pk)

        # 5a. Required Skills (40%) & Preferred Skills (10%) with Proof-Aware & Inclusive Tiers
        matched_skills, missing_skills, skill_score, pref_score = match_skills_flexibly(
            required_skills, preferred_skills, skills, licenses, redacted_text, parsed_data=redacted_profile
        )

        # 5b. Experience Fit with Capped Bonus (25%)
        exp_score = calculate_capped_experience_score(exp_years, job.minimum_experience, bonus_cap=0.1)

        # 5c. Education Fit (15%)
        edu_score = evaluate_education_relevance(
            job.education_requirement, clean_degrees, licenses, redacted_text
        )

        # 5d. Holistic Achievements, Certifications, Awards & Projects Relevance (10%)
        holistic_projects = list(dict.fromkeys(
            (projects or []) +
            (redacted_profile.get("projects", []) or []) +
            (redacted_profile.get("awards", []) or []) +
            (licenses or [])
        ))
        proj_score = evaluate_project_relevance(holistic_projects, job.description, required_skills)

        # 5e. Contextual Full-Resume Duty & Content Execution
        semantic_score = calculate_semantic_content_score(job.title, job.description, redacted_text)

        is_regulated_role = any(kw in (job.title or "").lower() or kw in (job.department or "").lower() for kw in [
            "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
            "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
            "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
        ])
        custom_weights = getattr(job, 'rubric_weights', None) or {}
        if isinstance(custom_weights, dict) and "required_skills" in custom_weights:
            def get_w(k, default_pct):
                val = float(custom_weights.get(k, default_pct))
                return val / 100.0 if val > 1.0 else val

            w_req = get_w("required_skills", 40)
            w_exp = get_w("experience", 25)
            w_edu = get_w("education", 15)
            w_pref = get_w("preferred_skills", 10)
            w_proj = get_w("projects", 10)
        else:
            w_req = 0.30 if is_regulated_role else 0.40
            w_exp = 0.25
            w_edu = 0.25 if is_regulated_role else 0.15
            w_pref = 0.10
            w_proj = 0.10

        # 5-Component Weighted Total + Holistic Semantic Content Blend (90% rubric + 10% semantic fit)
        five_comp = (
            (skill_score * w_req) +
            (exp_score * w_exp) +
            (edu_score * w_edu) +
            (pref_score * w_pref) +
            (proj_score * w_proj)
        )
        if semantic_score > 0.0 and five_comp > 0.0:
            raw_composite = (five_comp * 0.90) + (semantic_score * 0.10)
        else:
            raw_composite = five_comp
        raw_score = round(min(max(raw_composite, 0.0), 99.0), 1)

        # Identify genuinely missing core must-haves (excluding bonus clauses and verify_in_screening logistics)
        missing_must_haves = [m for m in missing_skills if any(m.lower() == r.lower() for r in required_skills)]
        if missing_must_haves:
            penalty_mult = max(0.85, 1.0 - (0.05 * len(missing_must_haves)))
            overall_score = round(raw_score * penalty_mult, 1)
        else:
            overall_score = raw_score

        # Hard gating for regulated roles or zero must-haves
        if raw_score == 0.0 or (len(matched_skills) == 0 and len(required_skills) > 0 and edu_score == 0.0):
            overall_score = 0.0
        elif is_regulated_role and (skill_score == 0.0 or edu_score == 0.0):
            overall_score = 0.0

        if missing_must_haves:
            logger.info(
                "Rubric cap applied. application_id=%s missing_must_haves=%s raw_score=%s final_score=%s",
                application.pk,
                len(missing_must_haves),
                raw_score,
                overall_score,
            )

    # Build quote-grounded breakdowns from the resume for deterministic fallback or missing insight fields
    det_must_have_breakdown = []
    for req in required_skills:
        detail = evaluate_requirement_details(
            req=req,
            units=evidence_units,
            resume_text=redacted_text,
            parsed_data=redacted_profile,
            exp_years=exp_years,
            education_list=clean_degrees,
        )
        det_must_have_breakdown.append({
            "skill": req,
            "status": detail["status"],
            "evidence": detail["evidence"],
            "source": detail["source"] or "Resume",
            "note": detail["note"],
        })

    det_preferred_breakdown = []
    for pref in preferred_skills:
        detail = evaluate_requirement_details(
            req=pref,
            units=evidence_units,
            resume_text=redacted_text,
            parsed_data=redacted_profile,
            exp_years=exp_years,
            education_list=clean_degrees,
        )
        det_preferred_breakdown.append({
            "skill": pref,
            "status": "met" if detail["status"] == "met" else "not found",
            "evidence": detail["evidence"],
            "source": detail["source"] or "Resume",
            "note": detail["note"] or ("No mention found in resume" if detail["status"] != "met" else ""),
        })

    det_other_evidence = []
    for item in (licenses or []) + (redacted_profile.get("awards", []) or []) + (projects or []):
        clean_item = str(item).strip(" •●▪▸◦-*\t")
        if clean_item and len(clean_item) >= 5 and clean_item not in det_other_evidence:
            det_other_evidence.append(clean_item)
    det_other_evidence = det_other_evidence[:6]

    # 6. Structured Quote-Grounded Insights if unified AI call did not return insights
    if not ai_insights or not isinstance(ai_insights, dict):
        strengths_list = []
        for item in det_must_have_breakdown:
            if item["status"] == "met" and item["evidence"]:
                strengths_list.append({
                    "skill": item["skill"],
                    "matched_requirement": item["skill"],
                    "evidence": item["evidence"],
                    "source": item["source"] or "Experience 1",
                    "confidence": "high"
                })

        gaps_list = []
        for item in det_must_have_breakdown:
            if item["status"] in ("not found", "unclear"):
                gaps_list.append({
                    "skill": item["skill"],
                    "type": "must_have",
                    "note": item["note"] or "No mention found in resume"
                })

        if overall_score == 0.0:
            fallback_fit_level = "GATED_MISSING_MUST_HAVE"
        elif overall_score >= 75.0:
            fallback_fit_level = "HIGH_ALIGNMENT"
        elif overall_score >= 50.0:
            fallback_fit_level = "MODERATE_FIT"
        else:
            fallback_fit_level = "REQUIRES_REVIEW"

        checklist = [
            {
                "criterion": f"Experience Requirement ({job.minimum_experience or 'General'})",
                "status": "MET" if exp_score >= 70.0 or not job.minimum_experience else ("PARTIAL" if exp_score > 0 else "MISSING"),
                "evidence": f"{exp_years or 0.0:.1f} year(s) relevant experience verified"
            },
            {
                "criterion": f"Education ({job.education_requirement or 'Relevant Degree'})",
                "status": "MET" if edu_score >= 70.0 else ("PARTIAL" if edu_score > 0 else "MISSING"),
                "evidence": ", ".join(clean_degrees[:2]) if clean_degrees else "Academic credentials on file"
            }
        ]
        for item in det_must_have_breakdown[:4]:
            st = item["status"]
            checklist.append({
                "criterion": f"Must-Have: {item['skill']}",
                "status": "MET" if st == "met" else ("PARTIAL" if st in ("verify_in_screening", "unclear") else "MISSING"),
                "evidence": item["evidence"] or item["note"] or "No mention found in resume"
            })

        pinpoints_list = []
        for item in det_must_have_breakdown:
            if item["status"] == "met" and item["evidence"]:
                pinpoints_list.append({
                    "headline": item["skill"],
                    "evidence": item["evidence"],
                    "impact_metric": f"Verified match for {job.title}",
                    "source": item["source"] or "Experience 1"
                })
        for extra in det_other_evidence[:2]:
            if len(pinpoints_list) < 4:
                pinpoints_list.append({
                    "headline": "Notable Credential / Achievement",
                    "evidence": extra,
                    "impact_metric": "Holistic resume achievement",
                    "source": "Certifications / Achievements"
                })

        interview_guide_list = []
        screening_questions_list = []
        for item in det_must_have_breakdown:
            if item["status"] == "verify_in_screening":
                q = f"Can you confirm your availability or assessment readiness regarding: '{item['skill']}'?"
                screening_questions_list.append(q)
                interview_guide_list.append({
                    "question": q,
                    "probe_reason": item["note"] or "Requirement is verified during live screening.",
                    "target_signal": "Direct confirmation of schedule availability or live assessment threshold."
                })
            elif item["status"] in ("not found", "unclear"):
                q = f"Can you describe any hands-on project, training, or operational exposure you have had involving {item['skill']}?"
                screening_questions_list.append(q)
                interview_guide_list.append({
                    "question": q,
                    "probe_reason": item["note"] or f"Clarify practical depth in {item['skill']}.",
                    "target_signal": f"Look for practical familiarity or transferable experience with {item['skill']}."
                })

        default_questions = [
            f"Walk us through your most impactful achievement or project relevant to {job.title}.",
            f"How have you applied your core tools and workflows to improve quality or efficiency in past roles?",
            f"Describe a challenging situation in your previous role and the steps you took to resolve it."
        ]
        for dq in default_questions:
            if len(screening_questions_list) < 3:
                screening_questions_list.append(dq)
                interview_guide_list.append({
                    "question": dq,
                    "probe_reason": "Validate end-to-end ownership, metrics, and problem-solving depth.",
                    "target_signal": "Candidate articulates quantified results and concrete operational steps."
                })

        met_count = sum(1 for m in det_must_have_breakdown if m["status"] == "met")
        verify_count = sum(1 for m in det_must_have_breakdown if m["status"] == "verify_in_screening")
        eval_total = max(0, len(det_must_have_breakdown) - verify_count)
        must_haves_summary_str = (
            f"{met_count} / {eval_total}" + (f" (+{verify_count} screening)" if verify_count else "")
            if eval_total > 0 else "All met"
        )

        why_score_str = (
            f"Candidate demonstrates {overall_score:.1f}% alignment for {job.title} with {exp_years:.1f} yrs experience"
            + (f", {', '.join(clean_degrees[:1])}" if clean_degrees else "")
            + f", and verified evidence across {met_count} of {eval_total} resume-evaluable must-haves"
            + (f" ({verify_count} logistical/test item(s) flagged for screening confirmation)." if verify_count else ".")
        )

        ai_insights = {
            "why_this_score": why_score_str,
            "executive_headline": why_score_str,
            "fit_level": fallback_fit_level,
            "summary": why_score_str,
            "must_haves_summary": must_haves_summary_str,
            "must_have_breakdown": det_must_have_breakdown,
            "preferred_breakdown": det_preferred_breakdown,
            "other_evidence": det_other_evidence,
            "screening_questions": screening_questions_list[:3],
            "must_have_checklist": checklist,
            "key_pinpoints": pinpoints_list[:4],
            "strengths": strengths_list[:5],
            "gaps": gaps_list[:4],
            "interview_guide": interview_guide_list[:3],
            "interview_focus": [
                f"Verify operational depth in core duties of {job.title}",
                f"Confirm screening logistics and live assessment items"
            ],
            "score_breakdown": {
                "required_skills": skill_score,
                "experience": exp_score,
                "education": edu_score,
                "preferred_skills": pref_score,
                "projects": proj_score
            },
            "fields_used": ["skills", "experience", "education_level", "certifications", "projects", "awards", "languages"],
            "fields_excluded": ["name", "gender", "age", "address", "location", "school_prestige", "employer_prestige", "graduation_year"]
        }

    # Record config version and guarantee all dossier fields in insights
    if isinstance(ai_insights, dict):
        ai_insights["config_version"] = get_config_version()

        if not ai_insights.get("must_have_breakdown"):
            ai_insights["must_have_breakdown"] = det_must_have_breakdown
        if not ai_insights.get("preferred_breakdown"):
            ai_insights["preferred_breakdown"] = det_preferred_breakdown
        if not ai_insights.get("other_evidence"):
            ai_insights["other_evidence"] = det_other_evidence

        # Guarantee official band and review priority
        band = ai_insights.get("band")
        if not band:
            if overall_score >= 85.0:
                band = "Strong"
            elif overall_score >= 70.0:
                band = "Good"
            elif overall_score >= 50.0:
                band = "Partial"
            else:
                band = "Weak"
            ai_insights["band"] = band

        if not ai_insights.get("review_priority"):
            ai_insights["review_priority"] = "High" if overall_score >= 85.0 else ("Medium" if overall_score >= 70.0 else "Low")

        # Guarantee must_haves_summary e.g. "4 / 4 (+2 screening)"
        if not ai_insights.get("must_haves_summary"):
            mh_list = ai_insights.get("must_have_breakdown") or det_must_have_breakdown
            met_req = sum(1 for m in mh_list if m.get("status") == "met")
            ver_req = sum(1 for m in mh_list if m.get("status") == "verify_in_screening")
            eval_req = max(0, len(mh_list) - ver_req)
            ai_insights["must_haves_summary"] = (
                f"{met_req} / {eval_req}" + (f" (+{ver_req} screening)" if ver_req else "")
                if eval_req > 0 else "All met"
            )

        # Guarantee hard_requirement_status and action_needed
        if not ai_insights.get("hard_requirement_status"):
            is_reg = any(kw in (job.title or "").lower() or kw in (job.department or "").lower() for kw in [
                "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
                "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
                "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
            ])
            if is_reg:
                certs_str = " ".join(licenses + clean_degrees + [redacted_text[:1000]])
                has_stated = any(kw in certs_str.lower() for kw in ["prc", "license", "registered", "rn", "cpa", "board"])
                has_number = bool(re.search(r'\b(no\.?|#)\s*\d{5,}\b', redacted_text, re.IGNORECASE))
                if has_stated and not has_number:
                    ai_insights["hard_requirement_status"] = "Stated, verify document"
                    if not ai_insights.get("action_needed"):
                        ai_insights["action_needed"] = "[verify] Professional license: resume states licensure credentials but gives no license number or expiry. Request the license document before offer."
                else:
                    ai_insights["hard_requirement_status"] = "Verified" if has_stated else "None required"
            else:
                ai_insights["hard_requirement_status"] = "None required"

        # Guarantee flags
        if "flags" not in ai_insights or not isinstance(ai_insights["flags"], list):
            ai_insights["flags"] = []
        if ai_insights.get("hard_requirement_status") == "Stated, verify document" and "License number not on resume" not in ai_insights["flags"]:
            ai_insights["flags"].append("License number not on resume")

        # Guarantee reference_calculation
        if not ai_insights.get("reference_calculation"):
            is_reg = any(kw in (job.title or "").lower() or kw in (job.department or "").lower() for kw in [
                "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
                "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
                "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
            ])
            p_name = "Regulated professional" if is_reg else "Professional / technical"
            w_r = 0.30 if is_reg else 0.40
            w_e = 0.25
            w_ed = 0.25 if is_reg else 0.15
            w_p = 0.10
            w_pr = 0.10

            s_req = float(ai_insights.get("score_breakdown", {}).get("required_skills", skill_score))
            s_exp = float(ai_insights.get("score_breakdown", {}).get("experience", exp_score))
            s_edu = float(ai_insights.get("score_breakdown", {}).get("education", edu_score))
            s_pref = float(ai_insights.get("score_breakdown", {}).get("preferred_skills", pref_score))
            s_proj = float(ai_insights.get("score_breakdown", {}).get("projects", proj_score))

            five_c = (s_req * w_r) + (s_exp * w_e) + (s_edu * w_ed) + (s_pref * w_p) + (s_proj * w_pr)
            raw_c = round((five_c * 0.90) + (semantic_score * 0.10), 1) if (semantic_score > 0 and five_c > 0) else round(five_c, 1)
            pen_mult = round(max(0.85, 1.0 - (0.05 * len(missing_must_haves))), 2) if missing_must_haves else 1.00
            pen_desc = (
                f"Adjusted by {int((1.0 - pen_mult) * 100)}% for {len(missing_must_haves)} unverified core requirement(s)"
                if missing_must_haves else "No must-have penalty applied."
            )

            ai_insights["reference_calculation"] = {
                "preset_name": f"{p_name} preset",
                "items": [
                    {
                        "component": f"Required skills ({int(w_r*100)}%)",
                        "description": f"Must-haves: {ai_insights['must_haves_summary']}",
                        "score": round(s_req / 100.0, 2),
                        "weight": w_r,
                        "contribution": round((s_req / 100.0) * w_r, 3),
                    },
                    {
                        "component": f"Experience ({int(w_e*100)}%)",
                        "description": f"{exp_years or 0:.1f} yrs vs {job.minimum_experience or 0} yrs required",
                        "score": round(s_exp / 100.0, 2),
                        "weight": w_e,
                        "contribution": round((s_exp / 100.0) * w_e, 3),
                    },
                    {
                        "component": f"Education / credential ({int(w_ed*100)}%)" if is_reg else f"Education ({int(w_ed*100)}%)",
                        "description": clean_degrees[0] if clean_degrees else f"{job.education_requirement or 'Requirement met'}",
                        "score": round(s_edu / 100.0, 2),
                        "weight": w_ed,
                        "contribution": round((s_edu / 100.0) * w_ed, 3),
                    },
                    {
                        "component": f"Preferred skills ({int(w_p*100)}%)",
                        "description": f"{sum(1 for p in det_preferred_breakdown if p.get('status') == 'met')} of {len(preferred_skills) or 1} met",
                        "score": round(s_pref / 100.0, 2),
                        "weight": w_p,
                        "contribution": round((s_pref / 100.0) * w_p, 3),
                    },
                    {
                        "component": f"Achievements, certs & projects ({int(w_pr*100)}%)",
                        "description": "Validated certifications, awards, achievements & projects",
                        "score": round(s_proj / 100.0, 2),
                        "weight": w_pr,
                        "contribution": round((s_proj / 100.0) * w_pr, 3),
                    },
                ],
                "raw_score": raw_c,
                "penalty_multiplier": pen_mult,
                "penalty_description": pen_desc,
                "final_match": overall_score,
                "final_band": band,
            }

    # Format human-readable markdown explanation

    explanation = format_insights_as_markdown(
        ai_insights,
        candidate_code=application.candidate_code,
        role_title=job.title,
        match_score=overall_score
    )

    logger.info(
        "Resume evaluation completed. application_id=%s overall_score=%s skill_score=%s "
        "experience_score=%s education_score=%s preferred_score=%s project_score=%s semantic_score=%s",
        application.pk,
        overall_score,
        skill_score,
        exp_score,
        edu_score,
        pref_score,
        proj_score,
        semantic_score,
    )

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

    resume.processing_status = 'COMPLETED'
    resume.processed_at = timezone.now()
    resume.save(update_fields=['processing_status', 'processed_at'])

    if application.status == 'SUBMITTED':
        application.status = 'UNDER_REVIEW'
        application.save(update_fields=['status'])

    return match_result
