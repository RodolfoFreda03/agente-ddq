"""Confusion matrix, accuracy, high-risk-to-AUTO check, and a token/cost
report for a full engine run against test/test_ddq.xlsx (CLAUDE.md section 11).

Usage: python test/evaluate.py   (run from the project root)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# So `import agent` etc. work when this file is run directly as
# `python test/evaluate.py` from the project root (section 10's documented
# command) -- pyproject.toml's pythonpath only applies under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")  # no display needed -- just save the PNG
import matplotlib.pyplot as plt
import numpy as np
import openpyxl

import agent
import llm
import scoring
import store

TEST_DDQ_PATH = Path(__file__).resolve().parent / "test_ddq.xlsx"
DEMO_DDQ_PATH = Path("demo_inbox/DDQ_Northgate_Pension_2026.xlsx")
OUTPUT_PNG = Path("output/confusion_matrix.png")

ROUTES = [scoring.AUTO, scoring.DRAFT, scoring.GAP, scoring.HUMAN_ONLY]

# Rough paid-tier prices (USD per 1M tokens) for the pinned models, used only
# to estimate a business-case cost -- this run itself was free (Gemini free
# tier). Source: https://ai.google.dev/gemini-api/docs/pricing at the time
# of writing (2026-10-14); re-check before using the number in a real slide.
PAID_PRICE_PER_MILLION_TOKENS_USD = {
    "gemini-flash-lite-latest": {"input": 0.10, "output": 0.40},
    "gemini-3.8-flash": {"input": 0.30, "output": 2.50},
}


def load_expected(path: Path = TEST_DDQ_PATH) -> dict:
    workbook = openpyxl.load_workbook(path)
    worksheet = workbook.active
    rows = list(worksheet.iter_rows(values_only=True))
    header, data = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    expected = {}
    for row in data:
        qid = row[col["id"]]
        expected[qid] = {
            "question": row[col["question"]],
            "expected_category": row[col["expected_category"]],
            "expected_route": row[col["expected_route"]],
            "approved_answer_exists": row[col["approved_answer_exists"]],
            "memo_reference": row[col["memo_reference"]],
            "rationale": row[col["rationale"]],
        }
    return expected


EVALUATE_DDQ_ID = "evaluate-run"


def run_engine():
    """Runs the full engine on the same 35 questions as test_ddq.xlsx
    (demo_inbox/DDQ_Northgate_Pension_2026.xlsx has identical question
    text). Thanks to llm.py's response cache, re-running after milestone 5
    already has costs ~0 extra quota -- every call hits the cache.

    process_ddq() persists its state to data/ddqs/evaluate-run/ via
    store.py (same as any real DDQ) because that incremental-save survives
    a crash mid-run; it is deleted again as soon as this function returns
    (see main()) so this evaluation run never shows up as a second,
    duplicate DDQ in the Streamlit interface, which only scans data/ddqs/
    for real mailbox-derived DDQs."""
    config = agent.load_config()
    llm.reset_usage_totals()
    return agent.process_ddq(DEMO_DDQ_PATH, agent.DEMO_DEFAULT_SENDER, config["llm"], config, ddq_id=EVALUATE_DDQ_ID)


def build_confusion_matrix(expected: dict, state) -> tuple[np.ndarray, list[tuple]]:
    matrix = np.zeros((4, 4), dtype=int)
    mismatches = []
    for q in state.questions:
        exp = expected.get(q.id)
        if exp is None:
            continue
        exp_route, pred_route = exp["expected_route"], q.route
        matrix[ROUTES.index(exp_route), ROUTES.index(pred_route)] += 1
        if exp_route != pred_route:
            mismatches.append((q.id, exp_route, pred_route, q.reason, exp["rationale"]))
    return matrix, mismatches


def plot_confusion_matrix(matrix: np.ndarray, path: Path = OUTPUT_PNG) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(4))
    ax.set_xticklabels(ROUTES, rotation=30)
    ax.set_yticks(range(4))
    ax.set_yticklabels(ROUTES)
    ax.set_xlabel("Predicted route")
    ax.set_ylabel("Expected route")
    ax.set_title("DDQ Agent — routing confusion matrix")
    vmax = matrix.max() if matrix.size else 0
    for i in range(4):
        for j in range(4):
            ax.text(
                j, i, str(matrix[i, j]), ha="center", va="center",
                color="white" if vmax and matrix[i, j] > vmax / 2 else "black",
            )
    fig.colorbar(im)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def count_high_risk_routed_to_auto(state, protected_categories: list[str]) -> int:
    """section 11: 'number of high-risk questions routed to AUTO: must be
    0'. High-risk = the question's own classified category is one of
    protected_categories -- guardrail 1 should make this structurally
    impossible; this is the end-to-end check that it actually does."""
    return sum(1 for q in state.questions if q.route == scoring.AUTO and q.category in protected_categories)


def estimate_cost_usd(totals: dict) -> float:
    cost = 0.0
    for model, stats in totals["by_model"].items():
        prices = PAID_PRICE_PER_MILLION_TOKENS_USD.get(model)
        if not prices:
            continue
        cost += stats["prompt_tokens"] / 1_000_000 * prices["input"]
        cost += stats["completion_tokens"] / 1_000_000 * prices["output"]
    return cost


def main() -> None:
    config = agent.load_config()
    protected_categories = config["protected_categories"]

    print("Running the engine on demo_inbox/DDQ_Northgate_Pension_2026.xlsx ...")
    try:
        state = run_engine()
    finally:
        # Always remove the evaluation run's state, even on failure -- it
        # must never linger in data/ddqs/ and appear as a duplicate DDQ in
        # the Streamlit interface (see run_engine's docstring).
        shutil.rmtree(store.ddq_dir(EVALUATE_DDQ_ID), ignore_errors=True)

    expected = load_expected()
    matrix, mismatches = build_confusion_matrix(expected, state)

    correct = int(np.trace(matrix))
    total = int(matrix.sum())
    accuracy = correct / total if total else 0.0

    print()
    print("=== Confusion matrix (rows=expected, cols=predicted) ===")
    print("            " + "".join(f"{r:>12}" for r in ROUTES))
    for i, r in enumerate(ROUTES):
        print(f"{r:>12}" + "".join(f"{matrix[i, j]:>12}" for j in range(4)))

    print()
    print(f"Accuracy: {correct}/{total} = {accuracy:.1%}")

    high_risk_auto = count_high_risk_routed_to_auto(state, protected_categories)
    print()
    print(f"*** High-risk questions routed to AUTO: {high_risk_auto} (must be 0) ***")

    print()
    print(f"Mismatches ({len(mismatches)}):")
    for qid, exp, pred, reason, rationale in mismatches:
        print(f"  {qid}: expected {exp}, got {pred}")
        print(f"      agent reason: {reason}")
        print(f"      test rationale: {rationale}")

    totals = llm.get_usage_totals()
    cost = estimate_cost_usd(totals)
    print()
    print("=== Tokens used ===")
    print(f"Calls: {totals['calls']} ({totals['cached_calls']} served from cache, 0 extra quota)")
    print(f"Prompt tokens: {totals['prompt_tokens']}")
    print(f"Completion tokens: {totals['completion_tokens']}")
    print(f"Total tokens: {totals['total_tokens']}")
    print(
        f"Estimated cost at paid-tier prices: ${cost:.4f} "
        f"(this run itself was free -- Gemini free tier; estimate for the business case)"
    )

    plot_confusion_matrix(matrix)
    print(f"\nConfusion matrix saved to {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
