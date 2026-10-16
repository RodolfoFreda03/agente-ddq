"""Tests for ddq_reader.py's Word support: table layout (real investor
file) and numbered-paragraph layout (test/test_ddq_numbered.docx, built to
hold the exact same 5 questions/sections as the real table file, so the
two can be compared directly)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import ddq_reader

ROOT = Path(__file__).resolve().parent.parent
WORD_TABLE_PATH = ROOT / "DDQ_Harbourline_Family_Office_2026_WORD.docx"
WORD_NUMBERED_PATH = Path(__file__).resolve().parent / "test_ddq_numbered.docx"

EXPECTED_QUESTIONS = [
    ("Q01", "A. Operations", "Who is the depositary of the fund?"),
    ("Q02", "B. Fees", "What is the annual management fee for Class I?"),
    ("Q03", "C. Dealing", "How quickly are redemption proceeds paid?"),
    ("Q04", "D. Cybersecurity", "Is the firm ISO 27001 certified, or does it have a SOC 2 report?"),
    ("Q05", "E. Compliance",
     "Is there any pending litigation or regulatory investigation involving the fund or the manager?"),
]


def _tuples(ddq):
    return [(q.id, q.section, q.text) for q in ddq.questions]


@pytest.mark.skipif(not WORD_TABLE_PATH.exists(), reason="real sample file not present")
def test_table_layout_reads_the_real_harbourline_word_file():
    ddq = ddq_reader.read_ddq(WORD_TABLE_PATH)
    assert _tuples(ddq) == EXPECTED_QUESTIONS
    assert ddq.investor_name == "Harbourline Family Office"
    assert ddq.sheet_name == "Word (table)"


def test_numbered_paragraph_layout_reads_the_built_sample():
    ddq = ddq_reader.read_ddq(WORD_NUMBERED_PATH)
    assert _tuples(ddq) == EXPECTED_QUESTIONS
    assert ddq.investor_name == "Harbourline Family Office"
    assert ddq.sheet_name == "Word (numbered paragraphs)"


@pytest.mark.skipif(not WORD_TABLE_PATH.exists(), reason="real sample file not present")
def test_both_word_layouts_extract_the_same_questions():
    table_ddq = ddq_reader.read_ddq(WORD_TABLE_PATH)
    numbered_ddq = ddq_reader.read_ddq(WORD_NUMBERED_PATH)
    assert _tuples(table_ddq) == _tuples(numbered_ddq)


def test_numbered_question_pattern_accepts_common_variants():
    cases = [
        ("1. Who is the depositary?", "1", "Who is the depositary?"),
        ("Q1. Who is the depositary?", "1", "Who is the depositary?"),
        ("Q1 Who is the depositary?", "1", "Who is the depositary?"),
        ("12) Some later question?", "12", "Some later question?"),
    ]
    for text, expected_num, expected_text in cases:
        match = ddq_reader._NUMBERED_QUESTION.match(text)
        assert match is not None, text
        assert match.group(1) == expected_num
        assert match.group(2) == expected_text


def test_word_ddq_with_no_table_and_no_numbered_questions_raises():
    import docx

    doc = docx.Document()
    doc.add_paragraph("This is just a letter, not a questionnaire.")
    tmp_path = Path(__file__).resolve().parent / "_tmp_not_a_ddq.docx"
    doc.save(str(tmp_path))
    try:
        with pytest.raises(ValueError):
            ddq_reader.read_ddq(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def test_investor_name_from_title_falls_back_to_filename():
    name = ddq_reader.investor_name_from_title([], Path("DDQ_Some_New_Investor_2027.xlsx"))
    assert name == "Some New Investor"


def test_investor_name_from_title_prefers_the_em_dash_title():
    name = ddq_reader.investor_name_from_title(
        ["Some preamble line", "Acme Capital — Manager Due Diligence Questionnaire"],
        Path("whatever.docx"),
    )
    assert name == "Acme Capital"
