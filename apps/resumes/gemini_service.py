import json
import logging
import re
import time
import random
import requests
from django.conf import settings
from google import genai
from google.genai import types
from apps.resumes.redaction_service import ensure_zero_pii_payload


logger = logging.getLogger(__name__)

GEMINI_MODEL_CANDIDATES = [
    "gemini-2.5-flash-lite",          # Dedicated lightweight Q&A / prompt-extraction model (fastest & uncongested)
    "gemini-2.5-flash",               # Fast primary Flash model
    "gemini-2.0-flash-lite",          # Secondary lite candidate
    "gemini-2.0-flash",               # Flagship fallback
]

CLAUDE_MODEL_CANDIDATES = [
    "claude-3-5-haiku-latest",
    "claude-3-5-sonnet-latest",
]

# Prohibited subjective and biased terms per Section 10
BANNED_SUBJECTIVE_TERMS = [
    "culture fit", "young", "energetic", "polished", "aggressive",
    "hire", "reject", "overqualified", "underqualified", "native speaker"
]


def _build_fast_json_config(model_name: str, max_output_tokens: int = 4096) -> types.GenerateContentConfig:
    kwargs = {
        "response_mime_type": "application/json",
        "temperature": 0.1,
        "max_output_tokens": max_output_tokens,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if "2.5" in model_name and hasattr(types, "ThinkingConfig"):
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
    return types.GenerateContentConfig(**kwargs)


def _clean_and_parse_json(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        # Try extracting outermost JSON object if wrapped in prose
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(cleaned[first_brace:last_brace + 1])
            except Exception:
                return None
    return None


def call_claude_json_fallback(prompt: str, max_tokens: int = 4096) -> dict | None:
    """
    Tier-2 LLM fallback using Anthropic Claude when Gemini/Vertex AI is unavailable or rate-limited.
    """
    if not getattr(settings, "ENABLE_GEMINI_PROCESSING", False):
        return None
    anthropic_key = getattr(settings, "ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        return None

    headers = {
        "x-api-key": anthropic_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    for model_name in CLAUDE_MODEL_CANDIDATES:
        try:
            logger.info("Secondary AI fallback requested. model=%s", model_name)
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={
                    "model": model_name,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt + "\n\nIMPORTANT: Output ONLY valid raw JSON with no markdown code fences or commentary."
                        }
                    ],
                },
                timeout=25,
            )
            if resp.status_code == 200:
                data = resp.json()
                content_blocks = data.get("content", [])
                text_out = "".join(b.get("text", "") for b in content_blocks if isinstance(b, dict))
                parsed = _clean_and_parse_json(text_out)
                if isinstance(parsed, dict):
                    logger.info("Secondary AI fallback succeeded. model=%s", model_name)
                    return parsed
            else:
                logger.warning("Secondary AI fallback returned an error. model=%s status=%s", model_name, resp.status_code)
                if resp.status_code in (401, 403):
                    break
        except Exception as exc:
            logger.warning("Secondary AI fallback failed. model=%s error=%s", model_name, type(exc).__name__)
            break
    return None


def _is_fatal_transport_or_auth_error(exc: Exception) -> bool:
    err_name = type(exc).__name__.lower()
    err_msg = str(exc).lower()
    if any(k in err_name for k in ("connecterror", "connectionerror", "nameresolutionerror", "timeout")):
        return True
    if any(k in err_msg for k in ("api_key_invalid", "api key not valid", "unauthenticated", "permission_denied")):
        return True
    return False

def generate_content_with_retry(
    client,
    model: str,
    contents,
    config: types.GenerateContentConfig | None = None,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
):
    """
    Executes client.models.generate_content with exponential backoff and jitter
    to smoothly recover from transient high-demand spikes (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED).
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            last_exception = e
            err_msg = str(e).lower()

            # Fail fast on non-retryable errors
            is_non_retryable = any(
                code in err_msg for code in [
                    "404", "not_found", "no longer available", "400", "invalid_argument", "permission_denied"
                ]
            )
            if is_non_retryable:
                raise e

            # Retry on transient capacity, high demand, or rate-limit errors
            is_transient = any(
                term in err_msg for term in [
                    "503", "429", "high demand", "overloaded", "unavailable",
                    "resource_exhausted", "quota", "temporarily", "rate limit"
                ]
            )

            if is_transient and attempt < max_retries - 1:
                jitter = random.uniform(0.1, 0.5)
                sleep_duration = delay + jitter
                logger.warning(
                    "AI model retry scheduled. model=%s attempt=%s max_attempts=%s delay_seconds=%.1f",
                    model, attempt + 1, max_retries, sleep_duration,
                )
                time.sleep(sleep_duration)
                delay *= backoff_factor
            else:
                raise e

    if last_exception:
        raise last_exception

def embed_content_with_retry(
    client,
    model: str,
    contents,
    config: types.EmbedContentConfig | None = None,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
):
    """
    Executes client.models.embed_content with exponential backoff and jitter.
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries):
        try:
            return client.models.embed_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            last_exception = e
            err_msg = str(e).lower()
            is_non_retryable = any(code in err_msg for code in ["404", "not_found", "400", "invalid_argument"])
            if is_non_retryable:
                raise e
            is_transient = any(
                term in err_msg for term in [
                    "503", "429", "high demand", "overloaded", "unavailable",
                    "resource_exhausted", "quota", "temporarily", "rate limit"
                ]
            )
            if is_transient and attempt < max_retries - 1:
                jitter = random.uniform(0.1, 0.5)
                sleep_duration = delay + jitter
                logger.warning(
                    "Embedding model retry scheduled. model=%s attempt=%s max_attempts=%s delay_seconds=%.1f",
                    model, attempt + 1, max_retries, sleep_duration,
                )
                time.sleep(sleep_duration)
                delay *= backoff_factor
            else:
                raise e

    if last_exception:
        raise last_exception

def get_gemini_client():
    if not settings.ENABLE_GEMINI_PROCESSING:
        return None
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.info("AI provider key is not configured; deterministic fallback enabled.")
        return None
    try:
        use_vertex = api_key.startswith("AQ.")
        if use_vertex:
            return genai.Client(vertexai=True, api_key=api_key)
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.warning("AI client initialization failed. error=%s", type(e).__name__)
        return None

def analyze_resume_with_gemini(resume_text: str) -> dict | None:
    """
    Universal, zero-shot dynamic resume extraction across all job categories:
    - Competencies, hard skills, software, tools, instruments
    - Professional licenses & certifications (PRC, CPA, RN, Civil Service, TESDA, BOSH, CCNA, CCSP, etc.)
    - Work experiences with duties, company, and duration in months
    - Education degrees and academic credentials
    - Projects, key achievements, awards, memberships, and languages
    """
    if not resume_text.strip():
        logger.info("AI extraction skipped because resume text is empty.")
        return None
    if not settings.ENABLE_GEMINI_PROCESSING:
        return None

    # Mandatory Zero-PII Gate before any text reaches the AI
    resume_text, _ = ensure_zero_pii_payload(text=resume_text)

    client = get_gemini_client()

    prompt = f"""
You are a universal recruitment information extraction system for TalentMatch.
Analyze the candidate's resume below for ANY industry or profession (Healthcare, Finance, Engineering, BPO, Customer Service, Education, Sales, IT, Hospitality, etc.).

Extract ONLY factual information directly stated in the text across ALL sections of the resume (Summary, Experience, Education, Skills, Certifications, Courses, Projects, Key Achievements, Awards, Memberships, Languages, Extracurriculars).
Do NOT extract or infer any personal data, names, locations, addresses, employer brand names, or demographic characteristics (age, gender, ethnicity, marital status, photo, school prestige).

Resume Text:
\"\"\"
{resume_text[:14000]}
\"\"\"

Return ONLY a valid JSON object matching this schema:
{{
  "industry_category": "string (e.g. Healthcare, Accounting, Engineering, Customer Service, Sales, IT, Education, General)",
  "skills": ["string"],
  "skills_with_evidence": [
    {{"name": "string", "evidence": "string (exact sentence/context from resume)"}}
  ],
  "licenses_and_certifications": [
    "string"
  ],
  "experiences": [
    {{
      "job_title": "string",
      "duration_months": 0,
      "responsibilities": "string"
    }}
  ],
  "total_experience_years": 0.0,
  "education": ["string (degree name and major field only, e.g. Bachelor of Arts in Communication, BS Accountancy, BS Nursing)"],
  "projects": ["string (notable technical, operational, academic projects, or quantified key achievements)"],
  "awards": ["string (notable awards, honors, or recognitions)"],
  "languages": ["string (spoken or written languages and proficiency level)"],
  "is_fresh_graduate": false
}}
"""

    if client:
        for model_name in GEMINI_MODEL_CANDIDATES:
            try:
                logger.info("AI resume parsing requested. model=%s", model_name)
                response = generate_content_with_retry(
                    client=client,
                    model=model_name,
                    contents=prompt,
                    config=_build_fast_json_config(model_name, max_output_tokens=4096),
                )
                if response.text:
                    parsed_json = _clean_and_parse_json(response.text)
                    if isinstance(parsed_json, dict):
                        logger.info("AI resume parsing succeeded. model=%s", model_name)
                        return parsed_json
            except Exception as e:
                logger.warning("AI resume parsing failed. model=%s error=%s", model_name, type(e).__name__)
                if _is_fatal_transport_or_auth_error(e):
                    break
                continue

    claude_parsed = call_claude_json_fallback(prompt, max_tokens=4096)
    if isinstance(claude_parsed, dict):
        return claude_parsed

    logger.warning("All AI resume parsers failed; deterministic fallback enabled.")
    return None

def extract_rubric_weights(job_data: dict, is_regulated_role: bool = False) -> tuple[float, float, float, float, float, str]:
    """
    Extracts normalized weights (w_req, w_exp, w_edu, w_pref, w_proj, preset_name)
    from job_data['rubric_weights'] if configured, otherwise falls back to preset defaults.
    """
    custom = job_data.get("rubric_weights") or {}
    if isinstance(custom, dict) and "required_skills" in custom:
        def get_w(k, default_pct):
            val = float(custom.get(k, default_pct))
            return val / 100.0 if val > 1.0 else val

        w_req = get_w("required_skills", 40)
        w_exp = get_w("experience", 25)
        w_edu = get_w("education", 15)
        w_pref = get_w("preferred_skills", 10)
        w_proj = get_w("projects", 10)
        return w_req, w_exp, w_edu, w_pref, w_proj, "Custom Recruiter Rubric"

    preset_name = "Regulated professional" if is_regulated_role else "Professional / technical"
    w_req = 0.30 if is_regulated_role else 0.40
    w_exp = 0.25
    w_edu = 0.25 if is_regulated_role else 0.15
    w_pref = 0.10
    w_proj = 0.10
    return w_req, w_exp, w_edu, w_pref, w_proj, preset_name

def evaluate_candidate_match_with_gemini(
    job_data: dict,
    redacted_profile: dict,
    redacted_text: str
) -> dict | None:
    """
    Bias-Reduced AI Match Evaluation Engine (Sections 3 & 6 of TalentMatch specification):
    The scoring model NEVER receives raw resume text containing candidate PII, age, gender,
    address, or school prestige. It evaluates purely structured, redacted performance data
    against the 5-component job rubric.
    """
    client = get_gemini_client()
    if not client:
        return None

    redacted_text, redacted_profile = ensure_zero_pii_payload(text=redacted_text, profile=redacted_profile)
    candidate_code = redacted_profile.get("candidate_code", "TM-Candidate")
    required_skills = job_data.get("required_skills", [])
    preferred_skills = job_data.get("preferred_skills", [])

    job_title_lower = (job_data.get('title') or '').lower()
    dept_lower = (job_data.get('department') or '').lower()
    is_regulated_role = any(kw in job_title_lower or kw in dept_lower for kw in [
        "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
        "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
        "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
    ])

    w_req, w_exp, w_edu, w_pref, w_proj, preset_name = extract_rubric_weights(job_data, is_regulated_role)

    prompt = f"""
You are an objective, bias-reduced AI Talent Matching Engine for TalentMatch.
All candidate identifying information (name, gender, age, address, school prestige, employer brands) has been deliberately redacted to ensure screening is strictly fair and merit-based.

Evaluate the candidate's structured, redacted profile against the job posting using this 5-component rubric ({preset_name}):
1. "required_skill_score" ({int(w_req*100)}% weight):
   Alignment with must-have competencies and licenses. Look for direct evidence of execution.
2. "experience_score" ({int(w_exp*100)}% weight):
   Alignment of work duties, career level, and tenure vs role requirements.
3. "education_score" ({int(w_edu*100)}% weight):
   Alignment of degree level, academic field, and professional certifications (evaluated without school prestige).
4. "preferred_skill_score" ({int(w_pref*100)}% weight):
   Coverage of optional, nice-to-have competencies.
5. "project_score" ({int(w_proj*100)}% weight):
   Relevance of demonstrated projects, systems built, or key operational achievements.
6. "semantic_match_score" (Contextual duty fit, 0-100):
   Overall qualitative depth of daily responsibilities vs the job description duties.

Job Details:
- Title: {job_data.get('title')}
- Department / Industry: {job_data.get('department')}
- Description & Responsibilities:
\"\"\"
{job_data.get('description', '')}
\"\"\"
- Minimum Experience Requirement: {job_data.get('minimum_experience')}
- Education Requirement: {job_data.get('education_requirement')}
- Required (Must-Have) Skills: {json.dumps(required_skills)}
- Preferred (Nice-to-Have) Skills: {json.dumps(preferred_skills)}

Redacted Candidate Profile (Protected attributes stripped):
- Candidate Identifier: {candidate_code}
- Work Experience Tenure: {redacted_profile.get('total_experience_years', 0.0)} year(s)
- Work History Roles & Responsibilities:
{json.dumps(redacted_profile.get('experience', []), indent=2)}
- Education Credentials (Degree & Field only):
{json.dumps(redacted_profile.get('education', []), indent=2)}
- Professional Licenses / Certifications:
{json.dumps(redacted_profile.get('certifications', []))}
- Validated Competencies:
{json.dumps(redacted_profile.get('skills', []))}
- Key Projects / Achievements:
{json.dumps(redacted_profile.get('projects', []))}

Sanitized Resume Context:
\"\"\"
{redacted_text[:10000]}
\"\"\"

Rubric Calculation Rules:
- Calculate scores (0.0 to 100.0) for: required_skill_score, experience_score, education_score, preferred_skill_score, project_score, semantic_match_score.
- Weighted Overall Match Formula:
  overall_score = round(
    (required_skill_score * {w_req}) +
    (experience_score * {w_exp}) +
    (education_score * {w_edu}) +
    (preferred_skill_score * {w_pref}) +
    (project_score * {w_proj}),
    1
  )
- MUST-HAVE PENALTY: If any critical must-have required skill has 0 evidence or is completely missing, cap overall_score at 60.0 maximum.
- ZERO-MATCH & REGULATED ROLE HARD GATE: If candidate has no relevant background, meets 0 must-have required skills, or holds an unrelated degree for a regulated role (nursing, CPA, engineering, legal, medical), every component score and overall_score MUST strictly be 0.0. Do NOT award baseline points to out-of-field applicants.

Return ONLY a valid JSON object matching this schema:
{{
  "overall_score": 85.5,
  "required_skill_score": 88.0,
  "experience_score": 80.0,
  "education_score": 90.0,
  "preferred_skill_score": 75.0,
  "project_score": 85.0,
  "semantic_match_score": 86.0,
  "must_have_penalty_applied": false,
  "matched_skills": ["Skill 1", "Skill 2"],
  "missing_skills": ["Missing Skill 1"]
}}
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            logger.info("AI rubric evaluation requested. model=%s", model_name)
            response = generate_content_with_retry(
                client=client,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=1200,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if response.text:
                cleaned = response.text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                parsed_json = json.loads(cleaned.strip())

                # Extract sub-scores deterministically
                req_score = max(0.0, min(100.0, float(parsed_json.get("required_skill_score", 0.0))))
                exp_score = max(0.0, min(100.0, float(parsed_json.get("experience_score", 0.0))))
                edu_score = max(0.0, min(100.0, float(parsed_json.get("education_score", 0.0))))
                pref_score = max(0.0, min(100.0, float(parsed_json.get("preferred_skill_score", 0.0))))
                proj_score = max(0.0, min(100.0, float(parsed_json.get("project_score", 0.0))))

                raw_calc = round(
                    (req_score * w_req) +
                    (exp_score * w_exp) +
                    (edu_score * w_edu) +
                    (pref_score * w_pref) +
                    (proj_score * w_proj),
                    1
                )

                missing = parsed_json.get("missing_skills", [])
                matched = parsed_json.get("matched_skills", [])
                penalty_applied = parsed_json.get("must_have_penalty_applied", False)

                overall = raw_calc
                missing_must_haves = [m for m in missing if any(m.lower() == r.lower() for r in required_skills)]
                if missing_must_haves:
                    penalty_applied = True
                    overall = round(overall * 0.85, 1)
                    if overall > 60.0:
                        overall = 60.0

                is_regulated_role = any(kw in (job_data.get("title") or "").lower() or kw in (job_data.get("department") or "").lower() for kw in [
                    "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
                    "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
                    "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
                ])

                # ZERO MATCH / HARD GATING:
                if raw_calc == 0.0:
                    overall = 0.0
                elif len(matched) == 0 and len(required_skills) > 0:
                    overall = 0.0
                elif is_regulated_role and (req_score == 0.0 or edu_score == 0.0):
                    overall = 0.0

                parsed_json["overall_score"] = overall
                parsed_json["must_have_penalty_applied"] = penalty_applied

                logger.info("AI rubric evaluation succeeded. model=%s overall_score=%s", model_name, overall)
                return parsed_json
        except Exception as e:
            logger.warning("AI rubric evaluation failed. model=%s error=%s", model_name, type(e).__name__)
            continue

    logger.warning("All AI rubric evaluations failed; deterministic fallback enabled.")
    return None

def _is_bonus_or_logistics_item(req_text: str) -> tuple[bool, bool]:
    """
    Returns (is_bonus_clause, is_screening_logistics) for a requirement string.
    """
    lower = (req_text or "").lower()
    is_bonus = bool(re.search(r'\b(is\s+a\s+plus|is\s+preferred|preferred|nice\s+to\s+have|advantage|bonus|optional)\b', lower))
    is_logistics = bool(re.search(
        r'\b(wpm|words\s+per\s+minute|typing\s+speed|shifting\s+schedule|night\s+shift|graveyard|weekend|holiday|relocat|willing\s+to\s+work|work\s+onsite|hybrid|discernible\s+accent)\b',
        lower
    ))
    return is_bonus, is_logistics


def evaluate_and_generate_insights_with_gemini(
    job_data: dict,
    redacted_profile: dict,
    redacted_text: str
) -> tuple[dict | None, dict | None]:
    """
    High-Performance Unified Bias-Reduced Match & Insights Evaluator (Sections 3, 6, & 10):
    Combines candidate match rubric scoring and grounded recruiter insights into a
    SINGLE AI API call (Gemini / Vertex AI primary, Anthropic Claude secondary fallback).

    Returns:
        tuple: (match_result_dict, insights_dict) or (None, None) on failure.
    """
    if not getattr(settings, "ENABLE_GEMINI_PROCESSING", False):
        return None, None

    # Mandatory Zero-PII Gate before any data reaches Gemini or Claude
    redacted_text, redacted_profile = ensure_zero_pii_payload(text=redacted_text, profile=redacted_profile)

    client = get_gemini_client()

    candidate_code = redacted_profile.get("candidate_code", "TM-Candidate")
    required_skills = job_data.get("required_skills", [])
    preferred_skills = job_data.get("preferred_skills", [])

    job_title_lower = (job_data.get('title') or '').lower()
    dept_lower = (job_data.get('department') or '').lower()
    is_regulated_role = any(kw in job_title_lower or kw in dept_lower for kw in [
        "nurse", "nursing", "physician", "doctor", "medical", "healthcare", "hospital",
        "pharmacist", "pharmacy", "accountant", "cpa", "civil engineer", "mechanical engineer",
        "electrical engineer", "attorney", "lawyer", "teacher", "lpt"
    ])

    w_req, w_exp, w_edu, w_pref, w_proj, preset_name = extract_rubric_weights(job_data, is_regulated_role)

    prompt = f"""You are an objective, bias-reduced AI Talent Matching and Insights Engine for TalentMatch.
All candidate identifying information (name, gender, age, address, school prestige, employer brands) has been deliberately redacted to ensure screening is strictly fair and merit-based.

CORE EVALUATION PRINCIPLES (FOLLOW STRICTLY):
1. PROOF-AWARE & INCLUSIVE RESUME MATCHING:
   - Concrete Proof (100% credit, status="met"): When the resume shows a work duty, project, certification/course, award, or quantified achievement proving the requirement, mark status="met" and cite the exact sentence and section (e.g., "Experience 1", "Certification", "Project", "Achievement").
   - General Resume Mention (85% credit, status="met"): When the applicant states a skill, tool, language, or metric in Skills, Summary, or Languages (e.g., "can type up to 70wpm", "English—Native", "Microsoft Office"), ACCEPT IT as status="met" (at 85% credit in required_skill_score) and cite the exact quote and section (e.g., "Languages", "Skills / Resume", "Summary").
   - Smart Quantitative, Hierarchy, Suite & Multi-Option Matching:
     * Quantitative: "Typing speed: 50 WPM or faster" is MET by "can type up to 70wpm" (70 >= 50). "At least six (6) months of customer service experience" is MET by 5+ years of customer service experience.
     * Education Hierarchy: Doctorate > Master > Bachelor > Associate/Diploma > High School. A Bachelor's degree ALWAYS satisfies "High school graduate or higher".
     * Software Suites: "Proficient in Microsoft software" is MET by experience, a certificate, or mention of Microsoft Office, Word, Excel, PowerPoint, Outlook, or Office 365.
     * Multi-Option ("OR" / Comma-Separated): "Develop using Python, Java" is MET if the candidate has a project, certificate, duty, or skill in Python OR Java (or related language like JavaScript).
     * Embedded Bonus Clauses: Requirements containing "is a plus", "is preferred", or "nice to have" should be matched on core or transferable experience (e.g. email/CRM/Zendesk/Salesforce support for chat) and NEVER trigger a missing must-have penalty.
     * Unstated Logistics / Live Assessments: If a requirement is a shift preference or live test (e.g., "Typing speed: 50 WPM or faster", "Willing to work on a shifting schedule (including night shifts and weekends)") and is NOT mentioned anywhere in the resume, set status="verify_in_screening" with note="Confirm in screening call / live assessment (not typically listed on resumes)". Do NOT penalize the candidate for unstated shift/test items! (If it IS mentioned in the resume, e.g. "70wpm", mark it status="met").

2. HOLISTIC 5-COMPONENT SCORING RUBRIC ({preset_name} preset):
   - required_skill_score ({int(w_req*100)}% weight): Alignment with must-have requirements (100% for proof-backed, 85% for general resume mention; exclude unstated verify_in_screening logistics from the denominator).
   - experience_score ({int(w_exp*100)}% weight): Alignment of work duties, career level, and total tenure vs role requirements.
   - education_score ({int(w_edu*100)}% weight): Alignment of degree level, academic field, and professional credentials (evaluated without school prestige).
   - preferred_skill_score ({int(w_pref*100)}% weight): Coverage of optional competencies and embedded "is a plus" / "is preferred" clauses.
   - project_score ({int(w_proj*100)}% weight): Holistic evaluation of Projects, Certifications (e.g. CCSP), Awards (e.g. Top Customer Service Representative Award), Quantified Key Achievements (e.g. 45% retention, 90% CSAT), and Leadership/Memberships.
   - semantic_match_score (0-100): Overall qualitative depth of the entire resume content vs the job description duties.
   - ZERO MATCH & REGULATED ROLE HARD GATE: If candidate has no relevant background, meets 0 must-have required skills, or holds an unrelated degree/lacks mandatory credentials for a regulated role (nursing, CPA, engineering, legal, medical), every component score and overall_score MUST strictly be 0.0.

3. GROUNDED RECRUITER INSIGHTS:
   - must_have_breakdown: List of each must-have requirement with status ("met", "verify_in_screening", "unclear", or "not found"), exact verbatim quote from the resume in "evidence", and "source" section.
   - preferred_breakdown: List of each preferred skill with status ("met" or "not found"), verbatim quote, and source section.
   - other_evidence: List of notable certifications, awards, quantified achievements, or conference talks from the resume.
   - screening_questions: 3 specific screening questions tailored to verify_in_screening items and operational depth.
   - BANNED TERMS: Do NOT use "culture fit", "young", "energetic", "polished", "aggressive", "hire", or "reject".

Job Position:
- Title: {job_data.get('title')}
- Department: {job_data.get('department')}
- Description & Responsibilities:
\"\"\"
{job_data.get('description', '')}
\"\"\"
- Minimum Experience Requirement: {job_data.get('minimum_experience')}
- Education Requirement: {job_data.get('education_requirement')}
- Required (Must-Have) Skills: {json.dumps(required_skills)}
- Preferred (Nice-to-Have) Skills: {json.dumps(preferred_skills)}

Redacted Candidate Profile (Protected attributes stripped):
- Candidate Identifier: {candidate_code}
- Work Experience Tenure: {redacted_profile.get('total_experience_years', 0.0)} year(s)
- Work History Roles & Responsibilities:
{json.dumps(redacted_profile.get('experience', []), indent=2)}
- Education Credentials (Degree & Field only):
{json.dumps(redacted_profile.get('education', []), indent=2)}
- Professional Licenses / Certifications:
{json.dumps(redacted_profile.get('certifications', []))}
- Validated Competencies:
{json.dumps(redacted_profile.get('skills', []))}
- Key Projects / Quantified Achievements:
{json.dumps(redacted_profile.get('projects', []))}
- Awards & Recognitions:
{json.dumps(redacted_profile.get('awards', []))}
- Languages:
{json.dumps(redacted_profile.get('languages', []))}

Sanitized Resume Context:
\"\"\"
{redacted_text[:10000]}
\"\"\"

Return ONLY a valid JSON object matching this schema:
{{
  "overall_score": 88.5,
  "required_skill_score": 90.0,
  "experience_score": 95.0,
  "education_score": 100.0,
  "preferred_skill_score": 85.0,
  "project_score": 90.0,
  "semantic_match_score": 90.0,
  "must_have_penalty_applied": false,
  "matched_skills": ["Skill 1", "Skill 2"],
  "missing_skills": [],
  "band": "Strong",
  "review_priority": "High",
  "why_this_score": "Candidate exceeds experience and education requirements with verified customer service duties, CRM proficiency, and quantified retention achievements.",
  "action_needed": null,
  "hard_requirement_status": "Verified",
  "flags": [],
  "must_have_breakdown": [
    {{
      "skill": "Requirement text",
      "status": "met",
      "evidence": "Exact verbatim quote from resume",
      "source": "Experience 1"
    }}
  ],
  "preferred_breakdown": [
    {{
      "skill": "Preferred skill text",
      "status": "met",
      "evidence": "Exact verbatim quote from resume",
      "source": "Experience 1"
    }}
  ],
  "other_evidence": [
    "Certified Customer Service Professional (CCSP)",
    "Top Customer Service Representative Award"
  ],
  "screening_questions": [
    "Question 1",
    "Question 2",
    "Question 3"
  ],
  "logistics": {{
    "notice_period": "Not stated",
    "work_arrangement": "Not stated"
  }},
  "executive_headline": "1 crisp neutral synthesis sentence describing candidate exact fit.",
  "fit_level": "HIGH_ALIGNMENT",
  "summary": "Neutral overview of alignment across all resume sections."
}}
"""

    parsed_json = None
    used_model = None

    if client:
        for model_name in GEMINI_MODEL_CANDIDATES:
            try:
                logger.info("Unified AI evaluation requested. model=%s", model_name)
                response = generate_content_with_retry(
                    client=client,
                    model=model_name,
                    contents=prompt,
                    config=_build_fast_json_config(model_name, max_output_tokens=4096),
                )
                if response.text:
                    candidate_json = _clean_and_parse_json(response.text)
                    if isinstance(candidate_json, dict):
                        parsed_json = candidate_json
                        used_model = model_name
                        break
            except Exception as e:
                logger.warning("Unified AI evaluation failed. model=%s error=%s", model_name, type(e).__name__)
                if _is_fatal_transport_or_auth_error(e):
                    break
                continue

    if not parsed_json:
        claude_json = call_claude_json_fallback(prompt, max_tokens=4096)
        if isinstance(claude_json, dict):
            parsed_json = claude_json
            used_model = "claude-fallback"

    if not parsed_json:
        logger.warning("All unified AI evaluations failed; deterministic fallback enabled.")
        return None, None

    try:
        req_score = max(0.0, min(100.0, float(parsed_json.get("required_skill_score", 0.0))))
        exp_score = max(0.0, min(100.0, float(parsed_json.get("experience_score", 0.0))))
        edu_score = max(0.0, min(100.0, float(parsed_json.get("education_score", 0.0))))
        pref_score = max(0.0, min(100.0, float(parsed_json.get("preferred_skill_score", 0.0))))
        proj_score = max(0.0, min(100.0, float(parsed_json.get("project_score", 0.0))))
        semantic_score = max(0.0, min(100.0, float(parsed_json.get("semantic_match_score", 0.0))))

        missing = parsed_json.get("missing_skills", []) or []
        matched = parsed_json.get("matched_skills", []) or []

        # Extract must-have breakdown first so we know which items are met / verify_in_screening / bonus
        raw_must_haves = parsed_json.get("must_have_breakdown", [])
        must_have_breakdown = []
        if isinstance(raw_must_haves, list) and len(raw_must_haves) > 0:
            for item in raw_must_haves:
                if isinstance(item, dict) and item.get("skill"):
                    sk = str(item.get("skill"))
                    st = str(item.get("status", "met")).lower().strip()
                    is_bonus, is_logistics = _is_bonus_or_logistics_item(sk)
                    if st in ("not found", "missing") and is_logistics:
                        st = "verify_in_screening"
                    must_have_breakdown.append({
                        "skill": sk,
                        "status": st,
                        "evidence": str(item.get("evidence", "")),
                        "source": str(item.get("source", "Resume")),
                        "note": str(item.get("note", "")) or (
                            "Confirm in screening call / live assessment" if st == "verify_in_screening" else ""
                        ),
                    })

        # Filter out bonus clauses and verify_in_screening items from missing_must_haves penalty
        verify_or_met_skills = {
            m["skill"].lower() for m in must_have_breakdown
            if m.get("status") in ("met", "verify_in_screening")
        }
        missing_must_haves = []
        for m in missing:
            m_low = str(m).lower()
            is_bonus, is_logistics = _is_bonus_or_logistics_item(str(m))
            if is_bonus or is_logistics or m_low in verify_or_met_skills:
                continue
            if any(m_low == r.lower() for r in required_skills):
                missing_must_haves.append(m)

        # Blend semantic duty fit (10%) into the composite rubric when semantic_score > 0
        five_comp_score = (
            (req_score * w_req) +
            (exp_score * w_exp) +
            (edu_score * w_edu) +
            (pref_score * w_pref) +
            (proj_score * w_proj)
        )
        if semantic_score > 0.0 and five_comp_score > 0.0:
            raw_calc_score = round((five_comp_score * 0.90) + (semantic_score * 0.10), 1)
        else:
            raw_calc_score = round(five_comp_score, 1)

        penalty_applied = False
        penalty_multiplier = 1.0
        overall = raw_calc_score
        if missing_must_haves:
            penalty_applied = True
            penalty_multiplier = max(0.85, 1.0 - (0.05 * len(missing_must_haves)))
            overall = round(overall * penalty_multiplier, 1)

        # ZERO MATCH & REGULATED ROLE HARD GATING:
        if raw_calc_score == 0.0:
            overall = 0.0
        elif len(matched) == 0 and len(required_skills) > 0 and not verify_or_met_skills:
            overall = 0.0
        elif is_regulated_role and (req_score == 0.0 or edu_score == 0.0):
            overall = 0.0

        parsed_json["overall_score"] = overall
        parsed_json["must_have_penalty_applied"] = penalty_applied

        if overall == 0.0 or (is_regulated_role and (req_score == 0.0 or edu_score == 0.0)):
            fit_level = "GATED_MISSING_MUST_HAVE"
        elif overall >= 75.0:
            fit_level = "HIGH_ALIGNMENT"
        elif overall >= 50.0:
            fit_level = "MODERATE_FIT"
        else:
            fit_level = "REQUIRES_REVIEW"

        raw_checklist = parsed_json.get("must_have_checklist")
        must_have_checklist = []
        if isinstance(raw_checklist, list) and len(raw_checklist) > 0:
            for item in raw_checklist:
                if isinstance(item, dict) and "criterion" in item:
                    must_have_checklist.append({
                        "criterion": str(item.get("criterion", "")),
                        "status": str(item.get("status", "MET")).upper(),
                        "evidence": str(item.get("evidence", ""))
                    })

        if not must_have_checklist:
            min_exp = job_data.get("minimum_experience")
            cand_exp = redacted_profile.get("total_experience_years", 0.0)
            if exp_score == 0.0:
                exp_status = "MISSING"
                exp_ev = f"No relevant domain experience found ({cand_exp:.1f} yr(s) in unrelated field)"
            elif exp_score < 70.0:
                exp_status = "PARTIAL"
                exp_ev = f"{cand_exp:.1f} yr(s) recorded (partial match for {min_exp or 'role'})"
            else:
                exp_status = "MET"
                exp_ev = f"{cand_exp:.1f} year(s) relevant experience"

            must_have_checklist.append({
                "criterion": f"Experience Requirement ({min_exp or 'General'})",
                "status": exp_status,
                "evidence": exp_ev
            })

            edu_req = job_data.get("education_requirement")
            cand_edu = redacted_profile.get("education", [])
            edu_labels = [
                e.get("credential_summary") or f"{e.get('level', '')} in {e.get('field', '')}"
                if isinstance(e, dict) else str(e)
                for e in cand_edu[:2]
            ]
            if edu_score == 0.0:
                edu_status = "MISSING"
                edu_ev = "No relevant degree or mandatory license found for this profession"
            elif edu_score < 70.0:
                edu_status = "PARTIAL"
                edu_ev = ", ".join(edu_labels) if edu_labels else "Degree credential requires verification"
            else:
                edu_status = "MET"
                edu_ev = ", ".join(edu_labels) if edu_labels else "Degree credential on file"

            must_have_checklist.append({
                "criterion": f"Education ({edu_req or 'Relevant Degree'})",
                "status": edu_status,
                "evidence": edu_ev
            })

            for item in must_have_breakdown[:4]:
                st = item.get("status", "met")
                must_have_checklist.append({
                    "criterion": f"Must-Have: {item['skill']}",
                    "status": "MET" if st == "met" else ("PARTIAL" if st in ("verify_in_screening", "unclear") else "MISSING"),
                    "evidence": item.get("evidence") or item.get("note") or f"Demonstrated competency in {item['skill']}"
                })

        raw_pinpoints = parsed_json.get("key_pinpoints")
        key_pinpoints = []
        if isinstance(raw_pinpoints, list) and len(raw_pinpoints) > 0:
            for p in raw_pinpoints:
                if isinstance(p, dict) and (p.get("headline") or p.get("evidence")):
                    key_pinpoints.append({
                        "headline": str(p.get("headline", "Validated Competency")),
                        "evidence": str(p.get("evidence", "")),
                        "impact_metric": str(p.get("impact_metric", "")),
                        "source": str(p.get("source", "Experience 1"))
                    })

        if not key_pinpoints:
            for item in must_have_breakdown:
                if item.get("status") == "met" and item.get("evidence"):
                    key_pinpoints.append({
                        "headline": item["skill"],
                        "evidence": item["evidence"],
                        "impact_metric": f"Verified match for {item['skill']}",
                        "source": item.get("source", "Experience 1")
                    })

        raw_guide = parsed_json.get("interview_guide")
        interview_guide = []
        if isinstance(raw_guide, list) and len(raw_guide) > 0:
            for g in raw_guide:
                if isinstance(g, dict) and g.get("question"):
                    interview_guide.append({
                        "question": str(g.get("question", "")),
                        "probe_reason": str(g.get("probe_reason", "Verify hands-on operational depth")),
                        "target_signal": str(g.get("target_signal", "Demonstrates direct execution and problem-solving"))
                    })

        match_dict = {
            "overall_score": overall,
            "required_skill_score": req_score,
            "experience_score": exp_score,
            "education_score": edu_score,
            "preferred_skill_score": pref_score,
            "project_score": proj_score,
            "semantic_match_score": semantic_score,
            "must_have_penalty_applied": penalty_applied,
            "matched_skills": matched,
            "missing_skills": missing_must_haves,
        }

        if overall >= 85.0:
            band = "Strong"
            review_priority = "High"
        elif overall >= 70.0:
            band = "Good"
            review_priority = "Medium"
        elif overall >= 50.0:
            band = "Partial"
            review_priority = "Medium" if overall >= 60.0 else "Low"
        else:
            band = "Weak"
            review_priority = "Low"

        raw_preferred = parsed_json.get("preferred_breakdown", [])
        preferred_breakdown = []
        if isinstance(raw_preferred, list) and len(raw_preferred) > 0:
            for item in raw_preferred:
                if isinstance(item, dict) and item.get("skill"):
                    preferred_breakdown.append({
                        "skill": str(item.get("skill")),
                        "status": str(item.get("status", "met")).lower(),
                        "evidence": str(item.get("evidence", "")),
                        "source": str(item.get("source", "Experience 1")),
                        "note": str(item.get("note", ""))
                    })

        met_must_haves = sum(1 for m in must_have_breakdown if m.get("status") == "met")
        verify_count = sum(1 for m in must_have_breakdown if m.get("status") == "verify_in_screening")
        evaluable_must_haves = max(0, len(must_have_breakdown) - verify_count) if must_have_breakdown else len(required_skills)
        if evaluable_must_haves > 0:
            must_haves_summary = f"{met_must_haves} / {evaluable_must_haves}" + (f" (+{verify_count} screening)" if verify_count else "")
        else:
            must_haves_summary = "All met"

        flags = parsed_json.get("flags") or []
        if not isinstance(flags, list):
            flags = []

        certs_text = " ".join([str(c) for c in redacted_profile.get("certifications", [])] + [str(e) for e in redacted_profile.get("education", [])])
        hard_req_status = parsed_json.get("hard_requirement_status", "None required")
        action_needed = parsed_json.get("action_needed")

        if is_regulated_role:
            has_license_stated = any(kw in certs_text.lower() for kw in ["prc", "license", "registered", "rn", "cpa", "board"])
            has_license_number = bool(re.search(r'\b(no\.?|#)\s*\d{5,}\b', redacted_text, re.IGNORECASE))
            if has_license_stated and not has_license_number:
                hard_req_status = "Stated, verify document"
                if not action_needed:
                    action_needed = "[verify] Professional license: resume states licensure credentials but gives no license number or expiry. Request the license document before offer."
                if "License number not on resume" not in flags:
                    flags.append("License number not on resume")

        screening_questions = parsed_json.get("screening_questions") or []
        if not screening_questions:
            screening_questions = [g.get("question") for g in interview_guide if g.get("question")]

        if overall == 0.0 and raw_calc_score == 0.0:
            penalty_note = "Candidate does not meet any must-have requirements or credentials for this role (0% match)."
        elif overall == 0.0 and raw_calc_score > 0.0:
            penalty_note = "Candidate score gated to 0% due to missing mandatory professional credentials/skills for regulated role."
        elif penalty_applied:
            penalty_note = f"Raw score ({raw_calc_score}%), adjusted to {overall:.1f}% for {len(missing_must_haves)} unverified core requirement(s)."
        else:
            penalty_note = "No must-have penalty applied."

        logistics = parsed_json.get("logistics") or {}
        if not isinstance(logistics, dict):
            logistics = {}
        logistics.pop("location", None)

        ref_items = [
            {
                "component": f"Required skills ({int(w_req*100)}%)",
                "description": f"Must-haves: {must_haves_summary}",
                "score": round(req_score / 100.0, 2),
                "weight": w_req,
                "contribution": round((req_score / 100.0) * w_req, 3),
            },
            {
                "component": f"Experience ({int(w_exp*100)}%)",
                "description": f"{redacted_profile.get('total_experience_years', 0.0):.1f} yrs vs {job_data.get('minimum_experience') or 0} yrs required",
                "score": round(exp_score / 100.0, 2),
                "weight": w_exp,
                "contribution": round((exp_score / 100.0) * w_exp, 3),
            },
            {
                "component": f"Education / credential ({int(w_edu*100)}%)" if is_regulated_role else f"Education ({int(w_edu*100)}%)",
                "description": f"{job_data.get('education_requirement') or 'Requirement met'}",
                "score": round(edu_score / 100.0, 2),
                "weight": w_edu,
                "contribution": round((edu_score / 100.0) * w_edu, 3),
            },
            {
                "component": f"Preferred skills ({int(w_pref*100)}%)",
                "description": f"{sum(1 for p in preferred_breakdown if p.get('status') == 'met')} of {len(preferred_skills) or len(preferred_breakdown) or 1} met",
                "score": round(pref_score / 100.0, 2),
                "weight": w_pref,
                "contribution": round((pref_score / 100.0) * w_pref, 3),
            },
            {
                "component": f"Achievements, certs & projects ({int(w_proj*100)}%)",
                "description": "Demonstrated certifications, awards, achievements & projects",
                "score": round(proj_score / 100.0, 2),
                "weight": w_proj,
                "contribution": round((proj_score / 100.0) * w_proj, 3),
            },
        ]

        reference_calculation = {
            "preset_name": f"{preset_name} preset",
            "items": ref_items,
            "raw_score": raw_calc_score,
            "penalty_multiplier": round(penalty_multiplier, 2),
            "penalty_description": penalty_note,
            "final_match": overall,
            "final_band": band,
        }

        insights_dict = {
            "band": band,
            "review_priority": review_priority,
            "why_this_score": parsed_json.get("why_this_score") or parsed_json.get("executive_headline") or f"Direct evidence for {must_haves_summary} must-haves.",
            "action_needed": action_needed,
            "hard_requirement_status": hard_req_status,
            "flags": flags,
            "must_haves_summary": must_haves_summary,
            "must_have_breakdown": must_have_breakdown,
            "preferred_breakdown": preferred_breakdown,
            "other_evidence": parsed_json.get("other_evidence", []),
            "screening_questions": screening_questions[:3],
            "logistics": logistics,
            "penalty_note": penalty_note,
            "reference_calculation": reference_calculation,
            "executive_headline": parsed_json.get(
                "executive_headline",
                f"Candidate demonstrates {overall:.1f}% alignment with {job_data.get('title', 'role')} requirements."
            ),
            "fit_level": fit_level,
            "summary": parsed_json.get("summary", "Neutral alignment assessment completed."),
            "must_have_checklist": must_have_checklist,
            "key_pinpoints": key_pinpoints,
            "strengths": parsed_json.get("strengths", []),
            "gaps": parsed_json.get("gaps", []),
            "interview_guide": interview_guide,
            "interview_focus": parsed_json.get("interview_focus", []),
            "score_breakdown": {
                "required_skills": req_score,
                "experience": exp_score,
                "education": edu_score,
                "preferred_skills": pref_score,
                "projects": proj_score,
            },
            "fields_used": ["skills evidence", "experience", "education level", "certifications", "projects", "awards", "languages"],
            "fields_excluded": ["name", "contact details", "address", "location", "age", "gender", "school prestige", "employer names", "graduation year"]
        }

        logger.info("Unified AI evaluation succeeded. model=%s overall_score=%s", used_model, overall)
        return match_dict, insights_dict
    except Exception as e:
        logger.warning("Unified AI response validation failed. error=%s", type(e).__name__)
        return None, None

def validate_insights(insights: dict, reference_text: str) -> list[str]:
    """
    Validates grounded AI insights (Section 10 of TalentMatch specification):
    1. Checks each strength includes a concrete, non-empty evidence quote.
    2. Verifies the evidence quote is grounded in the candidate's resume text.
    3. Confirms each strength has an identifiable source section (e.g. experience[0], projects).
    4. Rejects prohibited subjective phrases ('culture fit', 'young', 'energetic', 'hire', 'reject').
    """
    errors = []
    ref_lower = (reference_text or "").lower()

    strengths = insights.get("strengths", [])
    if not isinstance(strengths, list) or len(strengths) == 0:
        errors.append("No grounded strengths provided in insights")

    for s in strengths:
        skill = s.get("skill", "Unspecified skill")
        ev = s.get("evidence", "").strip()
        if not ev:
            errors.append(f"Strength '{skill}' has no evidence quote")
        else:
            # Check grounding: either full quote or majority of significant tokens appear in reference text
            ev_clean = re.sub(r'[^\w\s]', '', ev.lower())
            tokens = [w for w in ev_clean.split() if len(w) >= 4]
            if tokens:
                found_tokens = sum(1 for t in tokens if t in ref_lower)
                ratio = found_tokens / len(tokens)
                if ratio < 0.4:
                    errors.append(f"Evidence for '{skill}' not sufficiently grounded in resume text")
        if not s.get("source"):
            errors.append(f"Strength '{skill}' is missing a source location field")

    # Banned terms check
    all_text = " ".join([
        str(insights.get("summary", "")),
        " ".join(insights.get("interview_focus", [])),
        " ".join([g.get("note", "") for g in insights.get("gaps", []) if isinstance(g, dict)])
    ]).lower()

    for term in BANNED_SUBJECTIVE_TERMS:
        if term in all_text:
            errors.append(f"Prohibited subjective term detected in insights: '{term}'")

    return errors

def generate_grounded_insights_with_gemini(
    job_data: dict,
    redacted_profile: dict,
    scores: dict,
    redacted_text: str
) -> dict | None:
    """
    Generates structured, grounded AI Recruiter Insights conforming strictly
    to Section 10 JSON schema:
    - Every strength MUST cite an exact evidence quote and source field.
    - Gaps use neutral language ('No mention found in resume').
    - Discloses fields used vs excluded for auditability.
    """
    client = get_gemini_client()
    if not client:
        return None

    redacted_text, redacted_profile = ensure_zero_pii_payload(text=redacted_text, profile=redacted_profile)

    prompt = f"""
You are an explainable, objective AI Talent Screening Assistant for TalentMatch.
Generate grounded recruiter insights strictly matching the JSON schema below.

CRITICAL FAIRNESS & GROUNDING RULES:
1. USE ONLY THE PROVIDED REDACTED DATA. Do NOT infer personal traits, gender, age, ethnicity, or school prestige.
2. EVERY STRENGTH MUST INCLUDE A DIRECT EVIDENCE QUOTE from the candidate's responsibilities or projects, and specify its source section (e.g. "experience[0]", "projects[0]", "certifications").
3. If a required skill is absent, classify it under gaps as "No mention found in resume". Do NOT assume incompetence.
4. BANNED TERMS: Do NOT use "culture fit", "young", "energetic", "polished", "aggressive", "hire", or "reject".
5. Do NOT give a hiring or rejection command. Provide objective evaluation to assist human recruiters.
6. Use the exact score numbers provided below; do not alter them.

Job Position:
- Title: {job_data.get('title')}
- Department: {job_data.get('department')}
- Required Competencies: {json.dumps(job_data.get('required_skills', []))}
- Preferred Competencies: {json.dumps(job_data.get('preferred_skills', []))}
- Minimum Experience: {job_data.get('minimum_experience')}
- Education Requirement: {job_data.get('education_requirement')}

Candidate Redacted Profile:
- Candidate Code: {redacted_profile.get('candidate_code')}
- Work History:
{json.dumps(redacted_profile.get('experience', []), indent=2)}
- Education:
{json.dumps(redacted_profile.get('education', []), indent=2)}
- Licenses & Certifications:
{json.dumps(redacted_profile.get('certifications', []))}
- Skills:
{json.dumps(redacted_profile.get('skills', []))}
- Projects:
{json.dumps(redacted_profile.get('projects', []))}

Evaluated Scores:
- Overall Match: {scores.get('overall_score')}%
- Required Skills: {scores.get('required_skill_score', scores.get('skill_score', 0))}%
- Experience: {scores.get('experience_score', scores.get('exp_score', 0))}%
- Education: {scores.get('education_score', scores.get('edu_score', 0))}%
- Preferred Skills: {scores.get('preferred_skill_score', 0)}%
- Projects: {scores.get('project_score', 0)}%

Return ONLY a valid JSON object matching this schema:
{{
  "executive_headline": "Senior specialist with 5+ yrs experience executing core duties; lacks secondary cloud tools.",
  "fit_level": "HIGH_ALIGNMENT",
  "summary": "Concise 2-3 sentence neutral overview of job alignment...",
  "must_have_checklist": [
    {{"criterion": "Minimum Experience", "status": "MET", "evidence": "X years recorded"}},
    {{"criterion": "Education Requirement", "status": "MET", "evidence": "Degree verified"}}
  ],
  "key_pinpoints": [
    {{
      "headline": "Core Technical / Operational Expertise",
      "evidence": "Exact quoted sentence from candidate profile",
      "impact_metric": "Quantified result or system scale if stated",
      "source": "experience[0]"
    }}
  ],
  "strengths": [
    {{
      "skill": "Competency Name",
      "matched_requirement": "Job Requirement Name",
      "evidence": "Exact quoted phrasing or contextual task from candidate profile",
      "source": "experience[0] or projects[0] or certifications",
      "confidence": "high or medium"
    }}
  ],
  "gaps": [
    {{
      "skill": "Skill Name",
      "type": "must_have or nice_to_have",
      "note": "No mention found in resume"
    }}
  ],
  "interview_guide": [
    {{
      "question": "Specific question tailored to candidate's background or identified gap",
      "probe_reason": "Why this question should be asked",
      "target_signal": "What specific operational knowledge or depth the candidate should demonstrate"
    }}
  ],
  "interview_focus": [
    "Specific technical or operational area to probe during human interview"
  ],
  "score_breakdown": {{
    "required_skills": {scores.get('required_skill_score', scores.get('skill_score', 0))},
    "experience": {scores.get('experience_score', scores.get('exp_score', 0))},
    "education": {scores.get('education_score', scores.get('edu_score', 0))},
    "preferred_skills": {scores.get('preferred_skill_score', 0)},
    "projects": {scores.get('project_score', 0)}
  }},
  "fields_used": ["skills", "experience", "education_level", "certifications", "projects"],
  "fields_excluded": ["name", "gender", "age", "address", "school_prestige", "employer_prestige", "graduation_year"]
}}
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            logger.info("Grounded AI insights requested. model=%s", model_name)
            response = generate_content_with_retry(
                client=client,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if response.text:
                cleaned = response.text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                insights_json = json.loads(cleaned.strip())

                # Validate insights
                validation_errors = validate_insights(insights_json, redacted_text)
                if validation_errors:
                    logger.warning("AI insights validation failed. issue_count=%s", len(validation_errors))
                    valid_strengths = [s for s in insights_json.get("strengths", []) if s.get("evidence")]
                    insights_json["strengths"] = valid_strengths if valid_strengths else []

                logger.info("Grounded AI insights succeeded. model=%s", model_name)
                return insights_json
        except Exception as e:
            logger.warning("Grounded AI insights failed. model=%s error=%s", model_name, type(e).__name__)
            continue

    logger.warning("All grounded AI insight requests failed.")
    return None

def format_insights_as_markdown(insights: dict, candidate_code: str = "TM-Candidate", role_title: str = "Candidate", match_score: float = 0.0) -> str:
    """
    Renders structured Section 10 AI Recruiter Insights matching the exact
    Candidate Card specification in docs/TalentMatch_Sample_Recruiter_Summaries.md.
    """
    band = insights.get("band") or ("Strong" if match_score >= 85 else "Good" if match_score >= 70 else "Partial" if match_score >= 50 else "Weak")
    priority = insights.get("review_priority") or ("High" if match_score >= 85 else "Medium" if match_score >= 70 else "Low")
    penalty_note = insights.get("penalty_note") or ("No must-have penalty applied." if match_score >= 60 else "Must-have penalty applied.")
    why_this_score = insights.get("why_this_score") or insights.get("executive_headline") or insights.get("summary", "Alignment evaluation completed.")
    action_needed = insights.get("action_needed")
    must_have_breakdown = insights.get("must_have_breakdown", [])
    preferred_breakdown = insights.get("preferred_breakdown", [])
    other_evidence = insights.get("other_evidence", [])
    questions = insights.get("screening_questions", [])
    logistics = insights.get("logistics", {})
    breakdown = insights.get("score_breakdown", {})
    fields_used = ", ".join(insights.get("fields_used", ["skills evidence", "experience", "education level", "projects"]))
    fields_excluded = ", ".join(insights.get("fields_excluded", ["name", "contact details", "address", "school prestige", "employer names", "graduation year"]))

    md_lines = [
        f"CANDIDATE {candidate_code}  |  {role_title}",
        f"MATCH {match_score:.0f}% ({band})  |  Review priority: {priority}",
        f"{penalty_note}\n",
        f"WHY THIS SCORE",
        f"{why_this_score}\n",
    ]

    if action_needed:
        md_lines.append("ACTION NEEDED BEFORE OFFER")
        md_lines.append(f"{action_needed}\n")

    if must_have_breakdown:
        md_lines.append("MUST-HAVE SKILLS")
        for m in must_have_breakdown:
            status_tag = f"[{m.get('status', 'met')}]"
            skill_name = m.get("skill", "")
            if m.get("status") == "met":
                ev = m.get("evidence", "")
                src = f" ({m.get('source', 'Experience 1')})" if m.get("source") else ""
                md_lines.append(f"{status_tag:<12} {skill_name:<15} \"{ev}\"{src}")
            elif m.get("status") == "unclear":
                note = m.get("note") or "Listed under Skills only. No sentence shows hands-on use."
                md_lines.append(f"{status_tag:<12} {skill_name:<15} {note}")
            else:
                note = m.get("note") or "No mention found in resume"
                md_lines.append(f"{status_tag:<12} {skill_name:<15} {note}")
        md_lines.append("")

    if preferred_breakdown:
        md_lines.append("PREFERRED SKILLS")
        for p in preferred_breakdown:
            status_tag = f"[{p.get('status', 'met')}]"
            skill_name = p.get("skill", "")
            if p.get("status") == "met":
                ev = p.get("evidence", "")
                src = f" ({p.get('source', 'Experience 1')})" if p.get("source") else ""
                md_lines.append(f"{status_tag:<12} {skill_name:<15} \"{ev}\"{src}")
            else:
                note = p.get("note") or "No mention found in resume"
                md_lines.append(f"{status_tag:<12} {skill_name:<15} {note}")
        md_lines.append("")

    if other_evidence:
        md_lines.append("OTHER EVIDENCE")
        for o in other_evidence:
            md_lines.append(f"- {o}")
        md_lines.append("")

    md_lines.append("SCORE BREAKDOWN")
    md_lines.append(
        f"Required skills {breakdown.get('required_skills', 0):.0f} | "
        f"Experience {breakdown.get('experience', 0):.0f} | "
        f"Education {breakdown.get('education', 0):.0f} | "
        f"Preferred {breakdown.get('preferred_skills', 0):.0f} | "
        f"Achievements {breakdown.get('projects', 0):.0f}\n"
    )

    if questions:
        md_lines.append("ASK IN THE SCREENING CALL")
        for idx, q in enumerate(questions, 1):
            md_lines.append(f"{idx}. {q}")
        md_lines.append("")

    if logistics:
        md_lines.append("LOGISTICS (shown, not scored)")
        log_parts = []
        if logistics.get("notice_period"):
            log_parts.append(f"Notice period: {logistics['notice_period']}")
        if logistics.get("work_arrangement"):
            log_parts.append(f"Work arrangement: {logistics['work_arrangement']}")
        if logistics.get("expected_salary"):
            log_parts.append(f"Expected salary: {logistics['expected_salary']}")
        md_lines.append(" | ".join(log_parts) if log_parts else "Not stated")
        md_lines.append("")

    md_lines.append("DATA USED")
    md_lines.append(f"Scored on: {fields_used}")
    md_lines.append(f"Not used: {fields_excluded}")
    md_lines.append("Parse confidence: High")

    return "\n".join(md_lines)

# Backward-compatibility alias
def generate_recruiter_insights_with_gemini(job_data: dict, candidate_data: dict, scores: dict) -> str | None:
    redacted_profile = {
        "candidate_code": candidate_data.get("candidate_code", "TM-Candidate"),
        "skills": candidate_data.get("skills", []),
        "experience": [{"title": "Role", "duties": f"Work history ({candidate_data.get('experience_years', 0)} yrs)"}],
        "education": candidate_data.get("education", []),
        "certifications": candidate_data.get("licenses", []),
        "projects": candidate_data.get("projects", [])
    }
    insights = generate_grounded_insights_with_gemini(job_data, redacted_profile, scores, "")
    if insights:
        return format_insights_as_markdown(
            insights,
            candidate_code=candidate_data.get("candidate_code", "TM-Candidate"),
            role_title=job_data.get("title", "Role"),
            match_score=float(scores.get("overall_score", 0.0))
        )
    return None
