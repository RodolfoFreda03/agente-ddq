"""Public demo mode (DEMO_PUBLIC=true), for a safe link anyone can open
(Streamlit Community Cloud) -- no Gmail, no model calls, no real email.

What it does, when enabled:
- forces MAIL_MODE to "folder" (mail_gmail.py's credentials.json/token.json
  are never touched -- see mail.py's mail_mode() dispatch);
- copies demo_data/ (committed to the repo) into a temp working copy once
  per server process, and redirects store.py/memory.py/llm.py's data
  directories to it, so the real data/ and obsidian-vault/ (gitignored,
  the developer's local working data) are never read or written;
- app.py uses is_enabled() to disable "Check mailbox" (no new DDQ
  processing -- no model calls) and real "Send" (always a local-only
  action here anyway, but demo mode shows a fixed message instead of
  writing even a local .eml record), and to show the "Reset demo" button
  and the top banner.

Limitation, stated plainly rather than hidden: the temp working copy is
shared by the whole server process, not per-visitor -- if two people use
the public demo at the same time, they see each other's edits. Acceptable
for a small presentation audience; "Reset demo" is the recovery path.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import llm
import memory
import store

DEMO_DATA_DIR = Path("demo_data")
_TEMP_ROOT = Path(tempfile.gettempdir()) / "ddq_agent_demo_working_copy"


def is_enabled() -> bool:
    return os.environ.get("DEMO_PUBLIC", "").strip().lower() in ("1", "true", "yes")


def _redirect_data_dirs(base: Path) -> None:
    """Points store.py/memory.py/llm.py's module-level directory
    variables at the temp working copy. Safe to call repeatedly -- each
    consumer reads these as plain module attributes (store.DDQS_DIR,
    memory.VAULT_DIR, ...) or via a call-time default (see
    memory.load_vault_notes), never a value captured once and cached."""
    store.DDQS_DIR = base / "ddqs"
    memory.VAULT_DIR = base / "obsidian-vault"
    memory.MEMORANDUM_DIR = memory.VAULT_DIR / "memorandum"
    memory.APPROVED_ANSWERS_DIR = memory.VAULT_DIR / "approved-answers"
    memory.LOG_DIR = memory.VAULT_DIR / "log"
    llm.CACHE_DIR = base / "llm_cache"


def reset() -> None:
    """Copies demo_data/ fresh into the temp working copy, discarding any
    edits made so far -- the 'Reset demo' button."""
    shutil.rmtree(_TEMP_ROOT, ignore_errors=True)
    shutil.copytree(DEMO_DATA_DIR, _TEMP_ROOT)
    _redirect_data_dirs(_TEMP_ROOT)


def setup() -> None:
    """Called at the top of every app.py run when is_enabled(): forces
    MAIL_MODE, and ensures the temp working copy exists (built once; a
    later call just re-asserts the redirect, which is already in effect
    for the lifetime of the process -- cheap, and safe to repeat)."""
    os.environ["MAIL_MODE"] = "folder"
    if not _TEMP_ROOT.exists():
        reset()
    else:
        _redirect_data_dirs(_TEMP_ROOT)
