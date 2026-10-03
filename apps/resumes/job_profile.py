"""
TalentMatch Domain-General Job Profile Engine (Section 13).
Supports any job market (Healthcare, Finance, Education, Trades, Retail, IT).
Includes:
- Job family rubric weight presets
- Job family evidence tier presets
- Hard requirement evaluation with human-review flagging (never auto-reject)
- Dynamic skill descriptor generator via Gemini for any occupation
- Job Description (JD) bias and non-job-related criteria linter
"""

import os
import re
import json
from datetime import datetime
from typing import Any
from apps.resumes.config import get_scoring_config

JOB_FAMILY_PRESETS = {
    "professional_technical": {
        "label": "Professional / Technical (IT, Engineering, Marketing)",
        "weights": {
            "required_skills": 0.40,
            "experience": 0.25,
            "education": 0.15,
            "preferred_skills": 0.10,
            "achievements": 0.10,
        },
        "evidence_weights": {
            "duty": 1.0,
            "project": 0.9,
            "certification": 0.9,
            "training": 0.6,
            "skills_list": 0.4,
            "vague_duty": 0.5,
        }
    },
    "regulated_professional": {
        "label": "Regulated Professional (Healthcare, Nursing, Accounting, Law)",
        "weights": {
            "required_skills": 0.30,
            "experience": 0.25,
            "education": 0.25,
            "preferred_skills": 0.10,
            "achievements": 0.10,
        },
        "evidence_weights": {
            "duty": 1.0,
            "project": 0.9,
            "certification": 1.0,  # Formal license has full duty weight
            "training": 0.7,
            "skills_list": 0.4,
            "vague_duty": 0.5,
        }
    },
    "skilled_trades": {
        "label": "Skilled Trades and Operations (Electrician, Mechanics, Logistics)",
        "weights": {
            "required_skills": 0.35,
            "experience": 0.35,
            "education": 0.20,
            "preferred_skills": 0.10,
            "achievements": 0.00,  # Redistributed to experience and skills
        },
        "evidence_weights": {
            "duty": 1.0,
            "project": 0.9,
            "certification": 1.0,  # Trade license / NC certificate
            "training": 0.8,
            "skills_list": 0.4,
            "vague_duty": 0.5,
        }
    },
    "retail_service": {
        "label": "Retail, Hospitality, Customer Service",
        "weights": {
            "required_skills": 0.35,
            "experience": 0.30,
            "education": 0.05,     # Education is kept small; no degree penalty
            "preferred_skills": 0.20,
            "achievements": 0.10,
        },
        "evidence_weights": {
            "duty": 1.0,
            "project": 0.9,
            "certification": 0.9,
            "training": 0.7,
            "skills_list": 0.4,
            "vague_duty": 0.5,
        }
    }
}

# 13.11 JD Linter: Flagged biased and non-job-related keywords
JD_LINTER_RULES = [
    {
        "category": "age_bias",
        "patterns": [r"\bdigital\s+native\b", r"\brecent\s+grad(?:uate)?s?\b", r"\byoung\s+and\s+energetic\b", r"\byouthful\b", r"\benergetic\s+team\b"],
        "message": "Potential age-coded phrasing. Focus on demonstrated competency rather than age or graduation recency."
    },
    {
        "category": "gender_bias",
        "patterns": [r"\bsalesman\b", r"\bwaitress\b", r"\bstewardess\b", r"\bcraftsman\b", r"\bhe\s*\/\s*she\b", r"\bmanpower\b", r"\bninjas?\b", r"\brockstars?\b"],
        "message": "Gender-coded or hyper-masculine role terminology. Use neutral occupational terms (e.g., salesperson, server, flight attendant, technician)."
    },
    {
        "category": "non_job_related",
        "patterns": [r"\bculture\s+fit\b", r"\bnative\s+(?:english\s+)?speaker\b", r"\bpleasing\s+personality\b", r"\bmarital\s+status\b", r"\bphoto\s+attached\b", r"\bclean-shaven\b"],
        "message": "Non-job-related subjective requirement flagged. Evaluate only bona fide occupational qualifications."
    }
]

def lint_job_description(jd_text: str) -> list[dict]:
    """
    Scans a job description for age-coded, gender-coded, and non-job-related subjective criteria (Section 13.11).
    Returns list of warnings without blocking creation.
    """
    flags = []
    jd_lower = jd_text.lower()

    for rule in JD_LINTER_RULES:
        for pat in rule["patterns"]:
            matches = list(re.finditer(pat, jd_lower))
            if matches:
                matched_terms = [m.group(0) for m in matches]
                flags.append({
                    "category": rule["category"],
                    "matched_terms": list(set(matched_terms)),
                    "message": rule["message"],
                    "severity": "warning"
                })
                break

    return flags

def evaluate_hard_requirements(
    hard_requirements: list[dict],
    resume_text: str,
    candidate_licenses: list[str]
) -> list[dict]:
    """
    Section 13.3: Hard requirements versus scored skills.
    Hard requirements (licenses, mandatory certifications) are never auto-rejected.
    - Met if evidence found.
    - Flagged for human verification if not explicitly found.
    """
    results = []
    combined_text = (resume_text + " " + " ".join(candidate_licenses)).lower()

    for req in hard_requirements:
        req_name = req.get("name", "")
        req_type = req.get("type", "license")
        keywords = req.get("keywords", [req_name.lower()])

        is_met = any(re.search(r'\b' + re.escape(k.lower()) + r'\b', combined_text) for k in keywords)

        results.append({
            "requirement": req_name,
            "type": req_type,
            "status": "MET" if is_met else "FLAGGED_FOR_HUMAN_REVIEW",
            "action": "Verified via resume credentials" if is_met else "Flagged for manual recruiter verification (not auto-rejected)"
        })

    return results

def get_job_family_preset(job_family: str) -> dict:
    """Returns the rubric weights and evidence multipliers for the given job family."""
    if job_family in JOB_FAMILY_PRESETS:
        return JOB_FAMILY_PRESETS[job_family]
    return JOB_FAMILY_PRESETS["professional_technical"]

class JobProfile:
    """
    Domain-Agnostic Job Profile (Section 13.1).
    Encapsulates all role-specific criteria, weights, hard requirements, and locked descriptors.
    """
    def __init__(
        self,
        profile_id: str,
        title: str,
        job_family: str = "professional_technical",
        must_have_skills: list[str] | None = None,
        preferred_skills: list[str] | None = None,
        hard_requirements: list[dict] | None = None,
        required_experience_years: float = 2.0,
        education_requirement: str = "Bachelor's degree",
        weights_override: dict | None = None,
        version: int = 1
    ):
        self.profile_id = profile_id
        self.title = title
        self.job_family = job_family
        self.must_have_skills = must_have_skills or []
        self.preferred_skills = preferred_skills or []
        self.hard_requirements = hard_requirements or []
        self.required_experience_years = required_experience_years
        self.education_requirement = education_requirement
        self.version = version
        self.locked_at = datetime.utcnow().isoformat() + "Z"

        preset = get_job_family_preset(job_family)
        self.weights = weights_override or preset["weights"].copy()
        self.evidence_weights = preset["evidence_weights"].copy()

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "title": self.title,
            "job_family": self.job_family,
            "must_have_skills": self.must_have_skills,
            "preferred_skills": self.preferred_skills,
            "hard_requirements": self.hard_requirements,
            "required_experience_years": self.required_experience_years,
            "education_requirement": self.education_requirement,
            "weights": self.weights,
            "evidence_weights": self.evidence_weights,
            "version": self.version,
            "locked_at": self.locked_at,
        }

def calculate_relevant_experience_years(
    experience_text: str,
    duty_descriptors: list[str],
    total_years: float
) -> float:
    """
    Computes relevant experience years based on duty similarity to job profile duty descriptors (Section 13.6).
    - Unrelated occupations receive 0 credit.
    - Transferable experience receives proportional partial credit.
    - Directly relevant experience receives full credit (1.0 multiplier).
    """
    if total_years <= 0.0 or not experience_text.strip() or not duty_descriptors:
        return total_years

    exp_lower = experience_text.lower()
    matched_descriptors = 0
    for desc in duty_descriptors:
        desc_words = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', desc.lower())]
        if any(w in exp_lower for w in desc_words):
            matched_descriptors += 1

    if matched_descriptors == 0:
        return 0.0

    relevance_ratio = min(matched_descriptors / max(len(duty_descriptors), 1), 1.0)
    if relevance_ratio >= 0.5:
        return total_years
    else:
        return round(total_years * relevance_ratio, 1)

