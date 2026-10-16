"""Real Gmail access via OAuth (CLAUDE.md section 9, milestone 9).

Reading: finds eligible DDQ emails and labels them processed (see
check_gmail_inbox/mark_gmail_processed below) -- unchanged.

Sending (added after the user reviewed the step-3 reply and explicitly
asked for real delivery, reversing the earlier read-only-only decision):
send_gmail() sends via the Gmail API, in the same thread as the original
email (threadId + In-Reply-To/References), and only ever runs after the
human's "Send" click + confirmation in app.py and mail.py's own guardrail
checks in send_final_reply() (rule 6: approved + correct recipient). It
raises MailSendError on any failure or an API response with no confirmed
message id -- app.py must never show "Sent" except after this returns
successfully. Folder mode (MAIL_MODE=folder) is unaffected: it still only
ever writes a local .eml record, so it stays available as a safe,
network-free way to demo the interface.

Imported lazily by mail.py only when MAIL_MODE=gmail (see README
"Connecting Gmail" for the one-time setup: credentials.json, token.json).
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv(dotenv_path=".env")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]
# gmail.send was added, then reverted back out here: token.json's refresh
# token was granted for gmail.modify only (the original OAuth consent),
# and Google's refresh endpoint rejects a refresh request for a scope
# that is a strict superset of what was actually granted (confirmed
# live: 'invalid_scope' on every single Gmail API call, not just
# sending -- including reading, which is what silently broke "Check
# mailbox"). send_gmail()/MailSendError below are unreachable until the
# user redoes the OAuth consent (re-add gmail.send in Cloud Console, then
# delete token.json and re-run get_credentials() so a human can complete
# the browser consent screen) -- that step still needs a human; this file
# cannot do it alone. Until then, SCOPES must match what token.json
# actually has, so reading keeps working.
CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("token.json")


class MailSendError(Exception):
    """A real Gmail send was attempted but failed: an API error, or a
    response with no confirmed message id. app.py must never mark a DDQ
    'Sent' except after send_gmail() returns without raising this."""

LABEL_NAME = os.environ.get("GMAIL_LABEL", "DDQ-Agent")
PROCESSED_LABEL_NAME = f"{LABEL_NAME}/Processed"
LOOKBACK_DAYS = 30  # "received in the last 30 days"

GMAIL_ATTACHMENTS_DIR = Path("mailbox") / "_gmail_attachments"
ATTACHMENT_SUFFIXES = {".xlsx", ".docx"}


def get_credentials() -> Credentials:
    """Loads token.json (refreshing it if expired) or, the first time,
    runs the OAuth flow once: opens the browser, the user signs in and
    consents, and the result is saved to token.json so this never happens
    again. Never prints the contents of either file."""
    creds: Optional[Credentials] = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"{CREDENTIALS_PATH} not found. Download it from Google Cloud Console "
                "(OAuth client, Desktop app type) and save it in the project folder."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)
    return creds


def get_service():
    return build("gmail", "v1", credentials=get_credentials())


def _get_or_create_label(service, name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == name:
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
        .execute()
    )
    return created["id"]


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _walk_parts(payload: dict) -> list[dict]:
    parts = [payload]
    result = []
    while parts:
        part = parts.pop()
        if part.get("parts"):
            parts.extend(part["parts"])
        else:
            result.append(part)
    return result


def _find_text_body(payload: dict) -> Optional[str]:
    """The message's text/plain body, for email_template.py's
    signature-name heuristic only -- not stored anywhere beyond the first
    name it extracts (see agent.py's check_mailbox_and_process)."""
    for part in _walk_parts(payload):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return None


def _download_attachment(service, message_id: str, part: dict, dest_dir: Path) -> Optional[Path]:
    filename = part.get("filename") or ""
    if Path(filename).suffix.lower() not in ATTACHMENT_SUFFIXES:
        return None
    attachment_id = part.get("body", {}).get("attachmentId")
    if not attachment_id:
        return None
    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = base64.urlsafe_b64decode(attachment["data"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{message_id}__{filename}"
    out_path.write_bytes(data)
    return out_path


def build_query(investor: str, lookback_days: int = LOOKBACK_DAYS) -> str:
    """The Gmail search query (section 9, redefined a second time): sender +
    attachment + recency only. The rule is now "process every message from
    the investor with an .xlsx/.docx attachment from the last N days, read
    or unread, labelled or not, in a new or an old thread, unless that
    exact message id was already processed" -- no label:"DDQ-Agent"
    requirement any more (confirmed live as the actual cause of "no new
    DDQs found": the running app's real bottleneck that day wasn't this
    query at all -- MAIL_MODE was "folder", so it was reading an empty
    local folder, never Gmail -- but the label requirement was a second,
    independent fragility worth removing on its own: a label depends on a
    Gmail filter or manual tagging actually having run first, which is one
    more thing that can silently not happen).

    Deliberately has NO is:unread/is:read term -- read status is not a
    filter, and the agent must never change it either.

    Deliberately has NO -label:"...Processed" term: Gmail's search index
    applies label exclusion at THREAD granularity, not per-message --
    confirmed live, a second message (its own labelIds never included
    Processed) was silently hidden because Gmail grouped it into the same
    thread as an earlier, already-processed message. The Processed check
    happens exclusively in is_eligible() below (per-message labelIds) and
    in store.find_by_gmail_message_id() (per message id) -- both correct
    at message granularity, unlike the search query."""
    return f"from:{investor} has:attachment newer_than:{lookback_days}d"


def is_eligible(
    sender: str,
    investor: str,
    label_ids: list[str],
    processed_label_id: str,
    received_at: datetime,
    lookback_days: int = LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> bool:
    """Local re-verification of every query condition, so a Gmail search
    quirk can never widen what the agent reads (CLAUDE.md section "Never
    do"). Pure function, no API calls -- easy to unit-test directly."""
    if sender != investor:
        return False
    if processed_label_id in label_ids:
        return False
    now = now or datetime.now(timezone.utc)
    if (now - received_at).days > lookback_days:
        return False
    return True


def check_gmail_inbox() -> list["mail.IncomingEmail"]:  # noqa: F821 -- avoid a circular import, see below
    """Finds emails sent by DEMO_INVESTOR_EMAIL, with a .xlsx/.docx
    attachment, received in the last LOOKBACK_DAYS days, not already
    labeled PROCESSED_LABEL_NAME -- no LABEL_NAME requirement any more
    (see build_query's docstring). Read/unread status is never filtered
    on and never changed (an already-opened email is processed just like
    an unread one). The Gmail search query already filters tightly; every
    result is re-checked locally (is_eligible) before being returned, so
    a query bug can never widen what the agent reads. Never touches any
    other email (CLAUDE.md section "Never do")."""
    from mail import IncomingEmail  # local import: mail.py imports this module lazily too

    investor = (os.environ.get("DEMO_INVESTOR_EMAIL") or "").strip().lower()
    if not investor:
        raise RuntimeError("DEMO_INVESTOR_EMAIL is not set in .env.")

    service = get_service()
    # Still created so a human's own Gmail filter can keep using it for
    # organisation -- no longer required for a message to be found.
    _get_or_create_label(service, LABEL_NAME)
    processed_label_id = _get_or_create_label(service, PROCESSED_LABEL_NAME)

    query = build_query(investor)
    response = service.users().messages().list(userId="me", q=query).execute()
    message_ids = [m["id"] for m in response.get("messages", [])]

    emails: list[IncomingEmail] = []
    for message_id in message_ids:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = message["payload"].get("headers", [])
        sender_header = _header(headers, "From")
        sender = parseaddr(sender_header)[1].strip().lower()
        label_ids = message.get("labelIds", [])
        received_at_dt = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc)

        if not is_eligible(sender, investor, label_ids, processed_label_id, received_at_dt):
            continue

        attachment_path = None
        for part in _walk_parts(message["payload"]):
            attachment_path = _download_attachment(service, message_id, part, GMAIL_ATTACHMENTS_DIR)
            if attachment_path:
                break
        if attachment_path is None:
            continue

        emails.append(
            IncomingEmail(
                path=attachment_path,
                sender=sender,
                received_at=received_at_dt.isoformat(),
                subject=_header(headers, "Subject"),
                message_id=_header(headers, "Message-ID") or None,
                gmail_id=message_id,
                gmail_thread_id=message.get("threadId"),
                body_text=_find_text_body(message["payload"]),
            )
        )
    return emails


def mark_gmail_processed(incoming: "mail.IncomingEmail") -> None:  # noqa: F821
    """Adds the 'DDQ-Agent/Processed' label only. Never touches the
    read/unread status (an already-opened email must stay exactly as the
    user left it), and never deletes, moves, or otherwise touches any
    other message."""
    if not incoming.gmail_id:
        raise ValueError("IncomingEmail has no gmail_id -- it was not read from Gmail.")
    service = get_service()
    processed_label_id = _get_or_create_label(service, PROCESSED_LABEL_NAME)
    service.users().messages().modify(
        userId="me",
        id=incoming.gmail_id,
        body={"addLabelIds": [processed_label_id]},
    ).execute()


def _resolve_thread_id(service, gmail_message_id: Optional[str]) -> Optional[str]:
    """Looks up the thread id of the original message when the caller
    didn't already have it stored (point 2: an older DDQ saved before
    gmail_thread_id existed). Never raises -- a lookup failure (message
    deleted, API hiccup) just means the reply goes out as a new email
    instead of in-thread, which is still a correct send, not a failure."""
    if not gmail_message_id:
        return None
    try:
        original = service.users().messages().get(
            userId="me", id=gmail_message_id, format="minimal",
        ).execute()
        return original.get("threadId")
    except HttpError:
        return None


def send_gmail(
    to_addr: str,
    subject: str,
    body: str,
    attachment_path: Optional[Path] = None,
    attachment_filename: Optional[str] = None,
    in_reply_to_message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> dict:
    """Sends a real email via the Gmail API. Threading, in priority order
    (point 2): use thread_id if given; else look it up from
    gmail_message_id; else send as a new email (same subject, no
    threadId/In-Reply-To) -- never an error on its own. Returns the API
    response (has 'id' and 'threadId') only on confirmed success; raises
    MailSendError otherwise -- the caller (mail.py's send_final_reply,
    itself only called after app.py's confirmed human click) must treat
    any exception as "not sent"."""
    service = get_service()
    if not thread_id:
        thread_id = _resolve_thread_id(service, gmail_message_id)

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["From"] = os.environ.get("GMAIL_USER") or ""
    msg["Subject"] = subject
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id
    msg.set_content(body)

    if attachment_path is not None:
        data = attachment_path.read_bytes()
        subtype = (
            "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if attachment_path.suffix.lower() == ".xlsx"
            else "vnd.openxmlformats-officedocument.wordprocessingml.document"
            if attachment_path.suffix.lower() == ".docx"
            else "octet-stream"
        )
        msg.add_attachment(
            data, maintype="application", subtype=subtype,
            filename=attachment_filename or attachment_path.name,
        )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    request_body: dict = {"raw": raw}
    if thread_id:
        request_body["threadId"] = thread_id

    try:
        response = service.users().messages().send(userId="me", body=request_body).execute()
    except HttpError as e:
        raise MailSendError(f"Gmail API refused the send: {e}") from e

    if not response.get("id"):
        raise MailSendError(f"Gmail API returned no message id -- treating as failed: {response!r}")
    return response
