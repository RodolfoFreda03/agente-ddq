"""The routing formula: Confidence (C), Risk (R), priority (P) and quadrant routing.

No API calls here (CLAUDE.md milestone 1): this module is pure arithmetic over
already-known factor values (c1-c4, r1-r4). Factors that require an API call
(c2 source coverage, the general case of c4 consistency, r1-r3 classification)
are computed elsewhere (memory.py, drafter.py) and passed in here. c3
(freshness) needs no API call — it is pure date arithmetic — so it lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

Route = str  # "AUTO" | "DRAFT" | "GAP" | "HUMAN_ONLY"

AUTO = "AUTO"
DRAFT = "DRAFT"
GAP = "GAP"
HUMAN_ONLY = "HUMAN_ONLY"


def load_config(path: str | Path = "config.yaml") -> dict:
    """Loads config.yaml (section 4)."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_confidence(c1: float, c2: float, c3: float, c4: float, weights: dict) -> float:
    """C = sum(weight * factor). weights keys: c1_similarity, c2_source_coverage,
    c3_freshness, c4_consistency (section 5.1)."""
    return (
        weights["c1_similarity"] * c1
        + weights["c2_source_coverage"] * c2
        + weights["c3_freshness"] * c3
        + weights["c4_consistency"] * c4
    )


def compute_risk(r1: float, r2: float, r3: float, r4: float, weights: dict) -> float:
    """R = sum(weight * factor). weights keys: r1_category, r2_figures_commitments,
    r3_forward_looking, r4_investor (section 5.2)."""
    return (
        weights["r1_category"] * r1
        + weights["r2_figures_commitments"] * r2
        + weights["r3_forward_looking"] * r3
        + weights["r4_investor"] * r4
    )


def compute_priority(risk: float, confidence: float) -> float:
    """P = R x (1 - C): priority index for the human review queue (section 5.3)."""
    return risk * (1 - confidence)


def compute_c3_freshness(
    source_dates: list[date],
    category: str,
    freshness_days: dict,
    as_of_date: Optional[date] = None,
) -> float:
    """1 if the freshest source is within freshness_days[category] (or
    'default'), 0.5 if within twice that, 0 beyond. No sources = 0 (section 5.1)."""
    if not source_dates:
        return 0.0
    today = as_of_date or date.today()
    threshold = freshness_days.get(category, freshness_days["default"])
    age_days = (today - max(source_dates)).days
    if age_days <= threshold:
        return 1.0
    if age_days <= threshold * 2:
        return 0.5
    return 0.0


def resolve_confidence_threshold(
    category: str,
    global_threshold: float,
    compliance_floor: float,
    category_thresholds: Optional[dict] = None,
) -> float:
    """The confidence threshold used for quadrant routing: the per-category
    override from category_thresholds.yaml if present, otherwise the global
    threshold from config.yaml. Never allowed below compliance_floor."""
    threshold = global_threshold
    if category_thresholds and category in category_thresholds:
        threshold = category_thresholds[category]
    return max(threshold, compliance_floor)


def classify_quadrant(
    confidence: float,
    risk: float,
    confidence_threshold: float,
    risk_threshold: float,
) -> Route:
    """The 2x2 routing table (section 5.3)."""
    if confidence >= confidence_threshold and risk <= risk_threshold:
        return AUTO
    if confidence >= confidence_threshold and risk > risk_threshold:
        return DRAFT
    if confidence < confidence_threshold and risk <= risk_threshold:
        return GAP
    return HUMAN_ONLY


def compute_preliminary_confidence(c1: float, c3: float, c4: float, weights: dict) -> float:
    """Pass 1 of the two-pass confidence computation (section 5.1): C from
    c1, c3, c4 only, with their weights renormalised to sum to 1 -- c2
    (source coverage) is not available yet because no draft exists."""
    w1, w3, w4 = weights["c1_similarity"], weights["c3_freshness"], weights["c4_consistency"]
    total = w1 + w3 + w4
    return (w1 * c1 + w3 * c3 + w4 * c4) / total


# Best (most autonomous) to worst, matching the quadrant table's structure.
ROUTE_ORDER: list[Route] = [AUTO, DRAFT, GAP, HUMAN_ONLY]


def clamp_route_to_at_most(preliminary_route: Route, final_route: Route) -> Route:
    """Section 5.1, pass 2: 'if the final C falls below the threshold, the
    question moves down a route, never up'. If recomputing with c2 would
    make the route *more* autonomous than the preliminary one, the
    preliminary route wins instead."""
    if ROUTE_ORDER.index(final_route) < ROUTE_ORDER.index(preliminary_route):
        return preliminary_route
    return final_route


@dataclass
class ScoreResult:
    """Everything a result must expose, per section 5.3: the individual
    factors, C, R, P, the route and the reason."""

    c1: float
    c2: float
    c3: float
    c4: float
    r1: float
    r2: float
    r3: float
    r4: float
    confidence: float
    risk: float
    priority: float
    route: Route
    reason: str


def score_question(
    c1: float,
    c2: float,
    c3: float,
    c4: float,
    r1: float,
    r2: float,
    r3: float,
    r4: float,
    category: str,
    config: dict,
    category_thresholds: Optional[dict] = None,
) -> ScoreResult:
    """Runs the formula (no guardrails: see guardrails.py) for one question,
    given its eight factors. The single entry point milestone-1 tests and
    later modules should call."""
    confidence = compute_confidence(c1, c2, c3, c4, config["confidence_weights"])
    risk = compute_risk(r1, r2, r3, r4, config["risk_weights"])
    priority = compute_priority(risk, confidence)
    risk_threshold = config["low_risk_threshold"]
    confidence_threshold = resolve_confidence_threshold(
        category,
        config["high_confidence_threshold"],
        config["compliance_floor"],
        category_thresholds,
    )
    route = classify_quadrant(confidence, risk, confidence_threshold, risk_threshold)
    reason = (
        f"C={confidence:.2f} (threshold {confidence_threshold:.2f}), "
        f"R={risk:.2f} (threshold {risk_threshold:.2f}) -> {route}"
    )
    return ScoreResult(
        c1=c1, c2=c2, c3=c3, c4=c4,
        r1=r1, r2=r2, r3=r3, r4=r4,
        confidence=confidence, risk=risk, priority=priority,
        route=route, reason=reason,
    )
