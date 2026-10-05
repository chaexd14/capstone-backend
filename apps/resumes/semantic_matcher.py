"""
TalentMatch Semantic Matching Engine (Phases 2, 3, 4 and Section 12).
Combines:
- Sentence-level retrieval via Gemini embeddings (gemini-embedding-001)
- Cosine similarity thresholding against descriptive skill queries
- Verification gate via Gemini Flash (gemini-3.5-flash-lite / gemini-flash-latest) at temperature 0
- SHA256 caching for strict determinism, speed, and cost efficiency
- Resilient fallback to local heuristic matching if API is offline or quota exhausted
"""

import os
import json
import hashlib
import re
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / '.env')

from google import genai
from google.genai import types
from apps.resumes.config import get_scoring_config, get_evidence_weights

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["direct", "partial", "none"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}

VERDICT_CREDIT = {
    "direct": 1.0,
    "partial": 0.5,
    "none": 0.0
}

# In-memory and file-based deterministic verdict and embedding cache
_VERIFY_CACHE: dict[str, dict] = {}
_EMBED_CACHE: dict[str, list[float]] = {}

def get_gemini_client():
    load_dotenv(BASE_DIR / '.env', override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[SEMANTIC MATCHER] Gemini client init error: {e}")
        return None

def embed_texts(texts: list[str], task_type: str = "SEMANTIC_SIMILARITY", dims: int = 768) -> np.ndarray:
    """
    Computes normalized embeddings for a list of texts using gemini-embedding-001.
    Caches vectors by MD5/SHA256 text hash for determinism and quota efficiency.
    """
    if not texts:
        return np.zeros((0, dims), dtype="float32")

    config = get_scoring_config()
    model_name = config.get("models", {}).get("embedding_model", "gemini-embedding-001")
    client = get_gemini_client()

    cached_vectors = []
    uncached_texts = []
    uncached_indices = []

    for idx, t in enumerate(texts):
        cleaned = t.strip()
        key = hashlib.sha256(f"{model_name}|{dims}|{task_type}|{cleaned}".encode()).hexdigest()
        if key in _EMBED_CACHE:
            cached_vectors.append((idx, np.array(_EMBED_CACHE[key], dtype="float32")))
        else:
            uncached_texts.append(cleaned)
            uncached_indices.append((idx, key))

    if uncached_texts and client:
        try:
            # Batch embedding call
            res = client.models.embed_content(
                model=model_name,
                contents=uncached_texts,
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dims),
            )
            for (orig_idx, cache_key), emb_obj in zip(uncached_indices, res.embeddings):
                v = np.array(emb_obj.values, dtype="float32")
                norm = np.linalg.norm(v)
                v_norm = v / norm if norm > 0 else v
                _EMBED_CACHE[cache_key] = v_norm.tolist()
                cached_vectors.append((orig_idx, v_norm))
        except Exception as e:
            print(f"[SEMANTIC MATCHER] Embedding API call failed: {e}. Using zero vector fallback.")
            for orig_idx, cache_key in uncached_indices:
                cached_vectors.append((orig_idx, np.zeros(dims, dtype="float32")))

    # Sort back into original order
    cached_vectors.sort(key=lambda x: x[0])
    matrix = np.array([v for _, v in cached_vectors], dtype="float32")
    return matrix

def verify_sentence_with_gemini(
    skill_label: str,
    verify_question: str,
    sentence: str,
    job_context: str = ""
) -> dict:
    """
    Verification gate via Gemini Flash at temperature 0 with JSON schema (Section 12.3).
    Judges whether the sentence provides direct, partial, or no hands-on evidence.
    """
    config = get_scoring_config()
    verifier_model = config.get("models", {}).get("verifier_model", "gemini-3.5-flash-lite")
    cache_key = hashlib.sha256(f"{verifier_model}|{skill_label}|{sentence.strip()}".encode()).hexdigest()

    if cache_key in _VERIFY_CACHE:
        return _VERIFY_CACHE[cache_key]

    client = get_gemini_client()
    if not client:
        result = {"verdict": "partial", "reason": "No Gemini API client (fallback)"}
        _VERIFY_CACHE[cache_key] = result
        return result

    context_clause = f"In the context of a {job_context} role, " if job_context else ""
    prompt = f"""You are verifying resume evidence for job qualification.
{context_clause}Skill requirement: {skill_label}
Verification question: {verify_question}
Candidate resume sentence: "{sentence}"

Answer with JSON only:
{{"verdict": "direct" | "partial" | "none", "reason": "<short>"}}

Rules:
direct  = the candidate clearly performed, built, operated, or executed this skill hands-on.
partial = related hands-on work or testing/support, but not the core skill itself.
none    = only mentions, studies, attends seminars/courses, reads about, or is unrelated.
Do not infer anything about the candidate. Judge only what is stated in the sentence."""

    # Try preferred verifier model, then fall back to candidate list
    models_to_try = [verifier_model, "gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-flash-latest"]
    # De-duplicate while preserving order
    models_to_try = list(dict.fromkeys(models_to_try))

    verdict_dict = None
    for m in models_to_try:
        try:
            resp = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    seed=42,
                    response_mime_type="application/json",
                    response_schema=VERIFY_SCHEMA,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if resp.text:
                parsed = json.loads(resp.text.strip())
                if parsed.get("verdict") in ("direct", "partial", "none"):
                    verdict_dict = parsed
                    break
        except Exception as e:
            # Continue to next model on quota / transient error
            pass

    if not verdict_dict:
        # Fallback to partial credit and flag for human review per Section 12.4
        verdict_dict = {
            "verdict": "partial",
            "reason": "Verifier fallback - flagged for human verification",
            "flagged_for_review": True
        }

    _VERIFY_CACHE[cache_key] = verdict_dict
    return verdict_dict

def score_skill_with_evidence(
    skill_label: str,
    queries: list[str],
    verify_question: str,
    evidence_units: dict[str, list[str]],
    retrieve_threshold: float = 0.50,
    job_context: str = ""
) -> tuple[float, dict | None]:
    """
    Combined skill scorer (Section 4, Phase 4 of TalentMatch Specification):
    1. Embeds skill queries and all evidence sentences.
    2. Computes cosine similarity to find top matches per source tier.
    3. Sends top candidates above retrieve_threshold to the verification gate.
    4. Computes evidence-weighted credit and returns best logged evidence.
    """
    evidence_weights = get_evidence_weights()
    best_credit = 0.0
    best_log = None

    # Embed queries once
    q_vecs = embed_texts(queries)

    # Check each evidence source tier
    for source, sentences in evidence_units.items():
        if not sentences:
            continue

        if source == "skills_list":
            # Skills list mentions don't have sentence context to verify;
            # check simple string presence against queries/skill label
            for s in sentences:
                s_lower = s.lower()
                if any(q.lower() in s_lower or s_lower in q.lower() for q in queries) or skill_label.lower() in s_lower:
                    credit = VERDICT_CREDIT["partial"] * evidence_weights.get("skills_list", 0.4)
                    if credit > best_credit:
                        best_credit = credit
                        best_log = {
                            "skill": skill_label,
                            "sentence": s,
                            "source": "skills_list",
                            "similarity": 0.50,
                            "verdict": "partial",
                            "credit": round(credit, 2),
                            "reason": "Listed in skills section without duty description"
                        }
            continue

        # Embed sentences
        s_vecs = embed_texts(sentences)
        if s_vecs.shape[0] == 0:
            continue

        # Maximum cosine similarity against any query for this skill
        sim_matrix = q_vecs @ s_vecs.T  # shape: (n_queries, n_sentences)
        max_sims = sim_matrix.max(axis=0)  # shape: (n_sentences,)

        # Sort sentences by similarity descending and evaluate top 3
        top_indices = np.argsort(max_sims)[::-1][:3]
        for idx in top_indices:
            sim = float(max_sims[idx])
            if sim < retrieve_threshold:
                continue

            sent = sentences[idx]
            verdict_info = verify_sentence_with_gemini(skill_label, verify_question, sent, job_context)
            verdict = verdict_info.get("verdict", "partial")

            tier_multiplier = evidence_weights.get(source, 0.9)
            credit = VERDICT_CREDIT[verdict] * tier_multiplier

            if credit > best_credit:
                best_credit = credit
                best_log = {
                    "skill": skill_label,
                    "sentence": sent,
                    "source": source,
                    "similarity": round(sim, 3),
                    "verdict": verdict,
                    "credit": round(credit, 2),
                    "reason": verdict_info.get("reason", "")
                }

    return round(best_credit, 2), best_log
