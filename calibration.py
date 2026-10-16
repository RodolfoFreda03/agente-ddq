"""Recalibrates confidence thresholds per category (CLAUDE.md section 9),
run manually or periodically: `python calibration.py`.

Looks at every approved AUTO/DRAFT question across all DDQs in
data/ddqs/*/state.json (these are the only routes with a draft that a human
could "heavily edit"). For each category with at least 10 such approvals:
- more than 10% heavily edited (edit_rate > heavy_edit_threshold) -> raise
  the confidence threshold by 0.05 (max 0.95): the agent was too confident;
- fewer than 2% -> lower it by 0.02, never below compliance_floor: the agent
  is being needlessly cautious.
Categories with fewer than 10 approvals are left untouched -- not enough
data to recalibrate safely. Writes category_thresholds.yaml and prints what
changed and why.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

import scoring
import store

MIN_APPROVALS = 10
THRESHOLD_FILE = Path("category_thresholds.yaml")


def load_category_thresholds(path: Path = THRESHOLD_FILE) -> dict[str, float]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_category_thresholds(thresholds: dict[str, float], path: Path = THRESHOLD_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(thresholds, f, sort_keys=True)


def collect_edit_rates_by_category(states: list[store.DDQState]) -> dict[str, list[float]]:
    """edit_rate of every approved AUTO/DRAFT question, grouped by category.
    GAP and HUMAN_ONLY are excluded: they either have no draft to edit, or
    (GAP) only a partial one that was never meant to be graded this way."""
    by_category: dict[str, list[float]] = defaultdict(list)
    for ddq in states:
        for q in ddq.questions:
            if q.approved and q.route in (scoring.AUTO, scoring.DRAFT) and q.edit_rate is not None and q.category:
                by_category[q.category].append(q.edit_rate)
    return by_category


def recalibrate(config: dict, states: list[store.DDQState]) -> tuple[dict[str, float], list[str]]:
    """Returns the updated thresholds dict and a list of human-readable
    messages describing what changed and why (and what was skipped)."""
    thresholds = load_category_thresholds()
    heavy_edit_threshold = config["heavy_edit_threshold"]
    compliance_floor = config["compliance_floor"]
    global_threshold = config["high_confidence_threshold"]
    by_category = collect_edit_rates_by_category(states)
    messages: list[str] = []

    for category, edit_rates in sorted(by_category.items()):
        total = len(edit_rates)
        if total < MIN_APPROVALS:
            messages.append(
                f"{category}: skipped ({total} approval(s), need at least {MIN_APPROVALS})."
            )
            continue

        heavy_count = sum(1 for rate in edit_rates if rate > heavy_edit_threshold)
        heavy_share = heavy_count / total
        current = thresholds.get(category, global_threshold)

        if heavy_share > 0.10:
            new_threshold = min(round(current + 0.05, 2), 0.95)
            reason = (
                f"{heavy_count}/{total} ({heavy_share:.0%}) heavily edited (> 10%) "
                f"-> agent was too confident, raising threshold"
            )
        elif heavy_share < 0.02:
            new_threshold = max(round(current - 0.02, 2), compliance_floor)
            reason = (
                f"{heavy_count}/{total} ({heavy_share:.0%}) heavily edited (< 2%) "
                f"-> agent is overly cautious, lowering threshold"
            )
        else:
            messages.append(
                f"{category}: no change ({heavy_count}/{total} = {heavy_share:.0%} heavily edited, "
                f"within the 2-10% band). Threshold stays {current:.2f}."
            )
            continue

        if new_threshold == current:
            messages.append(
                f"{category}: {reason}, but already at the limit ({current:.2f}). No change."
            )
            continue

        thresholds[category] = new_threshold
        messages.append(f"{category}: {reason}: {current:.2f} -> {new_threshold:.2f}.")

    return thresholds, messages


def main() -> None:
    config = scoring.load_config()
    states = store.load_all_states()
    thresholds, messages = recalibrate(config, states)

    print(f"Calibration run over {len(states)} DDQ(s).")
    for message in messages:
        print(f"- {message}")

    if thresholds:
        save_category_thresholds(thresholds)
        print(f"\nWrote {THRESHOLD_FILE} with {len(thresholds)} category override(s).")
    else:
        print(f"\nNo category has enough data yet; {THRESHOLD_FILE} left untouched.")


if __name__ == "__main__":
    main()
