"""Tests for email_template.py -- the step-3 reply email is built from a
fixed template, never by the model; these tests are the guarantee that
stays true and that the signature-name heuristic doesn't guess wrong.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_template


# ── build_subject ────────────────────────────────────────────────────────


def test_build_subject_format():
    subject = email_template.build_subject("Harbourline Family Office")
    assert subject == (
        "Alken Aurora European Equity Fund – Completed Due Diligence "
        "Questionnaire – Harbourline Family Office"
    )


# ── extract_first_name_from_signature ───────────────────────────────────


def test_extracts_name_from_a_real_signature():
    # the actual body of the Harbourline test email (section 9 live test)
    body = (
        "Dear Investor Relations team,\r\n"
        "As part of our ongoing review of European long/short equity managers, "
        "Harbourline Family Office would like to learn more about the Alken Aurora "
        "European Equity Fund.\r\n"
        "Please find attached our short-form due diligence questionnaire.\r\n"
        "Kind regards,\r\n"
        "Sarah Whitfield\r\n"
        "Head of Manager Research, Harbourline Family Office\r\n"
    )
    assert email_template.extract_first_name_from_signature(body) == "Sarah"


def test_uses_the_last_signoff_when_several_appear():
    body = "Thanks for sending this.\n\nBest regards,\nJohn\n\nSincerely,\nAnna Smith\n"
    assert email_template.extract_first_name_from_signature(body) == "Anna"


def test_no_signoff_phrase_returns_none():
    body = "Please find attached our questionnaire.\n\nHarbourline Family Office\n"
    assert email_template.extract_first_name_from_signature(body) is None


def test_signoff_with_no_following_line_returns_none():
    body = "Please see attached.\n\nRegards,\n"
    assert email_template.extract_first_name_from_signature(body) is None


def test_signoff_followed_by_non_name_line_returns_none_rather_than_guess():
    # a disclaimer/company line, not a short capitalised name -- must not guess
    body = "Regards,\nThis email and any attachments are confidential and privileged.\n"
    assert email_template.extract_first_name_from_signature(body) is None


def test_empty_body_returns_none():
    assert email_template.extract_first_name_from_signature(None) is None
    assert email_template.extract_first_name_from_signature("") is None


# ── build_body ───────────────────────────────────────────────────────────


def test_body_with_recipient_name_and_no_gap_answers():
    body = email_template.build_body(recipient_first_name="Sarah", has_gap_answers=False)
    assert body.startswith("Dear Sarah,")
    assert "not included in our current documentation" not in body
    assert "Investor Relations" in body
    assert "Alken Asset Management (fictional case study)" in body


def test_body_without_recipient_name_uses_sir_or_madam():
    body = email_template.build_body(recipient_first_name=None, has_gap_answers=False)
    assert body.startswith("Dear Sir or Madam,")


def test_body_with_gap_answers_adds_the_paragraph():
    body = email_template.build_body(recipient_first_name="Sarah", has_gap_answers=True)
    assert "not included in our current documentation" in body
    assert "happy to discuss these points in a call" in body


# ── build_attachment_filename ───────────────────────────────────────────


def test_attachment_filename_format():
    name = email_template.build_attachment_filename("Harbourline Family Office", date(2026, 10, 15))
    assert name == "Alken_Aurora_DDQ_Response_Harbourline_Family_Office_2026-10-15.docx"


def test_attachment_filename_has_no_internal_id_or_hash():
    # a realistic investor name must never leak punctuation that could look like an id
    name = email_template.build_attachment_filename("Harbourline Family Office", date(2026, 10, 15))
    assert "__" not in name
    assert all(c.isalnum() or c in "_.-" for c in name)
