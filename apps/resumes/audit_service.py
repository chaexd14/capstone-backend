"""
Bias Audit and Fairness Testing Suite (Section 8 of TalentMatch Specification)
Provides:
1. Counterfactual Testing (demographic, school prestige, and address invariance)
2. Adverse Impact Ratio (Four-Fifths Rule / 80% rule)
3. Demographic Parity and Shortlisting Equality
"""

def counterfactual_gap(base_score: float, variant_scores: list[float]) -> float:
    """
    Computes maximum absolute difference between a base resume score
    and counterfactual variants (perturbed names, schools, genders).
    A bias-reduced scoring engine should yield a gap close to 0.0.
    """
    if not variant_scores:
        return 0.0
    return max(abs(score - base_score) for score in variant_scores)

def adverse_impact_ratio(selected_by_group: dict[str, int], total_by_group: dict[str, int]) -> dict:
    """
    Calculates the Adverse Impact Ratio (Four-Fifths Rule):
    The selection rate of any group should be at least 80% (0.80) of the highest group's rate.
    
    Returns:
    {
        "rates": {"group_a": 0.5, "group_b": 0.45},
        "benchmark_group": "group_a",
        "benchmark_rate": 0.5,
        "impact_ratios": {"group_a": 1.0, "group_b": 0.90},
        "adverse_impact_detected": False,
        "violating_groups": []
    }
    """
    rates = {}
    for group, total in total_by_group.items():
        if total <= 0:
            rates[group] = 0.0
        else:
            selected = selected_by_group.get(group, 0)
            rates[group] = selected / total

    if not rates:
        return {
            "rates": {},
            "benchmark_group": None,
            "benchmark_rate": 0.0,
            "impact_ratios": {},
            "adverse_impact_detected": False,
            "violating_groups": []
        }

    benchmark_group = max(rates, key=rates.get)
    benchmark_rate = rates[benchmark_group]

    impact_ratios = {}
    violating_groups = []

    for group, rate in rates.items():
        if benchmark_rate <= 0:
            ratio = 1.0
        else:
            ratio = round(rate / benchmark_rate, 3)
        impact_ratios[group] = ratio
        if ratio < 0.80:
            violating_groups.append(group)

    return {
        "rates": {g: round(r, 4) for g, r in rates.items()},
        "benchmark_group": benchmark_group,
        "benchmark_rate": round(benchmark_rate, 4),
        "impact_ratios": impact_ratios,
        "adverse_impact_detected": len(violating_groups) > 0,
        "violating_groups": violating_groups
    }

def run_counterfactual_audit_on_text(
    base_text: str,
    applicant_info: dict,
    variants: list[dict],
    eval_fn
) -> dict:
    """
    Evaluates demographic invariance across a list of counterfactual variants.
    Each variant can specify:
      - name / applicant_info overrides
      - school name replacements
      - gender/pronoun replacements
      - address replacements
    """
    base_result = eval_fn(base_text, applicant_info)
    base_score = base_result.get("match_score", 0.0)

    variant_results = []
    variant_scores = []

    for v in variants:
        v_info = {**applicant_info, **v.get("applicant_info", {})}
        v_text = v.get("text", base_text)

        v_result = eval_fn(v_text, v_info)
        v_score = v_result.get("match_score", 0.0)
        variant_scores.append(v_score)

        variant_results.append({
            "variant_label": v.get("label", "Variant"),
            "score": v_score,
            "score_gap": round(abs(v_score - base_score), 2)
        })

    max_gap = counterfactual_gap(base_score, variant_scores)

    return {
        "base_score": base_score,
        "max_counterfactual_gap": round(max_gap, 2),
        "is_invariant": max_gap <= 2.0,  # Strict threshold for demographic fairness
        "variants": variant_results
    }
