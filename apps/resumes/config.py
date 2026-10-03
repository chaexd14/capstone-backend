import os
import yaml
from pathlib import Path
from functools import lru_cache

CONFIG_PATH = Path(__file__).resolve().parent / "scoring_config.yaml"

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "taxonomy_version": "1.0.0",
    "models": {
        "embedding_model": "text-embedding-004",
        "verifier_model": "gemini-2.5-flash",
    },
    "rubric_weights": {
        "required_skills": 0.40,
        "experience": 0.25,
        "education": 0.15,
        "preferred_skills": 0.10,
        "projects": 0.10,
    },
    "evidence_weights": {
        "duty": 1.0,
        "project": 0.9,
        "certification": 0.9,
        "skills_list": 0.4,
        "vague_duty": 0.5,
    },
    "thresholds": {
        "missing_must_have_cutoff": 0.50,
        "retrieve_threshold": 0.50,
        "penalty_per_missing": 0.15,
        "penalty_floor": 0.55,
        "hard_cap_limit": 60.0,
    },
    "verdict_credit": {
        "direct": 1.0,
        "partial": 0.5,
        "none": 0.0,
    },
}

@lru_cache(maxsize=1)
def get_scoring_config() -> dict:
    """Loads and caches the versioned scoring configuration."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    # Merge with default config to ensure all keys exist
                    config = DEFAULT_CONFIG.copy()
                    config.update(loaded)
                    return config
        except Exception as e:
            print(f"[CONFIG] Warning: Failed to load scoring_config.yaml: {e}. Using defaults.")
    return DEFAULT_CONFIG

def get_config_version() -> str:
    """Returns the current scoring config version string."""
    return get_scoring_config().get("version", "1.0.0")

def get_rubric_weights() -> dict:
    """Returns the 5-component rubric weights."""
    return get_scoring_config().get("rubric_weights", DEFAULT_CONFIG["rubric_weights"])

def get_evidence_weights() -> dict:
    """Returns the evidence source tier weights."""
    return get_scoring_config().get("evidence_weights", DEFAULT_CONFIG["evidence_weights"])

def apply_penalty(raw_score: float, missing_must_haves_count: int, mode: str = "proportional") -> float:
    """
    Applies the penalty rule for missing must-have requirements.
    - proportional (Fix Plan Section 4 / Phase 6):
        multiplier = max(0.55, 1.0 - 0.15 * missing_must_haves_count)
        final_score = raw * multiplier
    - hard_cap:
        if missing_must_haves_count > 0 and raw_score > 60.0:
            final_score = 60.0
    """
    cfg = get_scoring_config()
    thresholds = cfg.get("thresholds", DEFAULT_CONFIG["thresholds"])

    if missing_must_haves_count <= 0:
        return round(raw_score, 1)

    if mode == "proportional":
        per_missing = thresholds.get("penalty_per_missing", 0.15)
        floor = thresholds.get("penalty_floor", 0.55)
        multiplier = max(floor, 1.0 - (per_missing * missing_must_haves_count))
        return round(raw_score * multiplier, 1)
    else:
        # hard_cap mode
        cap = thresholds.get("hard_cap_limit", 60.0)
        return round(min(raw_score, cap), 1)
