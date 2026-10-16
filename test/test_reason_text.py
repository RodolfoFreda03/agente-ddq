"""Tests for reason_text.py -- every example sentence the user specified,
verbatim, plus the priority order between prompt injection, contradiction,
and the route/category rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reason_text
import scoring


def test_auto_with_source():
    text = reason_text.plain_english_reason(
        scoring.AUTO, "operations", [], False, cited_sources=["S13"], figures_or_commitments=0,
    )
    assert text == (
        "Factual information that has already been approved and is fully supported by "
        "the memorandum (S13). Low risk, so the agent answered on its own."
    )


def test_auto_without_source():
    text = reason_text.plain_english_reason(
        scoring.AUTO, "organisation", [], False, cited_sources=[], figures_or_commitments=0,
    )
    assert "the memorandum." in text  # no stray "()" when there is no source
    assert "(" not in text


def test_auto_flagged_for_spot_check_adds_a_sentence():
    text = reason_text.plain_english_reason(
        scoring.AUTO, "operations", [], False, cited_sources=["S13"], figures_or_commitments=0,
        flagged_for_check=True,
    )
    assert text.endswith("This answer was randomly selected for a spot check.")


def test_draft_protected_category_fees():
    text = reason_text.plain_english_reason(
        scoring.DRAFT, "fees", [], False, cited_sources=["S11"], figures_or_commitments=1,
    )
    assert text == (
        "Fee information is a regulated communication to investors. The agent prepared "
        "a draft from S11, but a person must approve it."
    )


def test_draft_contradiction():
    text = reason_text.plain_english_reason(
        scoring.DRAFT, "operations", ["rule_8_contradiction"], False,
        cited_sources=["S13"], figures_or_commitments=1,
    )
    assert text == (
        "Two previously approved answers disagree on this question. "
        "Please confirm the correct figure before approving."
    )


def test_draft_non_protected_category_generic():
    text = reason_text.plain_english_reason(
        scoring.DRAFT, "operations", [], False, cited_sources=["S13"], figures_or_commitments=1,
    )
    assert "a person must approve it before sending" in text
    assert "regulated communication" not in text


def test_gap():
    text = reason_text.plain_english_reason(
        scoring.GAP, "operations", [], False, cited_sources=[], figures_or_commitments=0,
    )
    assert text == (
        "This information is not in the memorandum. The agent must not invent it: "
        "complete the answer or state that it is not available."
    )


def test_human_only_legal():
    text = reason_text.plain_english_reason(
        scoring.HUMAN_ONLY, "legal_regulatory", [], False, cited_sources=["S15"], figures_or_commitments=0,
    )
    assert text == (
        "Statements on litigation or regulatory matters are legally binding. "
        "Only a person can answer; the relevant sources are listed below."
    )


def test_human_only_commercial_commitment():
    text = reason_text.plain_english_reason(
        scoring.HUMAN_ONLY, "operations", [], False, cited_sources=["S13"], figures_or_commitments=1,
    )
    assert text == "The question asks for a commercial commitment, which only a person can make."


def test_human_only_protected_non_legal_category():
    text = reason_text.plain_english_reason(
        scoring.HUMAN_ONLY, "esg", [], False, cited_sources=[], figures_or_commitments=0,
    )
    assert "sustainability claim subject to anti-greenwashing rules" in text


def test_human_only_generic_fallback():
    text = reason_text.plain_english_reason(
        scoring.HUMAN_ONLY, "operations", [], False, cited_sources=[], figures_or_commitments=0,
    )
    assert "has not seen a close match" in text


def test_prompt_injection_overrides_everything():
    text = reason_text.plain_english_reason(
        scoring.AUTO, "fees", ["rule_8_contradiction"], True,
        cited_sources=["S11"], figures_or_commitments=1,
    )
    assert text == (
        "This question contains instructions addressed to the agent. "
        "They were ignored and the question was passed to a person."
    )


def test_no_numbers_or_codes_in_any_sentence():
    import re

    cases = [
        (scoring.AUTO, "operations", [], False),
        (scoring.DRAFT, "fees", [], False),
        (scoring.GAP, "operations", [], False),
        (scoring.HUMAN_ONLY, "legal_regulatory", [], False),
        (scoring.HUMAN_ONLY, "operations", [], False),
    ]
    for route, category, guardrails, injected in cases:
        text = reason_text.plain_english_reason(
            route, category, guardrails, injected, cited_sources=["S13"], figures_or_commitments=1,
        )
        # no bare decimal/threshold-looking numbers (a source reference like "S13" is fine)
        assert not re.search(r"\b0\.\d+\b", text)
        assert "threshold" not in text.lower()
