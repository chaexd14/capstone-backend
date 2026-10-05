import os
import re
import json
from google import genai
from google.genai import types
from apps.resumes.gemini_service import get_gemini_client, GEMINI_MODEL_CANDIDATES
from apps.resumes.job_profile import lint_job_description, JOB_FAMILY_PRESETS, get_job_family_preset

# Bias term replacements dictionary for 1-click sanitization
BIAS_TERM_REPLACEMENTS = {
    r"\bdigital\s+native\b": "tech-proficient professional",
    r"\brecent\s+grad(?:uate)?s?\b": "early-career candidates",
    r"\byoung\s+and\s+energetic\b": "motivated and collaborative",
    r"\byouthful\b": "innovative",
    r"\benergetic\s+team\b": "collaborative team",
    r"\bsalesman\b": "sales executive",
    r"\bwaitress\b": "food server",
    r"\bstewardess\b": "flight attendant",
    r"\bcraftsman\b": "skilled craftsperson",
    r"\bhe\s*\/\s*she\b": "they",
    r"\bmanpower\b": "workforce",
    r"\bninjas?\b": "specialist",
    r"\brockstars?\b": "high-performing professional",
    r"\bculture\s+fit\b": "values alignment",
    r"\bnative\s+(?:english\s+)?speaker\b": "fluent English communicator",
    r"\bpleasing\s+personality\b": "professional interpersonal demeanor",
    r"\bclean-shaven\b": "professional appearance",
}

# Domain-specific fallbacks if Gemini is offline
ROLE_FALLBACK_CATALOG = {
    "nurse": {
        "title": "Registered Staff Nurse (RN)",
        "department": "Healthcare & Medical",
        "employment_type": "Full-time / Shifting",
        "location": "Quezon City / Hospital On-site",
        "description": "• Deliver compassionate, patient-centered direct clinical nursing care in accordance with healthcare standards.\n• Administer prescribed oral and IV medications, monitor fluid balances, and document patient progress.\n• Regularly measure, record, and interpret patient vital signs, alerting attending physicians to acute clinical changes.\n• Coordinate with interdisciplinary healthcare teams to implement individualized nursing care plans.",
        "minimum_experience": "1-2 years",
        "education_requirement": "BS Nursing with active PRC Registered Nurse (RN) License",
        "required_skills": ["PRC Registered Nurse", "Patient Care", "IV Therapy", "Vital Signs Monitoring", "Medication Administration"],
        "preferred_skills": ["BLS/ACLS Certified", "ICU Care", "Electronic Health Records (EHR)", "Triage"],
        "job_family": "regulated_professional",
        "suggested_skills": ["Wound Dressing", "Catheterization", "Infection Control", "Emergency Response", "Patient Charting", "Clinical Assessment"],
    },
    "cpa": {
        "title": "Certified Public Accountant (CPA)",
        "department": "Finance & Accounting",
        "employment_type": "Full-time",
        "location": "Makati / Hybrid",
        "description": "• Perform general ledger reconciliations, monthly journal entries, and balance sheet variance analysis.\n• Oversee preparation and timely filing of BIR statutory tax returns (1601-C, 2550M/Q, 1702).\n• Prepare audit-ready financial statements in strict accordance with PFRS/IFRS accounting standards.\n• Coordinate external financial audit deliverables and maintain internal financial control documentation.",
        "minimum_experience": "2+ years",
        "education_requirement": "BS Accountancy with active PRC CPA License",
        "required_skills": ["PRC CPA", "Financial Reporting", "Tax Preparation", "General Ledger", "BIR Compliance"],
        "preferred_skills": ["QuickBooks", "SAP", "Auditing", "Xero", "Financial Modeling"],
        "job_family": "regulated_professional",
        "suggested_skills": ["Bank Reconciliation", "Payroll Accounting", "Variance Analysis", "Cash Flow Forecasting", "Budgeting", "Internal Controls"],
    },
    "civil engineer": {
        "title": "Civil Site Project Engineer",
        "department": "Engineering & Construction",
        "employment_type": "Full-time",
        "location": "Taguig (BGC) / Site-based",
        "description": "• Supervise structural and architectural construction activities on-site to ensure compliance with engineering specifications.\n• Inspect materials, structural framing, concrete pouring, and subcontractor deliverables for QA/QC compliance.\n• Review and verify 2D/3D civil blueprints, shop drawings, and structural calculations in AutoCAD.\n• Track daily project progress, manage site safety guidelines, and coordinate structural milestone billing.",
        "minimum_experience": "2-3 years",
        "education_requirement": "BS Civil Engineering with active PRC Civil Engineer License",
        "required_skills": ["PRC Civil Engineer", "AutoCAD", "Site Supervision", "Project Estimation", "QA/QC Inspection"],
        "preferred_skills": ["BOSH/COSH Certified", "STAAD Pro", "Revit", "MS Project"],
        "job_family": "regulated_professional",
        "suggested_skills": ["Concrete Testing", "Structural Analysis", "Bar Bending Schedules", "Cost Estimation", "Safety Compliance", "Subcontractor Management"],
    },
    "developer": {
        "title": "Full Stack Software Developer",
        "department": "Information Technology",
        "employment_type": "Full-time",
        "location": "Manila / Hybrid",
        "description": "• Design, develop, and maintain performant backend REST APIs and microservices using Python and Django.\n• Build interactive, accessible web user interfaces with React, TypeScript, and modern CSS frameworks.\n• Optimize relational database schemas, write complex PostgreSQL queries, and implement Redis caching.\n• Participate in code reviews, continuous integration/continuous delivery (CI/CD) pipelines, and Docker containerization.",
        "minimum_experience": "2+ years",
        "education_requirement": "BS Computer Science, Information Technology, or equivalent experience",
        "required_skills": ["Python", "Django", "PostgreSQL", "JavaScript", "React"],
        "preferred_skills": ["Docker", "Redis", "TypeScript", "Next.js", "AWS", "CI/CD"],
        "job_family": "professional_technical",
        "suggested_skills": ["REST API Design", "Git", "TailwindCSS", "Node.js", "Unit Testing", "Microservices Architecture"],
    },
    "customer": {
        "title": "Customer Support Specialist (BPO)",
        "department": "BPO & Customer Operations",
        "employment_type": "Full-time / Shifting",
        "location": "Pasig (Ortigas) / Hybrid",
        "description": "• Provide prompt, empathetic first-contact resolution for inbound customer inquiries via voice, email, and live chat.\n• Troubleshoot billing, account access, and service configuration issues using enterprise CRM software.\n• Maintain detailed documentation of customer interactions, bug reports, and escalation paths in Zendesk/Salesforce.\n• Consistently achieve team KPIs for Customer Satisfaction (CSAT), First Contact Resolution (FCR), and Average Handle Time (AHT).",
        "minimum_experience": "1+ year",
        "education_requirement": "College Graduate or Completed at least 2 years in College",
        "required_skills": ["Customer Service", "Inbound Voice Support", "English Fluency", "Troubleshooting", "Zendesk"],
        "preferred_skills": ["Salesforce", "Email Support", "Live Chat", "Conflict Resolution"],
        "job_family": "retail_service",
        "suggested_skills": ["Ticket Escalation", "CRM", "Active Listening", "Call Center Operations", "Omnichannel Support", "Order Tracking"],
    },
}

def clean_biased_text(text: str) -> str:
    """Replaces biased, non-job-related, or hyper-masculine terms with neutral equivalents."""
    cleaned = text
    for pattern, replacement in BIAS_TERM_REPLACEMENTS.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned

def detect_is_regulated_role(title: str, department: str = "", description: str = "") -> bool:
    """Checks if a position belongs to a legally regulated, licensed profession in the Philippines."""
    combined = f"{title} {department} {description}".lower()
    regulated_patterns = [
        r'\b(?:nurse|nursing|physician|doctor|medical|healthcare|hospital)\b',
        r'\b(?:pharmacist|pharmacy|accountant|cpa|attorney|lawyer|teacher|lpt|architect)\b',
        r'\b(?:medical\s+technologist|rmt|radiologic\s+technologist)\b',
        r'\b(?:civil|mechanical|electrical|electronics|ece|chemical|geodetic|sanitary)\s+(?:\w+\s+)*engineer\b',
        r'\bprc\b',
    ]
    return any(re.search(pat, combined) for pat in regulated_patterns)

def lint_job_requisition(title: str, description: str, required_skills: list[str] | None = None, education: str = "") -> dict:
    """
    Performs real-time Section 13.11 linting:
    - Scans for age bias, gender bias, and non-job-related subjective criteria
    - Verifies mandatory licensure for regulated roles
    - Offers 1-click text sanitation
    """
    required_skills = required_skills or []
    flags = lint_job_description(description)

    # Check for regulated profession license omission
    is_regulated = detect_is_regulated_role(title, description=description)
    has_license = any(
        re.search(r'\b(prc|license|certified|cpa|rn|lpt|registered)\b', s, re.IGNORECASE)
        for s in (required_skills + [education])
    )

    if is_regulated and not has_license:
        flags.append({
            "category": "missing_regulatory_license",
            "matched_terms": ["Regulated Profession without mandatory license specified"],
            "message": f"'{title}' is a regulated profession requiring state certification. Recommend adding the mandatory PRC license to 'Required Competencies' to activate the 0.0% hard-gating protection for non-licensed applicants.",
            "severity": "warning"
        })

    cleaned_desc = clean_biased_text(description)

    return {
        "flags": flags,
        "is_clean": len(flags) == 0,
        "is_regulated": is_regulated,
        "cleaned_description": cleaned_desc,
        "has_replacements": cleaned_desc != description
    }

def generate_job_requisition_with_gemini(title: str, department: str = "") -> dict:
    """
    AI-powered Job Requisition Auto-Drafter.
    Synthesizes:
    - Objective, duty-grounded job description
    - Standardized minimum experience tenure
    - Rigorous education & licensure requirement
    - Must-have competencies & licenses (dynamic tags)
    - Preferred tools & bonus certifications
    - Suggested related skills cloud
    - Recommended scoring rubric preset & weights
    """
    title_clean = title.strip()
    dept_clean = department.strip()
    
    is_regulated = detect_is_regulated_role(title_clean, dept_clean)
    job_family = "regulated_professional" if is_regulated else "professional_technical"

    client = get_gemini_client()
    if client:
        prompt = f"""You are the TalentMatch Job Calibration Copilot.
Create a high-precision, objective, bias-free job benchmark requisition for:
Job Title: "{title_clean}"
Department / Industry: "{dept_clean if dept_clean else 'Infer based on title'}"

Guidelines:
1. "description": Provide 3-5 concrete, duty-oriented bullet points outlining daily responsibilities with active verbs (e.g. "Administer...", "Design...", "Inspect..."). Avoid corporate buzzwords or subjective requirements like "culture fit" or "energetic".
2. "minimum_experience": Standardized tenure string (e.g. "1-2 years", "2+ years", "3-5 years", "Fresh Graduate / 0-1 year").
3. "education_requirement": Clear degree level, academic discipline, and required professional license (e.g., "BS Nursing with active PRC Registered Nurse License", "BS Computer Science or related degree").
4. "required_skills": 4 to 6 non-negotiable competencies. If the position is a regulated profession (Nursing, CPA, Engineering, Teaching, Pharmacy, Medicine, etc.), MUST include the official state license (e.g. "PRC Registered Nurse", "PRC CPA", "PRC Civil Engineer").
5. "preferred_skills": 3 to 5 nice-to-have tools, secondary certifications, or advanced frameworks.
6. "suggested_skills": 6 additional relevant tools/skills to display as quick-add chips for the recruiter.
7. "employment_type": Default to standard (e.g. "Full-time", "Full-time / Shifting").
8. "location": Default to practical Philippine/Remote setup (e.g. "Manila / Hybrid", "Quezon City / Hospital On-site", "Remote").
9. "job_family": Choose strictly one from: "regulated_professional", "professional_technical", "skilled_trades", "retail_service".

Return strictly a valid JSON object matching this schema:
{{
  "title": "{title_clean}",
  "department": "Department Name",
  "employment_type": "Full-time",
  "location": "Manila / Hybrid",
  "description": "• Responsibility 1\\n• Responsibility 2\\n• Responsibility 3",
  "minimum_experience": "2+ years",
  "education_requirement": "BS ...",
  "required_skills": ["Skill 1", "Skill 2", "Skill 3", "Skill 4"],
  "preferred_skills": ["Bonus 1", "Bonus 2", "Bonus 3"],
  "suggested_skills": ["Extra 1", "Extra 2", "Extra 3", "Extra 4", "Extra 5", "Extra 6"],
  "job_family": "{job_family}"
}}
"""
        for model_name in GEMINI_MODEL_CANDIDATES:
            try:
                print(f"[JOB COPILOT] [*] Drafting job requisition for '{title_clean}' using {model_name}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    )
                )
                if response.text:
                    cleaned = response.text.strip()
                    if cleaned.startswith("```json"): cleaned = cleaned[7:]
                    if cleaned.startswith("```"): cleaned = cleaned[3:]
                    if cleaned.endswith("```"): cleaned = cleaned[:-3]
                    data = json.loads(cleaned.strip())

                    # Enrich with rubric weights
                    detected_family = data.get("job_family", job_family)
                    preset = get_job_family_preset(detected_family)
                    data["weights"] = preset["weights"]
                    data["is_regulated"] = is_regulated or detected_family == "regulated_professional"
                    return data
            except Exception as e:
                print(f"[JOB COPILOT] [!] Model '{model_name}' failed: {e}. Trying fallback...")

    # Offline Fallback Matching
    print(f"[JOB COPILOT] [INFO] Using deterministic fallback catalog for '{title_clean}'")
    title_lower = title_clean.lower()
    for key, template in ROLE_FALLBACK_CATALOG.items():
        if key in title_lower:
            res = template.copy()
            res["title"] = title_clean
            if dept_clean: res["department"] = dept_clean
            preset = get_job_family_preset(res["job_family"])
            res["weights"] = preset["weights"]
            res["is_regulated"] = is_regulated or res["job_family"] == "regulated_professional"
            return res

    # Generic Smart Default
    preset = get_job_family_preset(job_family)
    return {
        "title": title_clean,
        "department": dept_clean or "Operations",
        "employment_type": "Full-time",
        "location": "Manila / Hybrid",
        "description": f"• Execute core operational workflows and responsibilities related to {title_clean}.\n• Collaborate across departmental stakeholders to achieve project and business objectives.\n• Ensure high quality standards and adherence to organizational policies and protocols.",
        "minimum_experience": "2+ years",
        "education_requirement": "Bachelor's degree in a relevant field or equivalent experience",
        "required_skills": ["Project Management", "Process Optimization", "Cross-Functional Collaboration"],
        "preferred_skills": ["Data Analysis", "Reporting", "ERP Systems"],
        "suggested_skills": ["Communication", "Stakeholder Management", "Process Documentation", "Quality Control"],
        "job_family": job_family,
        "weights": preset["weights"],
        "is_regulated": is_regulated
    }
