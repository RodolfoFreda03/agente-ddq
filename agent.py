"""Engine: processes a DDQ end to end (CLAUDE.md section 9).

Used by the interface (app.py) and the CLI (`python agent.py --demo`).
The interface never calls the model directly -- it only uses this module,
store.py, memory.py and mail.py (section 13).
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

import drafter
import ddq_reader
import email_template
import mail
import memory
import scoring
import guardrails
import store

DEMO_INBOX_DIR = Path("demo_inbox")
DEMO_DEFAULT_SENDER = "investor@northgate-pension.example"
CONFIG_PATH = Path("config.yaml")
INVESTORS_PATH = Path("investors.yaml")
CATEGORY_THRESHOLDS_PATH = Path("category_thresholds.yaml")

CONSISTENCY_SIMILARITY_THRESHOLD = 0.6
SEARCH_TOP_K = 3  # section 13: "keep prompts short (top 3 passages only)"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_investors() -> dict:
    if not INVESTORS_PATH.exists():
        return {}
    with open(INVESTORS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_category_thresholds() -> Optional[dict]:
    if not CATEGORY_THRESHOLDS_PATH.exists():
        return None
    with open(CATEGORY_THRESHOLDS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or None


def investor_importance(sender: str, investors: dict) -> float:
    """r4 (section 5.2): investor importance by sender email domain,
    default 0.5 if the domain is not in investors.yaml."""
    domain = sender.rsplit("@", 1)[-1].strip().lower() if "@" in sender else ""
    return float(investors.get(domain, 0.5))


def slugify(name: str) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug or "ddq"


def _passages_for(note_results: list[memory.SearchResult]) -> list[dict]:
    return [{"reference": r.note.reference, "text": r.note.content} for r in note_results[:SEARCH_TOP_K]]


def score_and_draft_question(
    question: ddq_reader.Question,
    sender: str,
    llm_config: dict,
    config: dict,
    investors: dict,
    category_thresholds: Optional[dict],
    as_of_date: Optional[str],
) -> store.QuestionState:
    """The per-question pipeline (section 9 step 4): classification -> vault
    search -> preliminary score -> guardrails -> draft + verification if
    required -> final score -> guardrails again."""
    confidence_weights = config["confidence_weights"]
    risk_weights = config["risk_weights"]
    category_risk = config["category_risk"]
    protected_categories = config["protected_categories"]
    freshness_days = config["freshness_days"]
    low_risk_threshold = config["low_risk_threshold"]
    high_confidence_threshold = config["high_confidence_threshold"]
    compliance_floor = config["compliance_floor"]
    auto_sample_rate = config["auto_sample_rate"]

    as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else None

    # ── classification (r1-r3) ───────────────────────────────────────
    classification = drafter.classify_question(llm_config, question.text)
    category = classification["category"]
    r1 = category_risk[category]
    r2 = classification["figures_or_commitments"]
    r3 = classification["forward_looking"]
    r4 = investor_importance(sender, investors)
    risk = scoring.compute_risk(r1, r2, r3, r4, risk_weights)

    # ── vault search (source passages + c1 + c4 inputs) ─────────────
    source_notes = memory.load_vault_notes(note_type="source")
    search_results = memory.search(question.text, source_notes, top_k=SEARCH_TOP_K)
    passages = _passages_for(search_results)
    cited_sources = [p["reference"] for p in passages]

    c1 = memory.max_similarity_to_approved_questions(question.text, as_of=as_of)

    source_dates = [r.note.metadata.get("date") for r in search_results if r.note.metadata.get("date")]
    c3 = scoring.compute_c3_freshness(source_dates, category, freshness_days, as_of_date=as_of)

    approved_notes = memory.non_expired(memory.load_vault_notes(note_type="approved_answer"), as_of=as_of)
    similar = memory.similar_approved_answers(question.text, approved_notes, threshold=CONSISTENCY_SIMILARITY_THRESHOLD)
    if len(similar) >= 2:
        answers = [r.note.answer() or "" for r in similar[:2]]
        c4 = drafter.judge_consistency(llm_config, question.text, answers)
    elif len(similar) == 1:
        c4 = 1.0
    else:
        c4 = 0.5

    # ── preliminary score + guardrails ────────────────────────────────
    preliminary_confidence = scoring.compute_preliminary_confidence(c1, c3, c4, confidence_weights)
    confidence_threshold = scoring.resolve_confidence_threshold(
        category, high_confidence_threshold, compliance_floor, category_thresholds,
    )
    preliminary_route = scoring.classify_quadrant(preliminary_confidence, risk, confidence_threshold, low_risk_threshold)

    # One random roll per question for rule 7 (auto_sample_rate), reused in
    # both guardrail passes below -- otherwise a question that stays AUTO
    # through both passes would get two independent rolls, flagging it at
    # a higher effective rate than configured.
    sample_roll = random.random()

    prelim_guard = guardrails.apply_guardrails(
        route=preliminary_route, category=category, c1=c1, c4=c4,
        protected_categories=protected_categories, auto_sample_rate=auto_sample_rate,
        question_text=question.text, random_value=sample_roll,
    )
    route = prelim_guard.route
    fired = list(prelim_guard.fired)

    # ── draft + verification if required (never for HUMAN_ONLY) ───────
    draft_text: Optional[str] = None
    c2 = 0.0
    confidence = preliminary_confidence

    if route != scoring.HUMAN_ONLY:
        draft_text, _ = drafter.draft_answer(llm_config, question.text, passages)
        c2 = drafter.verify_source_coverage(llm_config, draft_text, passages)
        confidence = scoring.compute_confidence(c1, c2, c3, c4, confidence_weights)
        final_route_raw = scoring.classify_quadrant(confidence, risk, confidence_threshold, low_risk_threshold)
        final_route = scoring.clamp_route_to_at_most(route, final_route_raw)

        final_guard = guardrails.apply_guardrails(
            route=final_route, category=category, c1=c1, c4=c4,
            protected_categories=protected_categories, auto_sample_rate=auto_sample_rate,
            question_text=question.text, random_value=sample_roll,
            draft_text=draft_text, cited_passages=[p["text"] for p in passages],
        )
        route = final_guard.route
        fired = list(dict.fromkeys(fired + final_guard.fired))  # union, order preserved
        flagged_for_check = prelim_guard.flagged_for_check or final_guard.flagged_for_check
        possible_prompt_injection = prelim_guard.possible_prompt_injection or final_guard.possible_prompt_injection
    else:
        flagged_for_check = prelim_guard.flagged_for_check
        possible_prompt_injection = prelim_guard.possible_prompt_injection

    priority = scoring.compute_priority(risk, confidence)
    reason = (
        f"C={confidence:.2f} (threshold {confidence_threshold:.2f}), "
        f"R={risk:.2f} (threshold {low_risk_threshold:.2f}) -> {route}"
        + (f"; guardrails: {', '.join(fired)}" if fired else "")
    )

    # AUTO starts pre-approved unless flagged for a spot check (section 8).
    approved = route == scoring.AUTO and not flagged_for_check

    return store.QuestionState(
        id=question.id, section=question.section, text=question.text, row=question.row,
        c1=c1, c2=c2, c3=c3, c4=c4, r1=r1, r2=r2, r3=r3, r4=r4,
        confidence=confidence, risk=risk, priority=priority,
        route=route, reason=reason, category=category,
        guardrails_fired=fired, flagged_for_check=flagged_for_check,
        possible_prompt_injection=possible_prompt_injection,
        draft_text=draft_text, cited_sources=cited_sources,
        final_answer=draft_text, approved=approved,
    )


def _notify_reviewer_stub(state: store.DDQState, config: dict) -> None:
    """Section 9 step 6: 'a short internal email tells the reviewer a DDQ
    is ready'. Deferred to milestone 9 (mail.py, the simulated mailbox):
    for now this just logs, so milestones 5-8 don't depend on code that
    hasn't been built yet."""
    if config.get("email", {}).get("notify_reviewer"):
        print(f"[notify_reviewer -- deferred to milestone 9] DDQ {state.id!r} is ready for review "
              f"({len(state.questions)} questions, sender {state.sender}).")


def process_ddq(
    path: Path,
    sender: str,
    llm_config: dict,
    config: dict,
    ddq_id: Optional[str] = None,
    limit_questions: Optional[int] = None,
) -> store.DDQState:
    """Reads one DDQ file, scores every question, saves the state, and
    returns it. Does not send anything (that's app.py step 3 + mail.py).
    limit_questions truncates the question list -- for quick smoke tests
    only; never passed by the real CLI or app.py."""
    ddq_id = ddq_id or slugify(path.name)

    summary = f"File: {path.name}."
    reader_error: Optional[str] = None
    ddq: Optional[ddq_reader.DDQ] = None
    try:
        ddq = ddq_reader.read_ddq(path)
    except Exception as e:  # noqa: BLE001 -- any parse failure means "not a DDQ we can read"
        reader_error = str(e)

    if ddq is not None:
        if limit_questions is not None:
            ddq.questions = ddq.questions[:limit_questions]
        sample = "; ".join(q.text for q in ddq.questions[:3])
        summary = f"File: {path.name}. Sheet: {ddq.sheet_name!r}. Sample questions: {sample}"

    investors = load_investors()
    category_thresholds = load_category_thresholds()
    as_of_date = config.get("as_of_date")

    if ddq is None:
        print(f"[skip] {path.name}: could not be read as a DDQ ({reader_error})")
        state = store.DDQState(
            id=ddq_id, source_path=str(path), sheet_name="", sender=sender,
            date_received=datetime.now(timezone.utc).isoformat(), status=store.STATUS_NEW,
        )
        store.save_state(state)
        return state

    is_ddq, reason = drafter.confirm_is_ddq(llm_config, summary)
    if not is_ddq:
        print(f"[skip] {path.name}: the model does not think this is a DDQ ({reason})")
        state = store.DDQState(
            id=ddq_id, source_path=str(path), sheet_name=ddq.sheet_name, sender=sender,
            date_received=datetime.now(timezone.utc).isoformat(), status=store.STATUS_NEW,
        )
        store.save_state(state)
        return state

    print(f"[process] {path.name}: {len(ddq.questions)} questions, sender={sender}")

    # Built and saved incrementally -- one question's failure (e.g. a
    # daily quota hit) must not discard every question already scored.
    state = store.DDQState(
        id=ddq_id, source_path=str(path), sheet_name=ddq.sheet_name, sender=sender,
        date_received=datetime.now(timezone.utc).isoformat(), status=store.STATUS_NEW,
        investor_name=ddq.investor_name,
    )
    store.save_state(state)

    for i, question in enumerate(ddq.questions, start=1):
        print(f"  [{i}/{len(ddq.questions)}] {question.id}: {question.text[:70]}")
        try:
            qs = score_and_draft_question(
                question, sender, llm_config, config, investors, category_thresholds, as_of_date,
            )
        except Exception as e:  # noqa: BLE001 -- save what we have (e.g. a quota error) and re-raise
            state.status = store.STATUS_NEW if not state.questions else store.STATUS_REVIEWED
            store.save_state(state)
            print(f"      !! failed: {e}")
            print(f"      ({len(state.questions)}/{len(ddq.questions)} questions saved to "
                  f"data/ddqs/{ddq_id}/state.json before the failure)")
            raise
        print(f"      -> {qs.route} (C={qs.confidence:.2f} R={qs.risk:.2f})"
              + (f" guardrails={qs.guardrails_fired}" if qs.guardrails_fired else ""))
        state.questions.append(qs)
        for name in qs.guardrails_fired:
            state.guardrails_fired_summary[name] = state.guardrails_fired_summary.get(name, 0) + 1
        store.save_state(state)  # incremental: survives a crash on a later question

    state.status = store.STATUS_REVIEWED
    store.save_state(state)
    _notify_reviewer_stub(state, config)
    return state


def run_demo(limit_questions: Optional[int] = None) -> list[store.DDQState]:
    """Processes every file in demo_inbox/ (section 12 milestone 5). Each
    gets the default demo sender."""
    config = load_config()
    llm_config = config["llm"]
    results = []
    for path in sorted(DEMO_INBOX_DIR.glob("*")):
        if path.name.startswith("."):
            continue
        state = process_ddq(path, DEMO_DEFAULT_SENDER, llm_config, config, limit_questions=limit_questions)
        results.append(state)
    return results


def check_mailbox_and_process() -> list[store.DDQState]:
    """Section 9 step 1 for the real mailbox (folder or gmail mode, per
    MAIL_MODE): finds new emails via mail.py, scores each one, and marks it
    processed. This is what app.py's 'Check mailbox' button calls -- never
    demo_inbox/, which is only for `agent.py --demo` (section 12 milestone 5).

    Gmail mode de-dup, two checks (section 9): mail.check_for_new_ddqs()
    already excludes messages carrying the 'DDQ-Agent/Processed' label;
    the gmail_message_id lookup here is the backstop for the rare case
    where a crash happened after process_ddq() saved state but before the
    label was applied -- in that case the label is still missing (so the
    message comes back), but we must not create a second DDQ for it. We
    re-apply the label (self-heals the missing label) and skip."""
    config = load_config()
    llm_config = config["llm"]
    results = []
    for incoming in mail.check_for_new_ddqs():
        if incoming.gmail_id and store.find_by_gmail_message_id(incoming.gmail_id):
            mail.mark_processed(incoming)
            continue
        ddq_id = f"{slugify(incoming.path.name)}-{incoming.received_at[:10]}"
        state = process_ddq(incoming.path, incoming.sender, llm_config, config, ddq_id=ddq_id)
        if incoming.gmail_id:
            state.gmail_message_id = incoming.gmail_id
        if incoming.gmail_thread_id:
            state.gmail_thread_id = incoming.gmail_thread_id
        if incoming.message_id:
            state.original_message_id = incoming.message_id
        if incoming.body_text:
            # email_template.py's heuristic, run once here rather than on
            # every render -- the original body text itself is never kept.
            state.recipient_first_name = email_template.extract_first_name_from_signature(incoming.body_text)
        if incoming.gmail_id or incoming.message_id or incoming.body_text:
            store.save_state(state)
        mail.mark_processed(incoming)
        results.append(state)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="DDQ agent CLI")
    parser.add_argument("--demo", action="store_true", help="process every file in demo_inbox/")
    parser.add_argument("--limit", type=int, default=None, help="only score the first N questions per file (smoke-testing)")
    args = parser.parse_args()

    if args.demo:
        states = run_demo(limit_questions=args.limit)
        print(f"\nProcessed {len(states)} DDQ(s).")
        for s in states:
            print(f"  {s.id}: {s.status}, {len(s.questions)} questions -> data/ddqs/{s.id}/state.json")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
