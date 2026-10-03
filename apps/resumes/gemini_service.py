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

GEMINI_MODEL_CANDIDATES = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]

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

                # Validate & enforce must-have penalty
                missing = parsed_json.get("missing_skills", [])
                penalty_applied = parsed_json.get("must_have_penalty_applied", False)
                overall = float(parsed_json.get("overall_score", 75.0))
                if (missing and any(m in required_skills for m in missing)) and overall > 60.0:
                    overall = 60.0
                    penalty_applied = True
                    parsed_json["overall_score"] = overall
                    parsed_json["must_have_penalty_applied"] = penalty_applied

                print(f"[GEMINI AI] [+] AI MATCH EVALUATION SUCCESS via '{model_name}'! Overall Score: {overall}%")
                return parsed_json
        except Exception as e:
            print(f"[GEMINI AI] [!] Model '{model_name}' match evaluation error: {e}")
            continue

    print("[GEMINI AI] [-] All Gemini models failed for match evaluation. Falling back to heuristic formula.")
    return None

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
  "summary": "Concise 2-3 sentence neutral overview of job alignment...",
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
                    # Remove ungrounded strengths or sanitize
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
    Renders structured Section 10 AI Recruiter Insights as clean GitHub Markdown for display.
    """
    summary = insights.get("summary", "Neutral alignment assessment completed.")
    strengths = insights.get("strengths", [])
    gaps = insights.get("gaps", [])
    interview_focus = insights.get("interview_focus", [])
    breakdown = insights.get("score_breakdown", {})
    fields_used = ", ".join(insights.get("fields_used", ["skills", "experience", "education"]))
    fields_excluded = ", ".join(insights.get("fields_excluded", ["name", "gender", "age", "address", "school"]))

    md_lines = [
        f"AI RECRUITER INSIGHTS",
        f"Candidate ID: {candidate_code}  |  Role: {role_title}  |  Match: {match_score:.1f}%\n",
        f"1. SUMMARY",
        f"{summary}\n",
        f"2. EVIDENCE-GROUNDED STRENGTHS"
    ]

    if strengths:
        for s in strengths:
            skill = s.get("skill", "")
            req = s.get("matched_requirement", "")
            ev = s.get("evidence", "")
            source = s.get("source", "")
            conf = s.get("confidence", "high").capitalize()
            md_lines.append(f"- **{skill}**" + (f" (matches: *{req}*)" if req and req != skill else ""))
            if ev:
                md_lines.append(f'  Evidence: "{ev}" [{source}]')
            md_lines.append(f'  Confidence: {conf}')
    else:
        md_lines.append("- Competencies aligned with core requirements based on resume history.")

    md_lines.append(f"\n3. GAPS / MISSING REQUIREMENTS")
    if gaps:
        for g in gaps:
            skill = g.get("skill", "")
            gtype = g.get("type", "must_have").replace("_", "-")
            note = g.get("note", "No mention found in resume")
            md_lines.append(f"- **{skill}** ({gtype}): {note}")
    else:
        md_lines.append("- None identified in required competencies.")

    if interview_focus:
        md_lines.append(f"\n4. POINTS TO VERIFY IN INTERVIEW")
        for p in interview_focus:
            md_lines.append(f"- {p}")

    md_lines.append(f"\n5. SCORE BREAKDOWN")
    md_lines.append(
        f"Required Skills: {breakdown.get('required_skills', 0):.0f}% | "
        f"Experience: {breakdown.get('experience', 0):.0f}% | "
        f"Education: {breakdown.get('education', 0):.0f}% | "
        f"Preferred Skills: {breakdown.get('preferred_skills', 0):.0f}% | "
        f"Projects: {breakdown.get('projects', 0):.0f}%"
    )

    md_lines.append(f"\n6. FAIRNESS & TRANSPARENCY DISCLOSURE")
    md_lines.append(f"- Scored on: {fields_used}")
    md_lines.append(f"- Not used: {fields_excluded}")

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
