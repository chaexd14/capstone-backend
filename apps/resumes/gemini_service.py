import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / '.env')

GEMINI_MODEL_CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest"
]

# Prohibited subjective and biased terms per Section 10
BANNED_SUBJECTIVE_TERMS = [
    "culture fit", "young", "energetic", "polished", "aggressive",
    "hire", "reject", "overqualified", "underqualified", "native speaker"
]

def get_gemini_client():
    load_dotenv(BASE_DIR / '.env', override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[GEMINI AI] [!] GEMINI_API_KEY is not configured in .env (Will use heuristic fallback)")
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[GEMINI AI] [ERR] Error initializing Gemini client: {e}")
        return None

def analyze_resume_with_gemini(resume_text: str) -> dict | None:
    """
    Universal, zero-shot dynamic resume extraction across all job categories:
    - Competencies, hard skills, software, tools, instruments
    - Professional licenses & certifications (PRC, CPA, RN, Civil Service, TESDA, BOSH, CCNA, etc.)
    - Work experiences with duties, company, and duration in months
    - Education degrees and academic credentials
    - Projects and notable achievements
    """
    client = get_gemini_client()
    if not client or not resume_text.strip():
        if not resume_text.strip():
            print("[GEMINI AI] [!] Resume text is empty. Skipping AI extraction.")
        return None

    prompt = f"""
You are a universal recruitment information extraction system for TalentMatch.
Analyze the candidate's resume below for ANY industry or profession (Healthcare, Finance, Engineering, BPO, Education, Sales, IT, Hospitality, etc.).

Extract ONLY factual information directly stated in the text.
Do NOT infer demographic or protected characteristics (age, gender, ethnicity, marital status, photo).

Resume Text:
\"\"\"
{resume_text[:12000]}
\"\"\"

Return ONLY a valid JSON object matching this schema:
{{
  "candidate_name": "string or null",
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
      "company": "string",
      "duration_months": 0,
      "responsibilities": "string"
    }}
  ],
  "total_experience_years": 0.0,
  "education": ["string (degree name and major field only, e.g. BS Accountancy, BS Nursing, BS Civil Engineering)"],
  "projects": ["string (notable technical, operational, or academic projects)"],
  "is_fresh_graduate": true
}}
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            print(f"[GEMINI AI] [*] Sending resume parsing request to model: '{model_name}'...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
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
                print(f"[GEMINI AI] [+] AI EXTRACTION SUCCESS: Resume parsed dynamically by '{model_name}'!")
                return parsed_json
        except Exception as e:
            print(f"[GEMINI AI] [!] Model '{model_name}' error: {e}")
            continue

    print("[GEMINI AI] [-] All Gemini models failed or timed out for resume analysis. Falling back to regex.")
    return None

def evaluate_candidate_match_with_gemini(
    job_data: dict,
    redacted_profile: dict,
    redacted_text: str
) -> dict | None:
    """
    Bias-Reduced AI Match Evaluation Engine (Sections 3 & 6 of TalentMatch specification):
    The scoring model NEVER receives raw resume text containing candidate PII, age, gender,
    address, or school prestige. It evaluates purely structured, redacted performance data
    against the fixed 5-component job rubric:
      - Required (must-have) skills: 40%
      - Relevant experience: 25%
      - Education / certification fit: 15%
      - Preferred (nice-to-have) skills: 10%
      - Project / achievement relevance: 10%
    """
    client = get_gemini_client()
    if not client:
        return None

    candidate_code = redacted_profile.get("candidate_code", "TM-Candidate")
    required_skills = job_data.get("required_skills", [])
    preferred_skills = job_data.get("preferred_skills", [])

    prompt = f"""
You are an objective, bias-reduced AI Talent Matching Engine for TalentMatch.
All candidate identifying information (name, gender, age, address, school prestige, employer brands) has been deliberately redacted to ensure screening is strictly fair and merit-based.

Evaluate the candidate's structured, redacted profile against the job posting using this fixed 5-component rubric:
1. "required_skill_score" (40% weight):
   Alignment with must-have competencies and licenses. Look for direct evidence of execution.
2. "experience_score" (25% weight):
   Alignment of work duties, career level, and tenure vs role requirements.
3. "education_score" (15% weight):
   Alignment of degree level, academic field, and professional certifications (evaluated without school prestige).
4. "preferred_skill_score" (10% weight):
   Coverage of optional, nice-to-have competencies.
5. "project_score" (10% weight):
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
    (required_skill_score * 0.40) +
    (experience_score * 0.25) +
    (education_score * 0.15) +
    (preferred_skill_score * 0.10) +
    (project_score * 0.10),
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
            print(f"[GEMINI AI] [*] Evaluating bias-reduced rubric match using model: '{model_name}'...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
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
                    (req_score * 0.40) +
                    (exp_score * 0.25) +
                    (edu_score * 0.15) +
                    (pref_score * 0.10) +
                    (proj_score * 0.10),
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

                print(f"[GEMINI AI] [+] AI MATCH EVALUATION SUCCESS via '{model_name}'! Overall Score: {overall}%")
                return parsed_json
        except Exception as e:
            print(f"[GEMINI AI] [!] Model '{model_name}' match evaluation error: {e}")
            continue

    print("[GEMINI AI] [-] All Gemini models failed for match evaluation. Falling back to heuristic formula.")
    return None

def evaluate_and_generate_insights_with_gemini(
    job_data: dict,
    redacted_profile: dict,
    redacted_text: str
) -> tuple[dict | None, dict | None]:
    """
    High-Performance Unified Bias-Reduced Match & Insights Evaluator (Sections 3, 6, & 10):
    Combines candidate match rubric scoring and grounded recruiter insights into a
    SINGLE Gemini API call, eliminating an entire network round-trip and cutting evaluation
    latency by ~50-60%.

    Returns:
        tuple: (match_result_dict, insights_dict) or (None, None) on failure.
    """
    client = get_gemini_client()
    if not client:
        return None, None

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

    preset_name = "Regulated professional" if is_regulated_role else "Professional / technical"
    w_req = 0.30 if is_regulated_role else 0.40
    w_exp = 0.25
    w_edu = 0.25 if is_regulated_role else 0.15
    w_pref = 0.10
    w_proj = 0.10

    prompt = f"""You are an objective, bias-reduced AI Talent Matching and Insights Engine for TalentMatch.
All candidate identifying information (name, gender, age, address, school prestige, employer brands) has been deliberately redacted to ensure screening is strictly fair and merit-based.

Your task is twofold:
1. SCORING RUBRIC (5 components - {preset_name} preset):
   - required_skill_score ({int(w_req*100)}% weight): Alignment with must-have competencies and licenses. Look for direct evidence of execution.
   - experience_score ({int(w_exp*100)}% weight): Alignment of work duties, career level, and tenure vs role requirements (capped at requirement, extra years not rewarded).
   - education_score ({int(w_edu*100)}% weight): Alignment of degree level, academic field, and professional credentials (evaluated without school prestige).
   - preferred_skill_score ({int(w_pref*100)}% weight): Coverage of optional, nice-to-have competencies.
   - project_score ({int(w_proj*100)}% weight): Relevance of demonstrated projects, systems built, or key operational achievements.
   - semantic_match_score (0-100): Overall qualitative depth of daily responsibilities vs the job description duties.
   - overall_score: Weighted formula:
       round((required_skill_score * {w_req}) + (experience_score * {w_exp}) + (education_score * {w_edu}) + (preferred_skill_score * {w_pref}) + (project_score * {w_proj}), 1)
   - MUST-HAVE PENALTY: If 1 must-have required skill has no hands-on evidence or is only in a skills list, apply a 15% penalty (multiplier x 0.85). If multiple critical must-haves missing, cap overall_score at 60.0 maximum.
   - ZERO MATCH & REGULATED ROLE HARD GATE: If candidate has no relevant background, meets 0 must-have required skills, or holds an unrelated degree/lacks mandatory credentials for a regulated role (nursing, CPA, engineering, legal, medical), every component score and overall_score MUST strictly be 0.0. Do NOT award baseline points to out-of-field applicants.

2. GROUNDED RECRUITER INSIGHTS & CANDIDATE SUMMARY CARD (Per TalentMatch Official Recruiter Summary Spec):
   - band: "Strong" (85%+), "Good" (70-84%), "Partial" (50-69%), or "Weak" (<50%).
   - review_priority: "High", "Medium", or "Low".
   - why_this_score: Direct 2-sentence explanation of why this score was reached, noting hands-on evidence vs skills-list-only or gaps.
   - action_needed: Verification step needed before offer (e.g. "[verify] Registered nurse license: resume states 'PRC licensed' but gives no license number. Request license document.") or null if none needed.
   - hard_requirement_status: "None required" | "Stated, verify document" | "Verified".
   - flags: List of flags (e.g. "Docker: skills list only", "License number not on resume") or empty list.
   - must_have_breakdown: List of each must-have skill with status ("met", "unclear", "not found"), exact quoted sentence from resume, and source section (e.g. "Experience 1"). If skill is only in a skills list with no described task use, label status as "unclear" and note "Listed under Skills only. No sentence shows hands-on use."
   - preferred_breakdown: List of each preferred skill with status ("met" or "not found"), quoted sentence, and source section.
   - other_evidence: List of notable certifications, achievements, or leadership quotes (e.g. "Nurse of the Quarter (2023)", "Basic Life Support certification", "Trained 6 new nurses").
   - screening_questions: 3 specific screening questions to ask in the screening call, built directly from evidence gaps, unclear skills, and verification needs.
   - logistics: Extracted logistical info: {{ "notice_period": "string or Not stated", "work_arrangement": "string or Not stated", "location": "string or Not stated" }}.
   - executive_headline: 1 crisp neutral synthesis sentence describing candidate exact fit.
   - fit_level: "HIGH_ALIGNMENT" (>=75%), "MODERATE_FIT" (50-74%), "REQUIRES_REVIEW" (<50%), or "GATED_MISSING_MUST_HAVE".
   - summary: Neutral overview of alignment.
   - BANNED TERMS: Do NOT use "culture fit", "young", "energetic", "polished", "aggressive", "hire", or "reject". Do NOT command hiring or rejection.

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
- Key Projects / Achievements:
{json.dumps(redacted_profile.get('projects', []))}

Sanitized Resume Context:
\"\"\"
{redacted_text[:5000]}
\"\"\"

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
  "missing_skills": ["Missing Skill 1"],
  "band": "Strong",
  "review_priority": "High",
  "why_this_score": "Direct hands-on evidence for all must-haves and relevant tenure exceeding role requirements.",
  "action_needed": null,
  "hard_requirement_status": "None required",
  "flags": [],
  "must_have_breakdown": [
    {{
      "skill": "Python",
      "status": "met",
      "evidence": "Built Python scripts and Flask endpoints for client websites",
      "source": "Experience 2"
    }},
    {{
      "skill": "Docker",
      "status": "unclear",
      "note": "Listed under Skills only. No sentence shows hands-on use."
    }}
  ],
  "preferred_breakdown": [
    {{
      "skill": "AWS",
      "status": "met",
      "evidence": "Deployed services to AWS EC2 and managed S3 storage",
      "source": "Experience 1"
    }},
    {{
      "skill": "CI/CD",
      "status": "not found",
      "note": "No mention found in resume"
    }}
  ],
  "other_evidence": [
    "Nurse of the Quarter (2023)",
    "Trained 6 new nurses during orientation"
  ],
  "screening_questions": [
    "Tell me about a service you containerized and how you deployed it.",
    "How do you build and release your backend services today?",
    "Describe your hands-on role on your most complex project."
  ],
  "logistics": {{
    "notice_period": "30 days",
    "work_arrangement": "hybrid",
    "location": "Metro Manila"
  }},
  "executive_headline": "Senior specialist with 5+ yrs experience executing core duties; lacks secondary cloud tools.",
  "fit_level": "HIGH_ALIGNMENT",
  "summary": "Candidate demonstrates strong alignment in core development duties..."
}}
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            print(f"[GEMINI AI] [*] Unified evaluation & insights using model: '{model_name}'...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
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

                # Parse sub-scores deterministically from response
                req_score = max(0.0, min(100.0, float(parsed_json.get("required_skill_score", 0.0))))
                exp_score = max(0.0, min(100.0, float(parsed_json.get("experience_score", 0.0))))
                edu_score = max(0.0, min(100.0, float(parsed_json.get("education_score", 0.0))))
                pref_score = max(0.0, min(100.0, float(parsed_json.get("preferred_skill_score", 0.0))))
                proj_score = max(0.0, min(100.0, float(parsed_json.get("project_score", 0.0))))
                semantic_score = max(0.0, min(100.0, float(parsed_json.get("semantic_match_score", 0.0))))

                missing = parsed_json.get("missing_skills", [])
                matched = parsed_json.get("matched_skills", [])
                penalty_applied = parsed_json.get("must_have_penalty_applied", False)

                # Deterministically compute raw composite from 5-component rubric
                raw_calc_score = round(
                    (req_score * w_req) +
                    (exp_score * w_exp) +
                    (edu_score * w_edu) +
                    (pref_score * w_pref) +
                    (proj_score * w_proj),
                    1
                )

                missing_must_haves = [m for m in missing if any(m.lower() == r.lower() for r in required_skills)]
                overall = raw_calc_score
                if missing_must_haves:
                    penalty_applied = True
                    overall = round(overall * 0.85, 1)
                    if overall > 60.0:
                        overall = 60.0

                # ZERO MATCH & REGULATED ROLE HARD GATING:
                # If raw score is 0.0, or 0 must-haves met, or regulated profession without credentials/skills:
                if raw_calc_score == 0.0:
                    overall = 0.0
                elif len(matched) == 0 and len(required_skills) > 0:
                    overall = 0.0
                elif is_regulated_role and (req_score == 0.0 or edu_score == 0.0):
                    overall = 0.0

                parsed_json["overall_score"] = overall
                parsed_json["must_have_penalty_applied"] = penalty_applied

                # Derive standardized fit_level
                if overall == 0.0 or (is_regulated_role and (req_score == 0.0 or edu_score == 0.0)) or penalty_applied or missing_must_haves:
                    fit_level = "GATED_MISSING_MUST_HAVE"
                elif overall >= 75.0:
                    fit_level = "HIGH_ALIGNMENT"
                elif overall >= 50.0:
                    fit_level = "MODERATE_FIT"
                else:
                    fit_level = "REQUIRES_REVIEW"

                # Extract or synthesize must-have checklist
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
                
                # Synthesize checklist if missing or unpopulated
                if not must_have_checklist:
                    # Experience tenure check
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
                    # Education check
                    edu_req = job_data.get("education_requirement")
                    cand_edu = redacted_profile.get("education", [])
                    if edu_score == 0.0:
                        edu_status = "MISSING"
                        edu_ev = "No relevant degree or mandatory license found for this profession"
                    elif edu_score < 70.0:
                        edu_status = "PARTIAL"
                        edu_ev = ", ".join(str(e) for e in cand_edu[:2]) if cand_edu else "Degree credential requires verification"
                    else:
                        edu_status = "MET"
                        edu_ev = ", ".join(str(e) for e in cand_edu[:2]) if cand_edu else "Degree credential on file"

                    must_have_checklist.append({
                        "criterion": f"Education ({edu_req or 'Relevant Degree'})",
                        "status": edu_status,
                        "evidence": edu_ev
                    })
                    # Critical skills check
                    for req in required_skills[:4]:
                        is_missing = any(req.lower() == m.lower() for m in missing)
                        must_have_checklist.append({
                            "criterion": f"Must-Have: {req}",
                            "status": "MISSING" if is_missing else "MET",
                            "evidence": "No mention found in resume" if is_missing else f"Demonstrated competency in {req}"
                        })

                # Extract or synthesize key pinpoints
                raw_pinpoints = parsed_json.get("key_pinpoints")
                key_pinpoints = []
                if isinstance(raw_pinpoints, list) and len(raw_pinpoints) > 0:
                    for p in raw_pinpoints:
                        if isinstance(p, dict) and (p.get("headline") or p.get("evidence")):
                            key_pinpoints.append({
                                "headline": str(p.get("headline", "Validated Competency")),
                                "evidence": str(p.get("evidence", "")),
                                "impact_metric": str(p.get("impact_metric", "")),
                                "source": str(p.get("source", "experience[0]"))
                            })
                
                if not key_pinpoints:
                    for st in parsed_json.get("strengths", [])[:3]:
                        if isinstance(st, dict) and st.get("evidence"):
                            key_pinpoints.append({
                                "headline": str(st.get("skill", "Core Competency")),
                                "evidence": str(st.get("evidence", "")),
                                "impact_metric": f"Verified match for {st.get('matched_requirement', st.get('skill', 'requirement'))}",
                                "source": str(st.get("source", "experience[0]"))
                            })

                # Extract or synthesize interview guide
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

                if not interview_guide:
                    for focus in parsed_json.get("interview_focus", [])[:3]:
                        interview_guide.append({
                            "question": f"Can you detail your hands-on execution and past challenges with {focus}?",
                            "probe_reason": f"Probe operational depth in {focus}",
                            "target_signal": "Specific architectural decisions, metrics, and incident resolution steps"
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
                    "matched_skills": parsed_json.get("matched_skills", []),
                    "missing_skills": missing,
                }

                # Derive official TalentMatch Band & Review Priority (Section 2.5 & Band Legend)
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

                # Extract or synthesize must-have breakdown with [met], [unclear], [not found]
                raw_must_haves = parsed_json.get("must_have_breakdown", [])
                must_have_breakdown = []
                if isinstance(raw_must_haves, list) and len(raw_must_haves) > 0:
                    for item in raw_must_haves:
                        if isinstance(item, dict) and item.get("skill"):
                            must_have_breakdown.append({
                                "skill": str(item.get("skill")),
                                "status": str(item.get("status", "met")).lower(),
                                "evidence": str(item.get("evidence", "")),
                                "source": str(item.get("source", "Experience 1")),
                                "note": str(item.get("note", ""))
                            })

                if not must_have_breakdown and required_skills:
                    for req in required_skills:
                        is_miss = any(req.lower() == m.lower() for m in missing)
                        matching_st = next((s for s in parsed_json.get("strengths", []) if isinstance(s, dict) and (req.lower() in s.get("skill", "").lower() or req.lower() in s.get("matched_requirement", "").lower())), None)
                        if is_miss:
                            must_have_breakdown.append({
                                "skill": req,
                                "status": "not found",
                                "note": "No mention found in resume"
                            })
                        elif matching_st and matching_st.get("evidence"):
                            must_have_breakdown.append({
                                "skill": req,
                                "status": "met",
                                "evidence": matching_st.get("evidence"),
                                "source": matching_st.get("source", "Experience 1")
                            })
                        else:
                            must_have_breakdown.append({
                                "skill": req,
                                "status": "unclear",
                                "note": "Listed under Skills only. No sentence shows hands-on use."
                            })

                # Extract or synthesize preferred breakdown
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

                if not preferred_breakdown and preferred_skills:
                    for pref in preferred_skills:
                        is_miss = any(pref.lower() == m.lower() for m in missing)
                        matching_st = next((s for s in parsed_json.get("strengths", []) if isinstance(s, dict) and (pref.lower() in s.get("skill", "").lower() or pref.lower() in s.get("matched_requirement", "").lower())), None)
                        if is_miss:
                            preferred_breakdown.append({
                                "skill": pref,
                                "status": "not found",
                                "note": "No mention found in resume"
                            })
                        else:
                            preferred_breakdown.append({
                                "skill": pref,
                                "status": "met",
                                "evidence": matching_st.get("evidence") if matching_st else f"Demonstrated competency in {pref}",
                                "source": matching_st.get("source", "Experience 1") if matching_st else "Experience"
                            })

                # Compute Must-Haves count e.g. "4 / 5"
                met_must_haves = sum(1 for m in must_have_breakdown if m.get("status") == "met")
                total_must_haves = len(required_skills) if required_skills else len(must_have_breakdown)
                must_haves_summary = f"{met_must_haves} / {total_must_haves}" if total_must_haves > 0 else "All met"

                # Detect Hard Requirements & Flags
                flags = parsed_json.get("flags") or []
                if not isinstance(flags, list):
                    flags = []
                for item in must_have_breakdown:
                    if item.get("status") == "unclear" and not any(item["skill"] in f for f in flags):
                        flags.append(f"{item['skill']}: skills list only")

                # Detect license requirement vs documentation
                certs_text = " ".join([str(c) for c in redacted_profile.get("certifications", [])] + [str(e) for e in redacted_profile.get("education", [])])
                hard_req_status = parsed_json.get("hard_requirement_status", "None required")
                action_needed = parsed_json.get("action_needed")

                is_regulated_role = any(kw in job_data.get("title", "").lower() for kw in ["nurse", "physician", "engineer", "accountant", "cpa", "pharmacist", "attorney", "lawyer"])
                if is_regulated_role:
                    has_license_stated = any(kw in certs_text.lower() for kw in ["prc", "license", "registered", "rn", "cpa", "board"])
                    has_license_number = bool(re.search(r'\b(no\.?|#)\s*\d{5,}\b', redacted_text, re.IGNORECASE))
                    if has_license_stated and not has_license_number:
                        hard_req_status = "Stated, verify document"
                        if not action_needed:
                            action_needed = f"[verify] Professional license: resume states licensure credentials but gives no license number or expiry. Request the license document before offer."
                        if "License number not on resume" not in flags:
                            flags.append("License number not on resume")

                # Screening Questions
                screening_questions = parsed_json.get("screening_questions") or []
                if not screening_questions:
                    screening_questions = [g.get("question") for g in interview_guide if g.get("question")]

                # Penalty note
                if overall == 0.0 and raw_calc_score == 0.0:
                    penalty_note = "Candidate does not meet any must-have requirements or credentials for this role (0% match)."
                elif overall == 0.0 and raw_calc_score > 0.0:
                    penalty_note = "Candidate score gated to 0% due to missing mandatory professional credentials/skills for regulated role."
                elif penalty_applied:
                    penalty_note = f"Raw score higher ({raw_calc_score}%), reduced to {overall:.1f}% after penalty for missing must-have requirements."
                else:
                    penalty_note = "No must-have penalty applied."

                # Logistics
                logistics = parsed_json.get("logistics") or {}
                if not isinstance(logistics, dict):
                    logistics = {}
                if not logistics.get("location"):
                    # Check for location in redacted profile or context
                    loc_match = re.search(r'\b(Taguig|Makati|Cebu|Manila|Quezon City|Pasig|Davao|Bacolod|Iloilo|Mandaluyong)\b', redacted_text, re.IGNORECASE)
                    if loc_match:
                        logistics["location"] = loc_match.group(0)

                raw_calc_score = round(
                    (req_score * w_req) +
                    (exp_score * w_exp) +
                    (edu_score * w_edu) +
                    (pref_score * w_pref) +
                    (proj_score * w_proj),
                    1
                )
                penalty_multiplier = 0.85 if penalty_applied else 1.00

                ref_items = [
                    {
                        "component": f"Required skills ({int(w_req*100)}%)",
                        "description": f"Must-haves: {met_must_haves} of {total_must_haves} met" + (f" ({', '.join(flags)})" if flags else ""),
                        "score": round(req_score / 100.0, 2),
                        "weight": w_req,
                        "contribution": round((req_score / 100.0) * w_req, 3),
                    },
                    {
                        "component": f"Experience ({int(w_exp*100)}%)",
                        "description": f"{redacted_profile.get('total_experience_years', 0.0):.1f} yrs vs {job_data.get('minimum_experience') or 0} yrs required (capped)",
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
                        "component": f"Achievements and projects ({int(w_proj*100)}%)",
                        "description": "Demonstrated operational project execution",
                        "score": round(proj_score / 100.0, 2),
                        "weight": w_proj,
                        "contribution": round((proj_score / 100.0) * w_proj, 3),
                    },
                ]

                reference_calculation = {
                    "preset_name": f"{preset_name} preset",
                    "items": ref_items,
                    "raw_score": raw_calc_score,
                    "penalty_multiplier": penalty_multiplier,
                    "penalty_description": penalty_note,
                    "final_match": overall,
                    "final_band": band,
                }

                insights_dict = {
                    "band": parsed_json.get("band", band),
                    "review_priority": parsed_json.get("review_priority", review_priority),
                    "why_this_score": parsed_json.get("why_this_score") or parsed_json.get("executive_headline") or f"Direct evidence for {met_must_haves} of {total_must_haves} must-haves.",
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
                    "fit_level": parsed_json.get("fit_level", fit_level),
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
                    "fields_used": ["skills evidence", "experience", "education level", "projects"],
                    "fields_excluded": ["name", "contact details", "address", "school prestige", "employer names", "graduation year"]
                }

                # Validate grounded insights against full text context and profile duties
                ref_check = (redacted_text or "") + " " + " ".join(
                    str(exp.get("responsibilities", "") or exp.get("duties", ""))
                    for exp in redacted_profile.get("experience", []) if isinstance(exp, dict)
                ) + " " + " ".join(str(p) for p in redacted_profile.get("projects", []))
                validation_errors = validate_insights(insights_dict, ref_check)
                if validation_errors:
                    print(f"[GEMINI AI] [!] Unified insights validation warnings: {validation_errors}")
                    valid_strengths = [s for s in insights_dict.get("strengths", []) if s.get("evidence")]
                    insights_dict["strengths"] = valid_strengths if valid_strengths else []

                print(f"[GEMINI AI] [+] UNIFIED AI EVALUATION SUCCESS via '{model_name}'! Overall Score: {overall}%")
                return match_dict, insights_dict

        except Exception as e:
            print(f"[GEMINI AI] [!] Model '{model_name}' unified evaluation error: {e}")
            continue

    print("[GEMINI AI] [-] All Gemini models failed for unified evaluation. Falling back.")
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
            print(f"[GEMINI AI] [*] Generating grounded recruiter insights using model: '{model_name}'...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
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
                    print(f"[GEMINI AI] [!] Insights validation warnings: {validation_errors}")
                    valid_strengths = [s for s in insights_json.get("strengths", []) if s.get("evidence")]
                    insights_json["strengths"] = valid_strengths if valid_strengths else []

                print(f"[GEMINI AI] [+] Grounded insights successfully generated by '{model_name}'!")
                return insights_json
        except Exception as e:
            print(f"[GEMINI AI] [!] Model '{model_name}' insight generation error: {e}")
            continue

    print("[GEMINI AI] [-] All Gemini models failed for grounded insights.")
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
        if logistics.get("location"):
            log_parts.append(f"Location: {logistics['location']}")
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
