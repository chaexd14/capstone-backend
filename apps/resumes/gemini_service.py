import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / '.env')

GEMINI_MODEL_CANDIDATES = ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-pro-latest"]

def get_gemini_client():
    load_dotenv(BASE_DIR / '.env', override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")
        return None

def analyze_resume_with_gemini(resume_text: str) -> dict | None:
    """
    Universal, zero-shot dynamic resume extraction across all job categories
    (Healthcare, Accounting, BPO, Sales, Engineering, Education, Hospitality, IT, etc.):
    - Competencies, hard skills, software, tools, instruments
    - Professional licenses & certifications (PRC, CPA, RN, Civil Service, TESDA, BOSH)
    - Work experiences with duties, company, and duration in months
    - Education degrees and academic credentials
    """
    client = get_gemini_client()
    if not client or not resume_text.strip():
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
  "skills": ["string (e.g. QuickBooks, IV Cannulation, AutoCAD, SEO, Python, Zendesk, Financial Auditing, Classroom Management)"],
  "skills_with_evidence": [
    {{"name": "string", "evidence": "string (exact sentence/context from resume)"}}
  ],
  "licenses_and_certifications": [
    "string (e.g. PRC Registered Nurse, Certified Public Accountant CPA, Civil Service Professional, TESDA NC II, BLS/ACLS, BOSH Safety Officer)"
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
  "education": ["string (e.g. BS Accountancy, BS Nursing, BS Civil Engineering, BS Information Technology)"],
  "is_fresh_graduate": true
}}
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
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
                return json.loads(cleaned.strip())
        except Exception as e:
            print(f"Gemini universal resume analysis error with {model_name}: {e}")
            continue

    return None

def generate_recruiter_insights_with_gemini(job_data: dict, candidate_data: dict, scores: dict) -> str | None:
    """
    Generate evidence-based recruiter insights tailored to any profession/industry.
    """
    client = get_gemini_client()
    if not client:
        return None

    prompt = f"""
Analyze this candidate's application against the job requirements.
Output your review directly without repeating these instructions or meta-headings.

Position Requirements:
- Title: {job_data.get('title')}
- Department / Industry: {job_data.get('department')}
- Required Competencies & Licenses: {', '.join(job_data.get('required_skills', []))}
- Preferred Competencies: {', '.join(job_data.get('preferred_skills', []))}
- Minimum Experience: {job_data.get('minimum_experience')}
- Education Requirement: {job_data.get('education_requirement')}

Candidate Profile:
- Candidate Code: {candidate_data.get('candidate_code')}
- Detected Skills: {', '.join(candidate_data.get('skills', []))}
- Detected Licenses / Certifications: {', '.join(candidate_data.get('licenses', []))}
- Extracted Work Experience: {candidate_data.get('experience_years')} year(s)
- Extracted Education: {', '.join(candidate_data.get('education', []))}
- Match Score: {scores.get('overall_score')}%

Format your response strictly using these 3 sections:
### Candidate Alignment & Strengths
(Provide 2-3 bullet points citing specific evidence from the resume)

### Gaps & Missing Requirements
(List missing licenses, tools, or experience deficits neutrally)

### Recruiter Summary
(A concise 1-2 sentence recommendation for the hiring team)
"""

    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="You are an explainable, objective AI recruitment screening assistant. Provide concise, grounded, and professional candidate assessments without echoing the prompt.",
                    temperature=0.2,
                    max_output_tokens=1500,
                )
            )
            return response.text.strip() if response.text else None
        except Exception as e:
            print(f"Gemini universal insight generation error with {model_name}: {e}")
            continue

    return None
