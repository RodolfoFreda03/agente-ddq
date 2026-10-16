"""Builds the step-3 reply email (subject, body, attachment filename) from
a fixed template -- never from the model (the user's explicit instruction,
matching section 13's spirit: only drafter.py calls the model, and never
for anything mail-related). Pure string functions, no Streamlit/Gmail
imports, so they can be unit-tested directly (test/test_email_template.py).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

FUND_NAME = "Alken Aurora European Equity Fund"
MEMORANDUM_VERSION = "1.0"
MEMORANDUM_AS_OF = "30 September 2026"

_SIGNOFF_PHRASES = {
    "regards", "kind regards", "best regards", "warm regards", "many thanks",
    "thanks", "thank you", "sincerely", "yours sincerely", "yours faithfully", "best",
}
_NAME_LINE = re.compile(r"^[A-Z][a-zA-Z.'-]*(?:\s+[A-Z][a-zA-Z.'-]*){0,3}$")


def build_subject(investor_name: str) -> str:
    """Point 1: 'Alken Aurora European Equity Fund – Completed Due
    Diligence Questionnaire – <Investor name>'. The reply keeps the same
    Gmail thread as the original email regardless of subject text -- that
    is handled by mail.py/mail_gmail.py via In-Reply-To/References and
    threadId, not by subject matching, so a changed subject here is safe."""
    return f"{FUND_NAME} – Completed Due Diligence Questionnaire – {investor_name}"


def extract_first_name_from_signature(body_text: Optional[str]) -> Optional[str]:
    """Best-effort first name from the sign-off of the ORIGINAL email body
    (point 2: 'first name of the sender if available in the original email
    signature'), e.g. '...\\nKind regards,\\nSarah Whitfield\\nHead of...'
    -> 'Sarah'. Deliberately conservative -- a heuristic, not the model:
    finds the LAST line that is (once stripped of punctuation) exactly a
    known sign-off phrase, then checks whether the next non-empty line
    looks like a short, capitalised name (not a job title, company name,
    or disclaimer). Returns None rather than guessing wrong -- the caller
    falls back to 'Sir or Madam'."""
    if not body_text:
        return None

    lines = [line.strip() for line in body_text.splitlines()]
    signoff_idx = None
    for i, line in enumerate(lines):
        normalised = line.strip(" ,.!").lower()
        if normalised in _SIGNOFF_PHRASES:
            signoff_idx = i  # keep the LAST match -- signatures are near the end

    if signoff_idx is None:
        return None

    for line in lines[signoff_idx + 1:]:
        if not line:
            continue
        if _NAME_LINE.match(line):
            return line.split()[0]
        return None  # the next non-empty line didn't look like a name -- don't guess

    return None


def build_body(*, recipient_first_name: Optional[str], has_gap_answers: bool) -> str:
    """Point 2's template, verbatim. The 'information not available'
    paragraph is included only when at least one answer is a GAP (states
    something is not documented) -- never a generic disclaimer on every
    reply."""
    greeting = f"Dear {recipient_first_name}," if recipient_first_name else "Dear Sir or Madam,"
    gap_paragraph = ""
    if has_gap_answers:
        gap_paragraph = (
            "\n\nSome of the information requested is not included in our current documentation; "
            "where this is the case, we have indicated it in the response. We would be happy to "
            "discuss these points in a call."
        )
    return (
        f"{greeting}\n\n"
        f"Thank you for your interest in the {FUND_NAME} and for sending your due diligence "
        f"questionnaire.\n\n"
        f"Please find attached our completed response. All answers are based on the fund's "
        f"investment memorandum (version {MEMORANDUM_VERSION}, information as at "
        f"{MEMORANDUM_AS_OF}), and each answer references the relevant section."
        f"{gap_paragraph}\n\n"
        f"Please do not hesitate to contact us if you have any further questions or would like "
        f"to arrange a meeting with the investment team.\n\n"
        f"Kind regards,\n\n"
        f"Investor Relations\n"
        f"Alken Asset Management (fictional case study)"
    )


def build_attachment_filename(investor_name: str, as_of: date) -> str:
    """'Alken_Aurora_DDQ_Response_<Investor_Name>_<YYYY-MM-DD>.docx' -- no
    internal id or hash, unlike the current final_file_path. Always .docx:
    the reply is always a fresh Word document now, whatever format the
    incoming DDQ was (ddq_writer.write_word_response)."""
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", investor_name).strip("_")
    return f"Alken_Aurora_DDQ_Response_{safe_name}_{as_of.isoformat()}.docx"
