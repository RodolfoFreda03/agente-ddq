"""Tests for store.py's backward/forward compatibility: a state.json
written before a new DDQState field existed must still load without
crashing (confirmed live: a Harbourline state.json saved before
gmail_thread_id existed crashed app.py with
'AttributeError: DDQState object has no attribute gmail_thread_id' the
moment the page tried to read it)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store

TEST_DDQ_ID = "test-backward-compat-ddq"


def _cleanup():
    shutil.rmtree(store.ddq_dir(TEST_DDQ_ID), ignore_errors=True)


def test_load_state_without_new_fields_falls_back_to_none():
    _cleanup()
    try:
        # A state.json shaped like one saved before gmail_message_id,
        # investor_name, recipient_first_name, original_message_id and
        # gmail_thread_id existed -- only the original fields.
        old_state = {
            "id": TEST_DDQ_ID,
            "source_path": "demo_inbox/x.xlsx",
            "sheet_name": "Sheet1",
            "sender": "investor@example.com",
            "date_received": "2026-01-01T00:00:00+00:00",
            "status": "Sent",
            "questions": [
                {"id": "Q01", "section": "A", "text": "Who is the depositary?", "row": 5},
            ],
            "guardrails_fired_summary": {},
            "approved_by": "Demo Reviewer",
            "sent_at": "2026-01-01T01:00:00+00:00",
            "final_file_path": "output/x-final.xlsx",
            "reply_eml_path": None,
        }
        store.ddq_dir(TEST_DDQ_ID).mkdir(parents=True, exist_ok=True)
        store.state_path(TEST_DDQ_ID).write_text(json.dumps(old_state), encoding="utf-8")

        loaded = store.load_state(TEST_DDQ_ID)

        assert loaded.gmail_thread_id is None
        assert loaded.gmail_message_id is None
        assert loaded.investor_name is None
        assert loaded.recipient_first_name is None
        assert loaded.original_message_id is None
        assert loaded.status == "Sent"
        assert len(loaded.questions) == 1
        assert loaded.questions[0].id == "Q01"
    finally:
        _cleanup()


def test_load_state_ignores_unknown_keys_from_a_newer_or_hand_edited_file():
    """The inverse case: a state.json carrying a key that isn't a current
    DDQState field (e.g. saved by a newer version, or hand-edited) must
    not crash either -- unknown keys are dropped, not passed through."""
    _cleanup()
    try:
        state_with_extra_key = {
            "id": TEST_DDQ_ID,
            "source_path": "demo_inbox/x.xlsx",
            "sheet_name": "Sheet1",
            "sender": "investor@example.com",
            "date_received": "2026-01-01T00:00:00+00:00",
            "questions": [
                {"id": "Q01", "section": None, "text": "...", "row": 5, "a_future_field": "x"},
            ],
            "a_field_that_does_not_exist_yet": "some value",
        }
        store.ddq_dir(TEST_DDQ_ID).mkdir(parents=True, exist_ok=True)
        store.state_path(TEST_DDQ_ID).write_text(json.dumps(state_with_extra_key), encoding="utf-8")

        loaded = store.load_state(TEST_DDQ_ID)
        assert loaded.id == TEST_DDQ_ID
        assert loaded.questions[0].id == "Q01"
    finally:
        _cleanup()


def test_save_then_load_roundtrip_is_unaffected():
    """The filtering must not break the normal, same-version case."""
    _cleanup()
    try:
        state = store.DDQState(
            id=TEST_DDQ_ID, source_path="x.xlsx", sheet_name="s", sender="a@b.example",
            date_received="2026-01-01T00:00:00+00:00", gmail_thread_id="thread-123",
        )
        store.save_state(state)
        loaded = store.load_state(TEST_DDQ_ID)
        assert loaded.gmail_thread_id == "thread-123"
    finally:
        _cleanup()
