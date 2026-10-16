"""Hard rules that override the formula's route, applied before and above it
(CLAUDE.md section 6). Rules 1, 2, 3, 7, 8 and 9 are implemented here; rules
4-6 (vault-sources-only / GAP, data framing in model prompts, guarded mail
sending) belong to later modules (drafter.py, mail.py, agent.py).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Optional

from scoring import AUTO, DRAFT, HUMAN_ONLY, Route

# Matches integers, decimals, comma-thousands and percentages (e.g. "1.25",
# "1,250", "31%") so draft figures can be checked against cited sources.
NUMBER_PATTERN = re.compile(r"\d[\d,.]*%?")

# Citation markers the drafter is instructed to use, e.g. "(S11)", "[S03]",
# "(S12, S13)". Stripped before number-extraction in rule 3: the "03" in
# "[S03]" is a reference ID, not a figure the answer is making a claim
# about, and almost never happens to also appear as a coincidental digit
# substring elsewhere in the cited passages -- left unstripped, nearly
# every cited draft was flagged as having an "unsourced number".
CITATION_PATTERN = re.compile(r"\bS\d+\b")

# Rule 9: phrases that signal a question is trying to direct the agent to
# *act* (approve, send, attach, email, ignore its rules) rather than asking
# for fund information. Deterministic and no API call, like the rest of
# this module -- a heuristic, not a semantic judgement; it is intentionally
# broad (false positives just mean an extra human look, which is safe;
# false negatives are the real risk).
INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+|any\s+)?(previous|prior)\s+instructions\b", re.I),
    re.compile(r"\bignore\s+(the\s+|all\s+|your\s+)?(rules|guardrails)\b", re.I),
    re.compile(r"\bdisregard\s+(the\s+|your\s+)?(rules|instructions|guardrails)\b", re.I),
    re.compile(r"\boverride\s+(the\s+|your\s+)?(rules|guardrails|system)\b", re.I),
    re.compile(r"\bbypass\s+(the\s+|your\s+)?(rules|guardrails|review|approval)\b", re.I),
    re.compile(r"\bmark\s+(every|all|this|the)\s+.*\bapprov", re.I),
    re.compile(r"\bauto[- ]approve\b", re.I),
    re.compile(r"\bsend\s+(this|it|the\s+\w+)\s+to\b", re.I),
    re.compile(r"\bemail\s+.*\bto\b", re.I),
    re.compile(r"\battach\s+.*\bto\b", re.I),
    re.compile(r"\bforward\s+(this|it)\s+to\b", re.I),
    re.compile(r"\bdo\s+not\s+(tell|show|inform)\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\back\s+as\s+(if|an?)\b", re.I),
]


def contains_injection_attempt(text: Optional[str]) -> bool:
    """Whether a question's text matches any known instruction-injection
    pattern (rule 9)."""
    if not text:
        return False
    return any(p.search(text) for p in INJECTION_PATTERNS)


def extract_numbers(text: Optional[str]) -> list[str]:
    """Every number-like token in a piece of text, in the exact form it
    appears (so '1.25' and '1,250' are compared literally)."""
    return NUMBER_PATTERN.findall(text or "")


def rule_1_protected_category(
    route: Route, category: str, protected_categories: list[str]
) -> tuple[Route, Optional[str]]:
    """Rule 1: category in protected_categories -> at most DRAFT, never AUTO."""
    if route == AUTO and category in protected_categories:
        return DRAFT, "rule_1_protected_category"
    return route, None


def rule_2_unseen_question(route: Route, c1: float) -> tuple[Route, Optional[str]]:
    """Rule 2: c1 < 0.5 (question never seen before) -> never AUTO."""
    if route == AUTO and c1 < 0.5:
        return DRAFT, "rule_2_unseen_question"
    return route, None


def rule_3_unsourced_numbers(
    route: Route,
    draft_text: Optional[str],
    cited_passages: Optional[list[str]],
) -> tuple[Route, Optional[str]]:
    """Rule 3: every number in the draft must appear in the cited passages;
    otherwise the question drops to HUMAN_ONLY. Only applies once a draft
    exists (draft_text is None before drafting). Citation markers like
    "(S11)" are stripped first -- "11" there is a reference ID, not a
    figure to check."""
    if draft_text is None:
        return route, None
    draft_without_citations = CITATION_PATTERN.sub("", draft_text)
    sources_text = " ".join(cited_passages or [])
    for number in extract_numbers(draft_without_citations):
        if number not in sources_text:
            return HUMAN_ONLY, "rule_3_unsourced_numbers"
    return route, None


def rule_7_auto_sample(
    route: Route, auto_sample_rate: float, random_value: Optional[float] = None
) -> bool:
    """Rule 7: a random sample of AUTO answers is flagged 'to be checked' and
    must be ticked by a human. Returns whether this answer is flagged.
    random_value is injectable so tests stay deterministic; it defaults to
    random.random()."""
    if route != AUTO:
        return False
    roll = random.random() if random_value is None else random_value
    return roll < auto_sample_rate


def rule_8_contradiction(route: Route, c4: float) -> tuple[Route, Optional[str]]:
    """Rule 8: if similar approved answers disagree (c4 == 0), the question
    can never be AUTO, whatever C is -- the formula's weights alone would
    still allow C >= 0.80 in that case, which is exactly why this guardrail
    exists. The reviewer seeing both conflicting answers is a UI/retrieval
    concern (app.py / drafter.py), not this function's job."""
    if route == AUTO and c4 == 0:
        return DRAFT, "rule_8_contradiction"
    return route, None


def rule_9_prompt_injection(route: Route, question_text: Optional[str]) -> tuple[Route, Optional[str]]:
    """Rule 9: a question that tries to instruct the agent to act (approve,
    send, attach, email, ignore its rules) is routed to HUMAN_ONLY and
    flagged as a possible prompt injection -- unconditionally, overriding
    whatever route the formula produced, and never acted on."""
    if contains_injection_attempt(question_text):
        return HUMAN_ONLY, "rule_9_prompt_injection"
    return route, None


@dataclass
class GuardrailResult:
    route: Route
    fired: list[str] = field(default_factory=list)  # names of guardrails that changed something
    flagged_for_check: bool = False  # rule 7
    possible_prompt_injection: bool = False  # rule 9


def apply_guardrails(
    route: Route,
    category: str,
    c1: float,
    protected_categories: list[str],
    auto_sample_rate: float,
    c4: float = 1.0,
    question_text: Optional[str] = None,
    draft_text: Optional[str] = None,
    cited_passages: Optional[list[str]] = None,
    random_value: Optional[float] = None,
) -> GuardrailResult:
    """Applies guardrails 9, 1, 2, 8, 3 and 7 in order and returns the
    (possibly overridden) route plus the names of every guardrail that
    fired, so the caller can log them (section 6: 'every guardrail that
    fires is written to the log with its name'). Safe to call twice
    (preliminary and final score, per agent.py's flow): draft_text /
    cited_passages are None before a draft exists, so rule 3 simply does
    not apply yet. c4 defaults to 1.0 (no contradiction) so callers that do
    not yet track consistency are unaffected by rule 8."""
    fired: list[str] = []

    # Rule 9 first: a possible prompt injection overrides everything else.
    route, name = rule_9_prompt_injection(route, question_text)
    if name:
        fired.append(name)

    route, name = rule_1_protected_category(route, category, protected_categories)
    if name:
        fired.append(name)

    route, name = rule_2_unseen_question(route, c1)
    if name:
        fired.append(name)

    route, name = rule_8_contradiction(route, c4)
    if name:
        fired.append(name)

    route, name = rule_3_unsourced_numbers(route, draft_text, cited_passages)
    if name:
        fired.append(name)

    flagged = rule_7_auto_sample(route, auto_sample_rate, random_value)
    if flagged:
        fired.append("rule_7_auto_sample")

    return GuardrailResult(
        route=route,
        fired=fired,
        flagged_for_check=flagged,
        possible_prompt_injection="rule_9_prompt_injection" in fired,
    )
