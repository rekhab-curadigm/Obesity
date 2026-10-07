"""Draft obesity target prioritization helpers."""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_PRIORITY_WEIGHTS = {
    "human_genetic": 0.25,
    "biological_mechanistic": 0.20,
    "disease_expression": 0.15,
    "translational_clinical": 0.15,
    "druggability": 0.15,
    "safety": 0.10,
}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"not available", "not assessed", "requires review", "na"}:
            return None
        cleaned = cleaned.replace("%", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evidence_strength_from_score(score: float | None) -> str:
    if score is None:
        return "Insufficient evidence"
    numeric = float(score)
    if numeric >= 0.75:
        return "High"
    if numeric >= 0.45:
        return "Moderate"
    if numeric >= 0.20:
        return "Low"
    return "Insufficient evidence"


def novelty_category_from_class(provisional_class: Any) -> str:
    class_name = str(provisional_class or "").strip().lower()
    if "known" in class_name or "established" in class_name:
        return "Established"
    if "novel" in class_name:
        return "Potentially novel"
    if "emerging" in class_name:
        return "Emerging"
    return "Potentially novel"


def therapeutic_opportunity_from_score(score: float | None) -> str:
    if score is None:
        return "Not available"
    if score >= 70:
        return "High"
    if score >= 45:
        return "Moderate"
    if score >= 20:
        return "Low"
    return "Very low"


def priority_category_from_score(score: float | None) -> str:
    if score is None:
        return "Tier 4 – Evidence insufficient"
    if score >= 75:
        return "Tier 1 – High-priority target"
    if score >= 55:
        return "Tier 2 – Promising target"
    if score >= 35:
        return "Tier 3 – Emerging target"
    return "Tier 4 – Evidence insufficient"


def summarize_target_priority(
    gene: str,
    gene_row: Mapping[str, Any],
    assessment: Mapping[str, Any] | Any,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    weight_map = dict(DEFAULT_PRIORITY_WEIGHTS if weights is None else weights)
    biological_scores = [
        score
        for score in (
            _coerce_float(gene_row.get("Literature")),
            _coerce_float(gene_row.get("Animal model")),
        )
        if score is not None
    ]
    normalized: dict[str, float | None] = {
        "human_genetic": _coerce_float(gene_row.get("Genetic association")),
        "biological_mechanistic": (
            sum(biological_scores) / len(biological_scores)
            if biological_scores
            else None
        ),
        "disease_expression": _coerce_float(
            gene_row.get("Disease/tissue expression")
            if gene_row.get("Disease/tissue expression") not in (None, "")
            else gene_row.get("Expression")
        ),
        "translational_clinical": _coerce_float(
            gene_row.get("Clinical evidence")
            if gene_row.get("Clinical evidence") is not None
            else gene_row.get("Clinical development status")
        ),
        "druggability": _coerce_float(gene_row.get("Known drug")),
        "safety": _coerce_float(gene_row.get("Safety evidence")),
    }

    if isinstance(assessment, Mapping):
        clinical_signal = assessment.get("clinical_signal")
        if clinical_signal is not None:
            normalized["translational_clinical"] = _coerce_float(clinical_signal)
        safety_signal = assessment.get("safety_signal")
        if safety_signal is not None:
            normalized["safety"] = _coerce_float(safety_signal)
    else:
        metadata = getattr(assessment, "metadata", {}) or {}
        clinical_signal = metadata.get("clinical_signal")
        if clinical_signal is not None:
            normalized["translational_clinical"] = _coerce_float(clinical_signal)
        safety_signal = metadata.get("safety_signal")
        if safety_signal is not None:
            normalized["safety"] = _coerce_float(safety_signal)

    available = {
        key: value for key, value in normalized.items() if value is not None
    }
    total_weight = sum(weight_map.get(key, 0.0) for key in available)
    score = (
        sum((value * weight_map.get(key, 0.0)) for key, value in available.items()) / total_weight
        if total_weight
        else None
    )
    if score is not None:
        score = round(score * 100, 1)

    evidence_strength = evidence_strength_from_score(
        max(
            (value or 0.0) for value in available.values()
        ) if available else None
    )
    target_novelty = novelty_category_from_class(gene_row.get("Provisional class"))
    therapeutic_opportunity = therapeutic_opportunity_from_score(score)
    evidence_gaps: list[str] = []
    if normalized["human_genetic"] is None or normalized["human_genetic"] < 0.35:
        evidence_gaps.append("Human genetic evidence: Not available or weak in the current source.")
    if normalized["biological_mechanistic"] is None or normalized["biological_mechanistic"] < 0.35:
        evidence_gaps.append("Mechanistic evidence: Not available or requires review.")
    if normalized["disease_expression"] is None or normalized["disease_expression"] < 0.35:
        evidence_gaps.append("Disease/tissue expression: Not available.")
    if normalized["translational_clinical"] is None or normalized["translational_clinical"] < 0.35:
        evidence_gaps.append("Clinical/translational evidence: Not available.")
    if normalized["druggability"] is None or normalized["druggability"] < 0.35:
        evidence_gaps.append("Druggability assessment: Not available in the currently loaded source.")
    if normalized["safety"] is None or normalized["safety"] < 0.35:
        evidence_gaps.append("Safety evidence: Not available; requires review.")
    if not evidence_gaps:
        evidence_gaps.append("No major evidence gaps identified in the current loaded data.")

    return {
        "gene": gene,
        "evidence_strength": evidence_strength,
        "target_novelty": target_novelty,
        "therapeutic_opportunity": therapeutic_opportunity,
        "target_priority_score": score,
        "priority_category": priority_category_from_score(score),
        "component_scores": {key: round((value or 0.0) * 100, 1) if value is not None else None for key, value in normalized.items()},
        "evidence_gaps": evidence_gaps,
    }


def rank_emerging_novel_targets(
    gene_rows: list[Mapping[str, Any]],
    assessments: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    component_labels = {
        "human_genetic": "Human genetics",
        "biological_mechanistic": "Biology / mechanism",
        "disease_expression": "Disease / tissue expression",
        "translational_clinical": "Clinical / translational",
        "druggability": "Druggability",
        "safety": "Safety",
    }
    ranked: list[dict[str, Any]] = []
    for gene_row in gene_rows:
        provisional_class = str(gene_row.get("Provisional class", "")).strip()
        if provisional_class not in {"Emerging", "Novel"}:
            continue

        gene = str(gene_row.get("Gene", "")).strip()
        assessment = assessments.get(gene, {})
        summary = summarize_target_priority(gene, gene_row, assessment, weights)
        component_scores = summary["component_scores"]
        available_categories = [
            label
            for key, label in component_labels.items()
            if component_scores[key] is not None
        ]
        ranked.append({
            "Rank": 0,
            "Gene": gene,
            "Gene name": str(gene_row.get("Gene name", "")),
            "Provisional class": provisional_class,
            "Target Priority Score": summary["target_priority_score"],
            "Priority category": summary["priority_category"],
            "Available score categories": len(available_categories),
            "Evidence categories scored": ", ".join(available_categories) or "None",
            "Human genetics score": component_scores["human_genetic"],
            "Biology / mechanism score": component_scores["biological_mechanistic"],
            "Disease / tissue expression score": component_scores["disease_expression"],
            "Clinical / translational score": component_scores["translational_clinical"],
            "Druggability score": component_scores["druggability"],
            "Safety score": component_scores["safety"],
            "Major evidence gap": summary["evidence_gaps"][0],
        })

    ranked.sort(
        key=lambda row: (
            row["Target Priority Score"] is None,
            -(row["Target Priority Score"] or 0),
            -row["Available score categories"],
            row["Gene"],
        )
    )
    for rank, row in enumerate(ranked, start=1):
        row["Rank"] = rank
    return ranked
