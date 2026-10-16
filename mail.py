"""Mailbox access, guarded (CLAUDE.md sections 3, 6, 9).

Two modes, selected by MAIL_MODE in .env:
- "folder": a simulated mailbox under mailbox/ (inbox, sent, processed) --
  no network calls, safe for demos and automated testing. Also the
  offline fallback for the live demo. Reading AND sending are both purely
  local in this mode; nothing ever leaves the machine.
- "gmail": the real Gmail API with OAuth (google-api-python-client +
  google-auth-oauthlib; scopes gmail.modify + gmail.send). See README
  "Connecting Gmail". Reading finds eligible DDQ emails and labels them
  processed. Sending (send_final_reply only) is a REAL send, in the same
  thread as the original email -- added after the user reviewed the
  step-3 reply and asked for real delivery. It only ever runs after the
  human's "Send" click + confirmation in app.py, on top of this module's
  own guardrail checks (rule 6: approved + correct recipient), and raises
  mail_gmail.MailSendError on any failure -- the caller must never show
  "Sent" except after a confirmed success.

send_internal_notification() always writes a local .eml record, in
either mode: it was never wired to anything that calls it (deferred,
see agent.py's _notify_reviewer_stub) and wasn't part of what the user
asked to make real.

agent.py and app.py call through check_for_new_ddqs(), mark_processed(),
send_internal_notification() and send_final_reply() only -- never anything
mode-specific.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

MAILBOX_DIR = Path("mailbox")
INBOX_DIR = MAILBOX_DIR / "inbox"
SENT_DIR = MAILBOX_DIR / "sent"
PROCESSED_DIR = MAILBOX_DIR / "processed"
EML_ATTACHMENTS_DIR = INBOX_DIR / "_eml_attachments"

ATTACHMENT_SUFFIXES = {".xlsx", ".docx"}


class MailGuardError(Exception):
    """Raised when a send would violate a guardrail (section 6, rule 6):
    wrong recipient, missing approval, or a non-whitelisted internal
    address. Never bypassed by a caller argument -- mail.py re-derives the
    allowed recipient itself."""


def mail_mode() -> str:
    mode = os.environ.get("MAIL_MODE", "folder").strip().lower()
    if mode not in ("folder", "gmail"):
        raise ValueError(f"Unknown MAIL_MODE: {mode!r} (expected 'folder' or 'gmail')")
    return mode


@dataclass
class IncomingEmail:
    path: Path  # the attachment file, ready for ddq_reader.read_ddq()
    sender: str
    received_at: str  # ISO datetime
    subject: str = ""
    message_id: Optional[str] = None  # gmail mode: needed to reply in-thread
    gmail_id: Optional[str] = None  # gmail mode: needed to label + for dedup
    gmail_thread_id: Optional[str] = None  # gmail mode: needed to reply in the same thread
    body_text: Optional[str] = None  # the original message body, for email_template.py's
    # signature-name heuristic only -- never stored beyond the first-name it extracts


# ── folder mode ───────────────────────────────────────────────────────────


def _sender_for(path: Path) -> str:
    sidecar = path.parent / f"{path.name}.sender.txt"
    if sidecar.exists():
        sender = sidecar.read_text(encoding="utf-8").strip()
        if sender:
            return sender
    return os.environ.get("DEMO_INVESTOR_EMAIL") or "investor@northgate-pension.example"


def _parse_eml_attachment(path: Path) -> Optional[IncomingEmail]:
    import email
    from email.policy import default as default_policy

    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=default_policy)
    sender = msg.get("From", "") or _sender_for(path)
    subject = msg.get("Subject", "")
    message_id = msg.get("Message-ID")
    body_part = msg.get_body(preferencelist=("plain",))
    body_text = body_part.get_content() if body_part is not None else None

    EML_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for part in msg.iter_attachments():
        filename = part.get_filename()
        if filename and Path(filename).suffix.lower() in ATTACHMENT_SUFFIXES:
            out_path = EML_ATTACHMENTS_DIR / f"{path.stem}__{filename}"
            out_path.write_bytes(part.get_payload(decode=True))
            return IncomingEmail(
                path=out_path, sender=sender, subject=subject, message_id=message_id,
                body_text=body_text,
                received_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            )
    return None


def _check_inbox_folder() -> list[IncomingEmail]:
    """Every .xlsx/.docx file in mailbox/inbox/ is a new email; sender comes
    from a '<file>.sender.txt' sidecar if present, else the default. A
    .eml file is parsed for its attachment instead."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    emails: list[IncomingEmail] = []
    for path in sorted(INBOX_DIR.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.name.endswith(".sender.txt"):
            continue
        suffix = path.suffix.lower()
        if suffix in ATTACHMENT_SUFFIXES:
            emails.append(IncomingEmail(
                path=path, sender=_sender_for(path),
                received_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            ))
        elif suffix == ".eml":
            parsed = _parse_eml_attachment(path)
            if parsed:
                emails.append(parsed)
    return emails


def _mark_processed_folder(incoming: IncomingEmail) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if incoming.path.exists():
        shutil.move(str(incoming.path), str(PROCESSED_DIR / incoming.path.name))
    sidecar = INBOX_DIR / f"{incoming.path.name}.sender.txt"
    if sidecar.exists():
        shutil.move(str(sidecar), str(PROCESSED_DIR / sidecar.name))


def _safe_filename_fragment(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")[:max_len] or "message"


def _write_eml(
    to_addr: str, from_addr: str, subject: str, body: str,
    attachment_path: Optional[Path] = None, in_reply_to: Optional[str] = None,
    attachment_filename: Optional[str] = None,
) -> Path:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
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
        # attachment_filename lets the recipient-facing name (built by
        # email_template.py, e.g. "Alken_Aurora_DDQ_Response_....xlsx")
        # differ from the file's actual name on disk (which keeps the
        # internal DDQ id, for traceability) -- no second file is written.
        msg.add_attachment(
            data, maintype="application", subtype=subtype,
            filename=attachment_filename or attachment_path.name,
        )

    SENT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = SENT_DIR / f"{timestamp}-{_safe_filename_fragment(subject)}.eml"
    out_path.write_bytes(bytes(msg))
    return out_path


# ── public, mode-dispatched API ─────────────────────────────────────────


def check_for_new_ddqs() -> list[IncomingEmail]:
    """Section 9 step 1: finds new DDQs waiting to be processed."""
    mode = mail_mode()
    if mode == "folder":
        return _check_inbox_folder()
    from mail_gmail import check_gmail_inbox  # imported lazily: only needed in gmail mode

    return check_gmail_inbox()


def mark_processed(incoming: IncomingEmail) -> None:
    """Folder mode: moves the file to mailbox/processed/. Gmail mode: adds
    the 'DDQ-Agent/Processed' label only -- never touches read/unread
    status (section 9: an already-opened email is processed exactly like
    an unread one, and must stay exactly as the user left it)."""
    mode = mail_mode()
    if mode == "folder":
        _mark_processed_folder(incoming)
        return
    from mail_gmail import mark_gmail_processed

    mark_gmail_processed(incoming)


def send_internal_notification(subject: str, body: str, config: dict) -> Path:
    """Rule 6: internal notifications can be sent only to internal_whitelist.
    Always a local .eml record (never a real send -- see module docstring)."""
    reviewer = os.environ.get("REVIEWER_EMAIL") or config["email"]["reviewer"]
    whitelist = config["email"].get("internal_whitelist", [])
    if reviewer not in whitelist:
        raise MailGuardError(
            f"Refusing to send: reviewer address {reviewer!r} is not in internal_whitelist {whitelist!r}."
        )
    sender = os.environ.get("GMAIL_USER") or "agent@alken-fund.example"
    return _write_eml(to_addr=reviewer, from_addr=sender, subject=subject, body=body)


def send_final_reply(
    ddq_sender: str,
    subject: str,
    body: str,
    attachment_path: Path,
    all_questions_approved: bool,
    in_reply_to_message_id: Optional[str] = None,
    attachment_filename: Optional[str] = None,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> Path | dict:
    """Rule 6: the final DDQ can be sent only to the original sender of
    that DDQ, and only if every question is approved. mail.py re-checks
    both itself rather than trusting the caller -- the human-click and
    confirmation-dialog guarantees are app.py's job (section 8 step 3);
    this is the hard backstop, in both modes.

    Folder mode: always a local .eml record under mailbox/sent/ (never a
    real send). Gmail mode: a REAL send via the Gmail API. Threading
    fallback chain (point 2 of the user's fix): thread_id if given, else
    looked up from gmail_message_id, else sent as a new email (same
    subject) -- see mail_gmail.send_gmail. Raises mail_gmail.MailSendError
    on any failure; the caller must treat that as "not sent" and never
    show "Sent" otherwise. Returns the .eml Path (folder mode) or the
    Gmail API response dict with 'id'/'threadId' (gmail mode)."""
    if not all_questions_approved:
        raise MailGuardError("Refusing to send: not every question is approved yet.")
    if not ddq_sender:
        raise MailGuardError("Refusing to send: no original sender recorded for this DDQ.")

    sender = os.environ.get("GMAIL_USER") or "agent@alken-fund.example"
    if mail_mode() == "folder":
        return _write_eml(
            to_addr=ddq_sender, from_addr=sender, subject=subject, body=body,
            attachment_path=attachment_path, in_reply_to=in_reply_to_message_id,
            attachment_filename=attachment_filename,
        )

    from mail_gmail import send_gmail

    return send_gmail(
        to_addr=ddq_sender, subject=subject, body=body,
        attachment_path=attachment_path, attachment_filename=attachment_filename,
        in_reply_to_message_id=in_reply_to_message_id, thread_id=thread_id,
        gmail_message_id=gmail_message_id,
    )
