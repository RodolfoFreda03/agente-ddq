"""Streamlit interface (CLAUDE.md section 8). Three steps:
1. DDQs found in the mailbox.
2. The agent's reviewed version, completed by the human.
3. Final version back to the agent, then sending.

Never calls the model directly -- only agent.py, store.py, memory.py and
mail.py (section 13).
"""

from __future__ import annotations

import difflib
import html
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

import agent
import ddq_writer
import demo
import email_template
import mail
import mail_gmail
import memory
import reason_text
import scoring
import store
import style

try:
    # Streamlit Community Cloud's "Secrets" UI populates st.secrets, not
    # os.environ -- every os.environ.get(...) call in this project (the
    # established pattern, matching local .env via python-dotenv) would
    # otherwise never see them. Bridged once, here, before anything reads
    # an env var. st.secrets raises if no secrets.toml exists at all (the
    # normal case for local development, where .env is used instead).
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:  # noqa: BLE001 -- no secrets.toml locally is expected, not an error
    pass

if demo.is_enabled():
    demo.setup()  # before anything else touches store.py/memory.py's data dirs

st.set_page_config(page_title="DDQ Agent", page_icon="assets/alken_fund_icon_square.png", layout="wide")
style.inject_once()
style.header()
if demo.is_enabled():
    st.markdown(style.demo_banner(), unsafe_allow_html=True)

CONFIG = agent.load_config()
AUTO_CHECK_SECONDS = CONFIG["email"]["check_interval_minutes"] * 60


# ── helpers ──────────────────────────────────────────────────────────────


def _format_readable_datetime(iso_string: Optional[str]) -> str:
    """'15 October 2026 at 16:47', local time, instead of a raw ISO
    timestamp. Not %-d/%-H (platform-specific strftime extensions): built
    manually so this also works on Windows."""
    if not iso_string:
        return ""
    local_dt = datetime.fromisoformat(iso_string).astimezone()
    return f"{local_dt.day} {local_dt.strftime('%B %Y')} at {local_dt.strftime('%H:%M')}"


def _source_excerpt(reference: str, max_chars: int = 600) -> str:
    """Looks up a memorandum note by its reference (e.g. 'S11') for display
    under a draft. Re-fetched on each render -- the vault is tiny (21
    notes), so this is cheap."""
    for note in memory.load_vault_notes(note_type="source"):
        if note.reference == reference:
            text = note.content.strip()
            return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return "(source not found)"


def _auto_check_mailbox_if_due() -> None:
    if demo.is_enabled():
        return  # demo.py: "processing new questionnaires is disabled" -- no model calls
    last_check = st.session_state.get("_last_mailbox_check")
    now = datetime.now(timezone.utc)
    if last_check is None or (now - last_check).total_seconds() >= AUTO_CHECK_SECONDS:
        st.session_state["_last_mailbox_check"] = now
        try:
            agent.check_mailbox_and_process()
        except Exception as e:  # noqa: BLE001 -- never crash the page over an auto-check
            st.session_state["_last_mailbox_error"] = str(e)


def _edit_rate(draft: Optional[str], final: Optional[str]) -> float:
    if not draft or not final:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, draft, final).ratio()


def _slugify(text: str) -> str:
    import re as _re

    return _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "answer"


def _write_approved_answer_note(question: store.QuestionState, approved_by: str) -> Path:
    """section 7.2: one note per answer, linking to its sources. A new
    approval of the same question creates a new version; the previous one
    is archived (kept, not deleted)."""
    base_slug = _slugify(question.text)
    existing = sorted(memory.APPROVED_ANSWERS_DIR.glob(f"{base_slug}*.md"))
    version = 1
    for path in existing:
        note = memory.load_note(path)
        if note.metadata.get("type") == "approved_answer" and not path.name.startswith("_archived-"):
            version = max(version, int(note.metadata.get("version", 1)) + 1)
            archived_path = memory.APPROVED_ANSWERS_DIR / f"_archived-v{note.metadata.get('version', 1)}-{path.name}"
            path.rename(archived_path)

    today = date.today()
    content = f"**Question:** {question.text}\n\n**Answer:** {question.final_answer}\n"
    return memory.write_note(
        content,
        memory.APPROVED_ANSWERS_DIR / f"{base_slug}.md",
        type="approved_answer",
        category=question.category,
        risk=round(question.risk, 2),
        approved_by=approved_by,
        approval_date=today,
        expiry=today + timedelta(days=365),
        version=version,
        edit_rate=round(question.edit_rate or 0.0, 3),
        sources=[f"[[{_source_note_name(ref)}]]" for ref in question.cited_sources],
        seed=False,
    )


def _source_note_name(reference: str) -> str:
    for note in memory.load_vault_notes(note_type="source"):
        if note.reference == reference:
            return note.path.stem
    return reference


# ── navigation ───────────────────────────────────────────────────────────

if "view" not in st.session_state:
    st.session_state["view"] = "list"
if "selected_ddq" not in st.session_state:
    st.session_state["selected_ddq"] = None

_auto_check_mailbox_if_due()


def go_to_list() -> None:
    st.session_state["view"] = "list"
    st.session_state["selected_ddq"] = None


def go_to_review(ddq_id: str) -> None:
    st.session_state["view"] = "review"
    st.session_state["selected_ddq"] = ddq_id


# ── Step 1: DDQs found in the mailbox ───────────────────────────────────


def render_list() -> None:
    if demo.is_enabled():
        col1, col2, col3 = st.columns([4, 1, 1])
    else:
        col1, col2 = st.columns([5, 1])
        col3 = None

    with col1:
        st.subheader("DDQs")

    with col2:
        if st.button("Check mailbox", use_container_width=True):
            if demo.is_enabled():
                st.markdown(
                    style.message(
                        "Demo version: processing new questionnaires is disabled.", kind="neutral",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                try:
                    with st.spinner("Checking mailbox…"):
                        new_states = agent.check_mailbox_and_process()
                except Exception as e:  # noqa: BLE001 -- the explicit button click must
                    # never show a raw traceback either (it previously had no
                    # try/except at all, unlike the background auto-check below).
                    st.markdown(
                        style.message(f"Could not check the mailbox: {html.escape(str(e))}", kind="error"),
                        unsafe_allow_html=True,
                    )
                else:
                    st.session_state["_last_mailbox_check"] = datetime.now(timezone.utc)
                    if new_states:
                        st.markdown(
                            style.message(f"Found and processed {len(new_states)} new DDQ(s).", kind="success"),
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(style.message("No new DDQs found.", kind="neutral"), unsafe_allow_html=True)

    if col3 is not None:
        with col3:
            if st.button("Reset demo", use_container_width=True):
                demo.reset()
                st.session_state["view"] = "list"
                st.session_state["selected_ddq"] = None
                st.markdown(
                    style.message("Demo reset to its starting snapshot.", kind="success"),
                    unsafe_allow_html=True,
                )
                st.rerun()

    if st.session_state.get("_last_mailbox_error"):
        error_text = html.escape(st.session_state["_last_mailbox_error"])
        st.markdown(
            style.message(f"Last automatic mailbox check failed: {error_text}", kind="error"),
            unsafe_allow_html=True,
        )

    states = store.load_all_states()
    states.sort(key=lambda s: s.date_received, reverse=True)

    if not states:
        st.markdown(
            style.message(
                "No DDQs yet. Click <strong>Check mailbox</strong>, "
                "or run <code>python agent.py --demo</code> first.",
                kind="neutral",
            ),
            unsafe_allow_html=True,
        )
        return

    for s in states:
        with st.container(border=True):
            cols = st.columns([3, 2, 2, 2, 2])
            cols[0].markdown(style.plain_text(s.sender, bold=True), unsafe_allow_html=True)
            cols[1].markdown(_format_readable_datetime(s.date_received))
            cols[2].markdown(f"{len(s.questions)} questions")
            cols[3].markdown(style.status_badge(s.status), unsafe_allow_html=True)
            if s.questions:
                st.markdown(style.route_count_badges([q.route for q in s.questions]), unsafe_allow_html=True)
            if cols[4].button("Open", key=f"open-{s.id}", type="primary"):
                go_to_review(s.id)
                st.rerun()


# ── Step 2: review ───────────────────────────────────────────────────────


def render_review(ddq_id: str) -> None:
    ddq = store.load_state(ddq_id)

    st.button("← Back to list", on_click=go_to_list)
    st.markdown(f'<h3>DDQ from {style.plain_text(ddq.sender)}</h3>', unsafe_allow_html=True)
    st.caption(f"Received {_format_readable_datetime(ddq.date_received)} · {ddq.sheet_name} · status: {ddq.status}")

    approved_count = ddq.approved_count()
    total = len(ddq.questions)
    st.progress(approved_count / total if total else 0, text=f"{approved_count}/{total} approved")

    questions_by_priority = sorted(ddq.questions, key=lambda q: q.priority, reverse=True)

    for q in questions_by_priority:
        with st.container(border=True):
            header_cols = st.columns([5, 1])
            with header_cols[0]:
                st.markdown(
                    f"<strong>{style.escape_text(q.id)}</strong> — {style.escape_text(q.text)}",
                    unsafe_allow_html=True,
                )
            with header_cols[1]:
                st.markdown(style.route_badge(q.route), unsafe_allow_html=True)

            if q.possible_prompt_injection:
                st.markdown(
                    style.message(
                        "Possible prompt injection: this question tried to instruct the agent. Never acted on.",
                        kind="error",
                    ),
                    unsafe_allow_html=True,
                )
            if q.flagged_for_check:
                st.caption("Flagged for a random spot check.")

            answer_label = (
                "Your answer" if q.route in (scoring.HUMAN_ONLY, scoring.GAP)
                else "Agent's answer — edit if needed"
            )
            st.markdown(f'<p class="answer-label">{answer_label}</p>', unsafe_allow_html=True)
            answer_value = st.text_area(
                answer_label,
                value=q.final_answer or "",
                key=f"answer-{q.id}",
                height=120,
                placeholder="Write the answer here…",
                label_visibility="collapsed",
            )
            st.caption("Changes are saved automatically.")
            if answer_value != (q.final_answer or ""):
                q.final_answer = answer_value
                q.edit_rate = _edit_rate(q.draft_text, q.final_answer)
                store.save_state(ddq)

            if q.cited_sources:
                with st.expander(f"Cited sources: {', '.join(q.cited_sources)}"):
                    for ref in q.cited_sources:
                        st.markdown(f"**{ref}**")
                        st.text(_source_excerpt(ref))

            with st.expander("Why this route?"):
                st.markdown(
                    reason_text.plain_english_reason(
                        route=q.route,
                        category=q.category,
                        guardrails_fired=q.guardrails_fired,
                        possible_prompt_injection=q.possible_prompt_injection,
                        cited_sources=q.cited_sources,
                        figures_or_commitments=q.r2,
                        flagged_for_check=q.flagged_for_check,
                    )
                )

            approved = st.checkbox("Approved", value=q.approved, key=f"approved-{q.id}")
            if approved != q.approved:
                q.approved = approved
                store.save_state(ddq)

    st.divider()
    render_submit_section(ddq)


# ── Step 3: submit + send ────────────────────────────────────────────────


def render_submit_section(ddq: store.DDQState) -> None:
    all_approved = ddq.all_approved()
    submit_disabled = not all_approved or ddq.status in (store.STATUS_READY, store.STATUS_SENT)
    investor_name = ddq.investor_name or Path(ddq.source_path).stem.replace("_", " ")

    if st.button("Submit final version to the agent", disabled=submit_disabled, type="primary"):
        with st.spinner("Updating memory and building the final file…"):
            for q in ddq.questions:
                if q.approved and q.final_answer and q.route != scoring.HUMAN_ONLY:
                    _write_approved_answer_note(q, approved_by="Demo Reviewer")

            # Always a fresh Word document now, whatever format the DDQ
            # arrived in -- never a filled-in copy of the original file.
            output_path = Path("output") / f"{ddq.id}-final.docx"
            ddq_writer.write_word_response(ddq.questions, investor_name, output_path)

            ddq.final_file_path = str(output_path)
            ddq.status = store.STATUS_READY
            store.save_state(ddq)
        st.markdown(
            style.message("Final file built. Review the email preview below.", kind="success"),
            unsafe_allow_html=True,
        )
        st.rerun()

    if ddq.status not in (store.STATUS_READY, store.STATUS_SENT):
        if not all_approved:
            st.caption("All questions must be approved before submitting.")
        return

    has_gap_answers = any(q.route == scoring.GAP for q in ddq.questions)
    total = len(ddq.questions)
    reviewed_by_person = sum(1 for q in ddq.questions if q.route != scoring.AUTO)
    not_available = sum(1 for q in ddq.questions if q.route == scoring.GAP)

    st.markdown(
        style.message(
            f"{total} questions answered · {reviewed_by_person} reviewed by a person · "
            f"{not_available} marked as not available",
            kind="neutral",
        ),
        unsafe_allow_html=True,
    )

    st.subheader("Reply email preview")
    default_subject = email_template.build_subject(investor_name)
    default_body = email_template.build_body(
        recipient_first_name=ddq.recipient_first_name, has_gap_answers=has_gap_answers,
    )
    attachment_display_name = email_template.build_attachment_filename(investor_name, date.today())

    st.text_input("To", value=ddq.sender, disabled=True)
    subject = st.text_input("Subject", value=default_subject, key=f"subject-{ddq.id}")
    body = st.text_area("Message", value=default_body, key=f"body-{ddq.id}", height=200)
    st.caption(f"Attachment: {attachment_display_name}")
    if ddq.final_file_path and Path(ddq.final_file_path).exists():
        st.download_button(
            "Download Word document",
            data=Path(ddq.final_file_path).read_bytes(),
            file_name=attachment_display_name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    if ddq.status == store.STATUS_SENT:
        sent_at = _format_readable_datetime(ddq.sent_at)
        st.markdown(style.message(f"Sent on {sent_at}.", kind="success"), unsafe_allow_html=True)
        return

    send_clicked = st.button("Send", type="primary")
    if send_clicked:
        st.session_state[f"_confirm_send_{ddq.id}"] = True

    if st.session_state.get(f"_confirm_send_{ddq.id}"):
        st.markdown(
            f'<div class="confirm-box">Send to {style.plain_text(ddq.sender)}? This cannot be undone.</div>',
            unsafe_allow_html=True,
        )
        confirm_cols = st.columns(2)
        if confirm_cols[0].button("Yes, send", key=f"confirm-yes-{ddq.id}", type="primary"):
            if demo.is_enabled():
                st.markdown(
                    style.message(
                        "Demo version: sending is disabled. In production the email is "
                        "sent only after this confirmation.",
                        kind="neutral",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                try:
                    reply_result = mail.send_final_reply(
                        ddq_sender=ddq.sender,
                        subject=subject,
                        body=body,
                        attachment_path=Path(ddq.final_file_path),
                        all_questions_approved=ddq.all_approved(),
                        in_reply_to_message_id=ddq.original_message_id,
                        attachment_filename=attachment_display_name,
                        thread_id=ddq.gmail_thread_id,
                        gmail_message_id=ddq.gmail_message_id,
                    )
                except Exception as e:  # noqa: BLE001 -- deliberately broad: the user
                    # must never see a Python traceback for this action. Nothing
                    # was sent on this path: send_gmail() only returns (and
                    # mail.send_final_reply only returns a value) after a
                    # confirmed success, so any exception here -- a guardrail
                    # refusal, a real Gmail API failure, or an unexpected bug --
                    # means the message was never handed to the network, and
                    # status is never touched below.
                    st.markdown(
                        style.message(
                            f"The email could not be sent: {html.escape(str(e))}. Nothing was sent.",
                            kind="error",
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    # Reached only on a confirmed success: folder mode always
                    # succeeds here (it just wrote a local file); gmail mode
                    # only reaches here if send_gmail() returned without
                    # raising, which it does only for a response with a
                    # confirmed message id (see mail_gmail.send_gmail).
                    ddq.status = store.STATUS_SENT
                    ddq.sent_at = datetime.now(timezone.utc).isoformat()
                    ddq.approved_by = "Demo Reviewer"
                    ddq.reply_eml_path = (
                        str(reply_result) if isinstance(reply_result, Path)
                        else f"gmail:{reply_result.get('id')}"
                    )
                    store.save_state(ddq)
                    st.session_state[f"_confirm_send_{ddq.id}"] = False
                    st.markdown(style.message("Sent.", kind="success"), unsafe_allow_html=True)
                    st.rerun()
        if confirm_cols[1].button("Cancel", key=f"confirm-no-{ddq.id}"):
            st.session_state[f"_confirm_send_{ddq.id}"] = False
            st.rerun()


# ── render ───────────────────────────────────────────────────────────────

if st.session_state["view"] == "list":
    render_list()
else:
    render_review(st.session_state["selected_ddq"])

style.footer()
