"""Tests for the Gmail reading rule (CLAUDE.md section 9, as redefined):
read/unread status is never filtered on and never changed; eligible
messages are label + sender + attachment + received within 30 days + not
already labeled Processed; duplicates are avoided via that label AND via
the Gmail message id stored in state.json. No real Gmail API calls here --
get_service() is monkeypatched with a stub that records what would have
been sent.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent
import mail
import mail_gmail
import store

INVESTOR = "investor@northgate-pension.example"
PROCESSED_LABEL_ID = "Label_processed_123"


# ── build_query ──────────────────────────────────────────────────────────


def test_build_query_has_no_read_status_term():
    query = mail_gmail.build_query(INVESTOR)
    assert "is:unread" not in query
    assert "is:read" not in query


def test_build_query_has_the_other_required_terms():
    query = mail_gmail.build_query(INVESTOR)
    assert f"from:{INVESTOR}" in query
    assert "has:attachment" in query
    assert f"newer_than:{mail_gmail.LOOKBACK_DAYS}d" in query


def test_build_query_has_no_label_requirement():
    """Confirmed live as the second, independent cause behind 'no new DDQs
    found' (the first and main cause was MAIL_MODE=folder reading an empty
    local folder, never Gmail at all): the rule is now sender + attachment
    + recency only -- a message no longer needs the "DDQ-Agent" label to
    be found, since that label depends on a filter or manual tagging
    having already run, which is one more thing that can silently fail."""
    query = mail_gmail.build_query(INVESTOR)
    assert "label:" not in query


def test_build_query_has_no_processed_label_exclusion():
    """Confirmed live: Gmail's search index applies -label:"...Processed"
    at thread granularity, silently hiding a second, unprocessed message
    that Gmail grouped into an already-processed thread. The exclusion
    must happen only in is_eligible(), against each message's own
    labelIds, never in the query string."""
    query = mail_gmail.build_query(INVESTOR)
    assert "-label:" not in query
    assert mail_gmail.PROCESSED_LABEL_NAME not in query


# ── is_eligible ──────────────────────────────────────────────────────────


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_eligible_already_opened_email_still_counts():
    # no UNREAD label present at all -- must not matter
    received = NOW - timedelta(days=1)
    assert mail_gmail.is_eligible(
        INVESTOR, INVESTOR, label_ids=["DDQ-Agent"], processed_label_id=PROCESSED_LABEL_ID,
        received_at=received, now=NOW,
    )


def test_not_eligible_wrong_sender():
    received = NOW - timedelta(days=1)
    assert not mail_gmail.is_eligible(
        "someone-else@example.com", INVESTOR, label_ids=["DDQ-Agent"],
        processed_label_id=PROCESSED_LABEL_ID, received_at=received, now=NOW,
    )


def test_not_eligible_already_processed_label():
    received = NOW - timedelta(days=1)
    assert not mail_gmail.is_eligible(
        INVESTOR, INVESTOR, label_ids=["DDQ-Agent", PROCESSED_LABEL_ID],
        processed_label_id=PROCESSED_LABEL_ID, received_at=received, now=NOW,
    )


def test_eligible_exactly_at_the_30_day_boundary():
    received = NOW - timedelta(days=30)
    assert mail_gmail.is_eligible(
        INVESTOR, INVESTOR, label_ids=["DDQ-Agent"], processed_label_id=PROCESSED_LABEL_ID,
        received_at=received, now=NOW,
    )


def test_not_eligible_past_the_30_day_boundary():
    received = NOW - timedelta(days=31)
    assert not mail_gmail.is_eligible(
        INVESTOR, INVESTOR, label_ids=["DDQ-Agent"], processed_label_id=PROCESSED_LABEL_ID,
        received_at=received, now=NOW,
    )


# ── mark_gmail_processed: never touches read/unread status ────────────────


class _FakeModifyCall:
    def __init__(self, recorded: list[dict], user_id, id, body):
        recorded.append(body)

    def execute(self):
        return {}


class _FakeMessages:
    def __init__(self, recorded: list[dict]):
        self._recorded = recorded

    def modify(self, userId, id, body):
        return _FakeModifyCall(self._recorded, userId, id, body)


class _FakeLabels:
    def list(self, userId):
        class _Exec:
            def execute(self_inner):
                return {"labels": [{"name": mail_gmail.PROCESSED_LABEL_NAME, "id": PROCESSED_LABEL_ID}]}

        return _Exec()


class _FakeUsers:
    def __init__(self, recorded: list[dict]):
        self._recorded = recorded

    def messages(self):
        return _FakeMessages(self._recorded)

    def labels(self):
        return _FakeLabels()


class _FakeService:
    def __init__(self, recorded: list[dict]):
        self._recorded = recorded

    def users(self):
        return _FakeUsers(self._recorded)


def test_mark_gmail_processed_never_touches_read_status(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(mail_gmail, "get_service", lambda: _FakeService(recorded))

    incoming = mail.IncomingEmail(
        path=Path("x.xlsx"), sender=INVESTOR, received_at=NOW.isoformat(), gmail_id="msg-1",
    )
    mail_gmail.mark_gmail_processed(incoming)

    assert len(recorded) == 1
    body = recorded[0]
    assert "removeLabelIds" not in body
    assert body["addLabelIds"] == [PROCESSED_LABEL_ID]


# ── store.find_by_gmail_message_id ─────────────────────────────────────────


TEST_DDQ_IDS = ["test-dedup-aaa", "test-dedup-bbb"]


def _cleanup_test_ddqs():
    for ddq_id in TEST_DDQ_IDS:
        shutil.rmtree(store.ddq_dir(ddq_id), ignore_errors=True)


def test_find_by_gmail_message_id():
    _cleanup_test_ddqs()
    try:
        store.save_state(store.DDQState(
            id="test-dedup-aaa", source_path="a.xlsx", sheet_name="s", sender=INVESTOR,
            date_received=NOW.isoformat(), gmail_message_id="gmail-msg-aaa",
        ))
        store.save_state(store.DDQState(
            id="test-dedup-bbb", source_path="b.xlsx", sheet_name="s", sender=INVESTOR,
            date_received=NOW.isoformat(), gmail_message_id="gmail-msg-bbb",
        ))

        assert store.find_by_gmail_message_id("gmail-msg-aaa") == "test-dedup-aaa"
        assert store.find_by_gmail_message_id("gmail-msg-bbb") == "test-dedup-bbb"
        assert store.find_by_gmail_message_id("gmail-msg-does-not-exist") is None
    finally:
        _cleanup_test_ddqs()


# ── agent.check_mailbox_and_process: skip + self-heal on a state.json dup ──


def test_check_mailbox_skips_and_reheals_label_for_known_message_id(monkeypatch):
    """A message already recorded in state.json (gmail_message_id match)
    must not be reprocessed into a second DDQ, even if the query returned
    it again (e.g. the Processed label failed to apply after a crash) --
    but the label is still (re-)applied, so the query stops returning it."""
    _cleanup_test_ddqs()
    try:
        store.save_state(store.DDQState(
            id="test-dedup-aaa", source_path="a.xlsx", sheet_name="s", sender=INVESTOR,
            date_received=NOW.isoformat(), gmail_message_id="gmail-msg-aaa",
        ))

        incoming = mail.IncomingEmail(
            path=Path("a.xlsx"), sender=INVESTOR, received_at=NOW.isoformat(), gmail_id="gmail-msg-aaa",
        )
        monkeypatch.setattr(mail, "check_for_new_ddqs", lambda: [incoming])
        marked: list[mail.IncomingEmail] = []
        monkeypatch.setattr(mail, "mark_processed", lambda inc: marked.append(inc))

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("process_ddq must not run for an already-recorded gmail message id")

        monkeypatch.setattr(agent, "process_ddq", _fail_if_called)

        results = agent.check_mailbox_and_process()

        assert results == []  # nothing new was processed
        assert marked == [incoming]  # but the label is re-applied (self-heal)
    finally:
        _cleanup_test_ddqs()


# ── send_gmail: real delivery, confirmed-success-only contract ────────────


class _FakeSendCall:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeSendMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.sent_bodies: list[dict] = []

    def send(self, userId, body):
        self.sent_bodies.append(body)
        if self._error:
            raise self._error
        return _FakeSendCall(self._response)


class _FakeSendUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeSendService:
    def __init__(self, messages):
        self._messages = messages

    def users(self):
        return _FakeSendUsers(self._messages)


def test_send_gmail_succeeds_and_returns_the_api_response(monkeypatch):
    messages = _FakeSendMessages(response={"id": "new-msg-1", "threadId": "thread-1"})
    monkeypatch.setattr(mail_gmail, "get_service", lambda: _FakeSendService(messages))

    response = mail_gmail.send_gmail(
        to_addr=INVESTOR, subject="Re: DDQ", body="Dear Sarah,", thread_id="thread-1",
    )

    assert response == {"id": "new-msg-1", "threadId": "thread-1"}
    assert messages.sent_bodies[0]["threadId"] == "thread-1"


def test_send_gmail_raises_on_missing_id_rather_than_silently_succeeding(monkeypatch):
    messages = _FakeSendMessages(response={})  # no 'id' -- must not be treated as success
    monkeypatch.setattr(mail_gmail, "get_service", lambda: _FakeSendService(messages))

    try:
        mail_gmail.send_gmail(to_addr=INVESTOR, subject="x", body="y")
        assert False, "expected MailSendError"
    except mail_gmail.MailSendError:
        pass


def test_send_gmail_raises_mailsenderror_on_http_error(monkeypatch):
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 403
        reason = "Forbidden"

    error = HttpError(resp=_FakeResp(), content=b'{"error": "insufficientPermissions"}')
    messages = _FakeSendMessages(error=error)
    monkeypatch.setattr(mail_gmail, "get_service", lambda: _FakeSendService(messages))

    try:
        mail_gmail.send_gmail(to_addr=INVESTOR, subject="x", body="y")
        assert False, "expected MailSendError"
    except mail_gmail.MailSendError:
        pass
