"""Saves and loads the state of each DDQ (CLAUDE.md sections 3, 8, 9).

One JSON file per DDQ: data/ddqs/<id>/state.json. agent.py and app.py both
read and write through this module only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

DDQS_DIR = Path("data/ddqs")

STATUS_NEW = "New"
STATUS_REVIEWED = "Reviewed by agent"
STATUS_IN_REVIEW = "In human review"
STATUS_READY = "Ready to send"
STATUS_SENT = "Sent"


@dataclass
class QuestionState:
    id: str
    section: Optional[str]
    text: str
    row: int

    # scoring (section 5.3: "every result must expose the individual
    # factors, C, R, P, the route and the reason")
    c1: float = 0.0
    c2: float = 0.0
    c3: float = 0.0
    c4: float = 0.0
    r1: float = 0.0
    r2: float = 0.0
    r3: float = 0.0
    r4: float = 0.0
    confidence: float = 0.0
    risk: float = 0.0
    priority: float = 0.0
    route: str = "GAP"
    reason: str = ""
    category: Optional[str] = None

    guardrails_fired: list[str] = field(default_factory=list)
    flagged_for_check: bool = False  # rule 7
    possible_prompt_injection: bool = False  # rule 9

    draft_text: Optional[str] = None
    cited_sources: list[str] = field(default_factory=list)  # e.g. ["S11"]
    final_answer: Optional[str] = None  # human-edited text (section 8, step 2)
    approved: bool = False
    edit_rate: Optional[float] = None  # set once submitted (section 8, step 3)


@dataclass
class DDQState:
    id: str
    source_path: str
    sheet_name: str
    sender: str
    date_received: str  # ISO datetime
    status: str = STATUS_NEW
    questions: list[QuestionState] = field(default_factory=list)
    guardrails_fired_summary: dict[str, int] = field(default_factory=dict)
    approved_by: Optional[str] = None
    sent_at: Optional[str] = None
    final_file_path: Optional[str] = None
    reply_eml_path: Optional[str] = None
    gmail_message_id: Optional[str] = None  # set only for gmail-mode DDQs; dedup key (section 9)
    investor_name: Optional[str] = None  # from ddq_reader.py, for the reply email template
    recipient_first_name: Optional[str] = None  # from the original email's signature, if found
    original_message_id: Optional[str] = None  # RFC822 Message-ID, so the reply threads correctly
    gmail_thread_id: Optional[str] = None  # Gmail API threadId, so a real reply lands in-thread

    def approved_count(self) -> int:
        return sum(1 for q in self.questions if q.approved)

    def all_approved(self) -> bool:
        return bool(self.questions) and all(q.approved for q in self.questions)


def ddq_dir(ddq_id: str) -> Path:
    return DDQS_DIR / ddq_id


def state_path(ddq_id: str) -> Path:
    return ddq_dir(ddq_id) / "state.json"


def save_state(state: DDQState) -> Path:
    path = state_path(state.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    return path


def _filter_known_fields(cls, data: dict) -> dict:
    """Drops any key in `data` that isn't a current field of `cls` --
    backward AND forward compatibility: a state.json written before a
    field existed is missing a key (the dataclass default fills it in,
    e.g. a new Optional[...] = None field); one written by a newer
    version (or hand-edited) might have a key that no longer exists
    (would otherwise be an unexpected-keyword-argument TypeError). Either
    way, loading an old or differently-shaped state.json must never
    crash (confirmed live: a pre-gmail_thread_id state.json crashed
    app.py with AttributeError when the field was read)."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def load_state(ddq_id: str) -> DDQState:
    data = json.loads(state_path(ddq_id).read_text(encoding="utf-8"))
    questions = [
        QuestionState(**_filter_known_fields(QuestionState, q))
        for q in data.pop("questions", [])
    ]
    return DDQState(questions=questions, **_filter_known_fields(DDQState, data))


def list_ddq_ids() -> list[str]:
    if not DDQS_DIR.exists():
        return []
    return sorted(p.name for p in DDQS_DIR.iterdir() if p.is_dir() and (p / "state.json").exists())


def load_all_states() -> list[DDQState]:
    return [load_state(ddq_id) for ddq_id in list_ddq_ids()]


def find_by_gmail_message_id(gmail_message_id: str) -> Optional[str]:
    """The id of the DDQ already recorded for this Gmail message, if any --
    the state.json-based half of gmail mode's two-check de-dup (section 9):
    the 'DDQ-Agent/Processed' label is the primary signal, this is the
    backstop for the rare case where a crash happened after process_ddq()
    saved state but before the label was applied."""
    for ddq_id in list_ddq_ids():
        if load_state(ddq_id).gmail_message_id == gmail_message_id:
            return ddq_id
    return None
