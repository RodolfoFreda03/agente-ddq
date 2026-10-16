"""Reads and writes the Obsidian vault, and TF-IDF search over it
(CLAUDE.md section 7).

No API calls: search is deterministic TF-IDF cosine similarity (the same
technique used for c1 in scoring.py's caller, memory.py being where c1 is
actually computed from the vault).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import frontmatter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

VAULT_DIR = Path("obsidian-vault")
MEMORANDUM_DIR = VAULT_DIR / "memorandum"
APPROVED_ANSWERS_DIR = VAULT_DIR / "approved-answers"
LOG_DIR = VAULT_DIR / "log"

# Approved-answer notes store "**Question:** ...\n\n**Answer:** ..." in the
# body (section 7.2). This pulls each part out on its own.
QUESTION_PATTERN = re.compile(r"\*\*Question:\*\*\s*(.+?)(?=\n\s*\*\*Answer:\*\*|\Z)", re.DOTALL)
ANSWER_PATTERN = re.compile(r"\*\*Answer:\*\*\s*(.+)", re.DOTALL)


@dataclass
class Note:
    path: Path
    metadata: dict
    content: str

    @property
    def reference(self) -> Optional[str]:
        return self.metadata.get("reference")

    @property
    def type(self) -> Optional[str]:
        return self.metadata.get("type")

    @property
    def category(self) -> Optional[str]:
        return self.metadata.get("category")

    def question(self) -> Optional[str]:
        """For an approved_answer note: the text after '**Question:**'.
        None if the note has no such marker (e.g. a source note)."""
        match = QUESTION_PATTERN.search(self.content)
        return match.group(1).strip() if match else None

    def answer(self) -> Optional[str]:
        """For an approved_answer note: the text after '**Answer:**'."""
        match = ANSWER_PATTERN.search(self.content)
        return match.group(1).strip() if match else None

    def searchable_text(self) -> str:
        """What gets vectorised for a general vault search: frontmatter
        fields that carry meaning, plus the body, so a query can match on
        the section title or reference as well as the prose and tables."""
        parts = [
            str(self.metadata.get("section", "")),
            str(self.metadata.get("reference", "")),
            self.content,
        ]
        return "\n".join(parts)


def load_note(path: Path) -> Note:
    post = frontmatter.load(path)
    return Note(path=path, metadata=dict(post.metadata), content=post.content)


def load_vault_notes(
    vault_dir: Optional[Path] = None,
    note_type: Optional[str] = None,
) -> list[Note]:
    """Reads every .md note under vault_dir (recursively). note_type, if
    given, filters to that frontmatter 'type' (e.g. 'source',
    'approved_answer'). vault_dir defaults to the *current* VAULT_DIR,
    looked up at call time -- not a plain `= VAULT_DIR` default, which
    would freeze in the value from module-import time and silently ignore
    a later redirect (demo.py points VAULT_DIR at a temp copy of
    demo_data/ for the public demo)."""
    vault_dir = vault_dir if vault_dir is not None else VAULT_DIR
    notes = [load_note(path) for path in sorted(vault_dir.rglob("*.md"))]
    if note_type is not None:
        notes = [n for n in notes if n.type == note_type]
    return notes


def write_note(content: str, path: Path, **metadata) -> Path:
    """Writes one note with a YAML frontmatter block. Dates are passed as
    datetime.date so they serialise unquoted (e.g. date: 2026-09-30)."""
    post = frontmatter.Post(content, **metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    return path


@dataclass
class SearchResult:
    note: Note
    score: float


def _tfidf_search(
    query: str,
    notes: list[Note],
    text_fn: Callable[[Note], str],
    top_k: int,
) -> list[SearchResult]:
    if not notes:
        return []

    corpus = [text_fn(n) for n in notes]
    vectorizer = TfidfVectorizer()
    try:
        matrix = vectorizer.fit_transform(corpus + [query])
    except ValueError:
        # Empty vocabulary (e.g. every document and the query are blank).
        return []

    query_vector = matrix[-1]
    note_vectors = matrix[:-1]
    scores = cosine_similarity(query_vector, note_vectors)[0]

    results = [SearchResult(note=n, score=float(s)) for n, s in zip(notes, scores)]
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def search(query: str, notes: list[Note], top_k: int = 5) -> list[SearchResult]:
    """TF-IDF cosine similarity between the query and each note's
    searchable_text() (section/reference/body) -- general-purpose vault
    search, used to retrieve source passages to cite."""
    return _tfidf_search(query, notes, text_fn=Note.searchable_text, top_k=top_k)


def search_approved_questions(query: str, notes: list[Note], top_k: int = 5) -> list[SearchResult]:
    """TF-IDF cosine similarity between the query and each approved-answer
    note's *question only* (not its answer) -- this is what c1 is defined
    over (section 5.1: 'similarity between the question and the questions
    of approved answers'). Notes with no parseable question are skipped."""
    questioned = [n for n in notes if n.question()]
    return _tfidf_search(query, questioned, text_fn=lambda n: n.question(), top_k=top_k)


def similar_approved_answers(
    query: str,
    notes: list[Note],
    threshold: float = 0.6,
    top_k: int = 5,
) -> list[SearchResult]:
    """c4's input (section 5.1): approved answers whose question similarity
    to `query` is strictly above `threshold`. notes should already be
    non_expired(). Returns [] if none qualify -- the caller then uses
    c4=0.5 (no similar answer) without any model call."""
    results = search_approved_questions(query, notes, top_k=top_k)
    return [r for r in results if r.score > threshold]


def non_expired(notes: list[Note], as_of: Optional[date] = None) -> list[Note]:
    """Filters out approved answers whose `expiry` has passed (section 7.2:
    'expired answers do not count towards c1')."""
    today = as_of or date.today()
    live = []
    for note in notes:
        expiry = note.metadata.get("expiry")
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        if isinstance(expiry, date) and expiry < today:
            continue
        live.append(note)
    return live


def max_similarity_to_approved_questions(
    query: str,
    vault_dir: Optional[Path] = None,
    as_of: Optional[date] = None,
) -> float:
    """c1 (section 5.1): the maximum TF-IDF cosine similarity between a
    question and the questions of (non-expired) approved answers in the
    vault. 0.0 if there are none. See load_vault_notes() for why vault_dir
    is looked up at call time rather than defaulted eagerly."""
    vault_dir = vault_dir if vault_dir is not None else VAULT_DIR
    notes = load_vault_notes(vault_dir, note_type="approved_answer")
    live_notes = non_expired(notes, as_of=as_of)
    if not live_notes:
        return 0.0
    results = search_approved_questions(query, live_notes, top_k=1)
    return results[0].score if results else 0.0
