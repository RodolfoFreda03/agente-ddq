"""Tests for calibration.py (CLAUDE.md section 9). No API calls: builds
QuestionState/DDQState objects in memory, never touches data/ddqs/ or
category_thresholds.yaml on disk."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calibration
import scoring
import store


def _ddq_with(category: str, route: str, edit_rates: list[float], approved: bool = True) -> store.DDQState:
    questions = [
        store.QuestionState(
            id=f"q{i}", section=None, text="t", row=i,
            category=category, route=route, approved=approved, edit_rate=rate,
        )
        for i, rate in enumerate(edit_rates)
    ]
    return store.DDQState(
        id="d", source_path="x.xlsx", sheet_name="s",
        sender="a@b.example", date_received="2026-01-01T00:00:00+00:00",
        questions=questions,
    )


CONFIG = {
    "heavy_edit_threshold": 0.30,
    "compliance_floor": 0.70,
    "high_confidence_threshold": 0.80,
}


def test_category_below_min_approvals_is_skipped():
    ddq = _ddq_with("fees", scoring.AUTO, [0.5] * 9)  # 9 < MIN_APPROVALS
    thresholds, messages = calibration.recalibrate(CONFIG, [ddq])
    assert thresholds == {}
    assert any("skipped" in m for m in messages)


def test_high_edit_rate_raises_threshold():
    # 3/10 = 30% heavily edited (> 10%)
    edit_rates = [0.5, 0.5, 0.5] + [0.1] * 7
    ddq = _ddq_with("fees", scoring.DRAFT, edit_rates)
    thresholds, messages = calibration.recalibrate(CONFIG, [ddq])
    assert thresholds["fees"] == 0.85
    assert any("raising threshold" in m for m in messages)


def test_high_edit_rate_never_exceeds_cap():
    # starting already close to the cap: 0.93 + 0.05 would be 0.98, must clamp to 0.95
    edit_rates = [0.5] * 3 + [0.1] * 7
    ddq = _ddq_with("fees", scoring.AUTO, edit_rates)
    (Path("category_thresholds.yaml")).write_text("fees: 0.93\n", encoding="utf-8")
    try:
        thresholds, _ = calibration.recalibrate(CONFIG, [ddq])
    finally:
        Path("category_thresholds.yaml").unlink(missing_ok=True)
    assert thresholds["fees"] == 0.95


def test_low_edit_rate_lowers_threshold():
    # 0/10 = 0% heavily edited (< 2%)
    edit_rates = [0.1] * 10
    ddq = _ddq_with("fees", scoring.AUTO, edit_rates)
    thresholds, messages = calibration.recalibrate(CONFIG, [ddq])
    assert thresholds["fees"] == 0.78
    assert any("lowering threshold" in m for m in messages)


def test_low_edit_rate_never_goes_below_compliance_floor():
    # 0.80 - 0.02 = 0.78, but the floor is 0.79, so it must clamp to 0.79
    edit_rates = [0.0] * 10
    config = {**CONFIG, "compliance_floor": 0.79}
    ddq = _ddq_with("fees", scoring.AUTO, edit_rates)
    thresholds, _ = calibration.recalibrate(config, [ddq])
    assert thresholds["fees"] == 0.79


def test_middle_band_makes_no_change():
    # 1/10 = 10% heavily edited: not > 10% and not < 2%, so no change
    edit_rates = [0.5] + [0.1] * 9
    ddq = _ddq_with("fees", scoring.AUTO, edit_rates)
    thresholds, messages = calibration.recalibrate(CONFIG, [ddq])
    assert thresholds == {}
    assert any("no change" in m for m in messages)


def test_gap_and_human_only_routes_are_excluded():
    # neither route has a draft a human could "heavily edit" -- excluded
    # entirely, so "fees" never even appears as a category with data.
    gap_ddq = _ddq_with("fees", scoring.GAP, [0.9] * 10)
    human_ddq = _ddq_with("fees", scoring.HUMAN_ONLY, [0.9] * 10)
    thresholds, messages = calibration.recalibrate(CONFIG, [gap_ddq, human_ddq])
    assert thresholds == {}
    assert not any("fees" in m for m in messages)


def test_unapproved_questions_are_excluded():
    ddq = _ddq_with("fees", scoring.AUTO, [0.9] * 10, approved=False)
    thresholds, messages = calibration.recalibrate(CONFIG, [ddq])
    assert thresholds == {}
    assert not any("fees" in m for m in messages)
