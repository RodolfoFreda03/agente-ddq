"""Plain-English 'Why this route?' explanations for a non-technical
reviewer (CLAUDE.md section 5.3: 'every result must expose... the reason,
for transparency' -- but not as C/R/c1-c4/r1-r4 numbers and category
codes). Built entirely from the route, category, guardrails fired and the
first cited source: a fixed decision tree, never the model, so the same
kind of decision always produces the same kind of sentence. The raw
numeric factors are untouched elsewhere (output/log.csv, state.json) --
this module only changes what the review page displays.
"""

from __future__ import annotations

from typing import Optional

import scoring

# "Category wording to use" (the user's own phrasing, verbatim).
CATEGORY_TOPIC = {
    "fees": "Fee information",
    "performance": "Performance information",
    "legal_regulatory": "Legal and regulatory information",
    "esg": "ESG information",
    "conflicts_of_interest": "This question",
}
CATEGORY_WORDING = {
    "fees": "regulated communication to investors",
    "performance": "regulated communication to investors",
    "legal_regulatory": "legally binding statement",
    "esg": "sustainability claim subject to anti-greenwashing rules",
    "conflicts_of_interest": "sensitive governance matter",
}


def plain_english_reason(
    route: str,
    category: Optional[str],
    guardrails_fired: list[str],
    possible_prompt_injection: bool,
    cited_sources: list[str],
    figures_or_commitments: float,
    flagged_for_check: bool = False,
) -> str:
    """One or two short sentences explaining the routing decision."""
    first_source = cited_sources[0] if cited_sources else None

    if possible_prompt_injection:
        return (
            "This question contains instructions addressed to the agent. "
            "They were ignored and the question was passed to a person."
        )

    if "rule_8_contradiction" in guardrails_fired:
        return (
            "Two previously approved answers disagree on this question. "
            "Please confirm the correct figure before approving."
        )

    if route == scoring.GAP:
        return (
            "This information is not in the memorandum. The agent must not invent it: "
            "complete the answer or state that it is not available."
        )

    if route == scoring.HUMAN_ONLY:
        if category == "legal_regulatory":
            return (
                "Statements on litigation or regulatory matters are legally binding. "
                "Only a person can answer; the relevant sources are listed below."
            )
        if figures_or_commitments and category not in CATEGORY_WORDING:
            return "The question asks for a commercial commitment, which only a person can make."
        if category in CATEGORY_WORDING:
            return (
                f"{CATEGORY_TOPIC[category]} is a {CATEGORY_WORDING[category]}. "
                f"Only a person can answer; the relevant sources are listed below."
            )
        return (
            "The agent has not seen a close match to this question before, so a person "
            "must answer it; the relevant sources are listed below."
        )

    if route == scoring.DRAFT:
        source_phrase = f"from {first_source}" if first_source else "without a clear source"
        if category in CATEGORY_WORDING:
            return (
                f"{CATEGORY_TOPIC[category]} is a {CATEGORY_WORDING[category]}. "
                f"The agent prepared a draft {source_phrase}, but a person must approve it."
            )
        return (
            f"The agent prepared a draft {source_phrase}, but this question's risk is high "
            f"enough that a person must approve it before sending."
        )

    # AUTO
    source_phrase = f" ({first_source})" if first_source else ""
    sentence = (
        f"Factual information that has already been approved and is fully supported by "
        f"the memorandum{source_phrase}. Low risk, so the agent answered on its own."
    )
    if flagged_for_check:
        sentence += " This answer was randomly selected for a spot check."
    return sentence
