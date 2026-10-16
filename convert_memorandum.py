"""Converts the Word investment memorandum into one Obsidian note per
section (CLAUDE.md section 7.1).

Usage:
    python convert_memorandum.py source/Investment_Memorandum_Alken_Aurora_EN.docx

The knowledge base is the English memorandum (DDQs, approved answers and
replies are all in English; TF-IDF search does not work across languages).
See README note below for the next_review derivation, which CLAUDE.md does
not fully pin down for this real document.
"""

from __future__ import annotations

import calendar
import re
import sys
from datetime import date
from pathlib import Path

import docx
import frontmatter
from docx.table import Table
from docx.text.paragraph import Paragraph

VAULT_MEMORANDUM_DIR = Path("obsidian-vault/memorandum")

HEADING1_PATTERN = re.compile(r"^(\d{2})\s+(.+)$")
DDQ_REFERENCE_PATTERN = re.compile(
    r"^DDQ reference\s+(\S+)\s*\|\s*Scenario owner\s+(.+)$"
)

# Section -> (category, date). Exactly the mapping table in CLAUDE.md
# section 7.1. S04's content also matches conflicts_of_interest questions,
# but the section's own category stays "organisation" as the table states;
# the question's own category (from the classifier) drives r1 at query
# time -- the source category here is only used to filter search.
SECTION_CATEGORY_DATE: dict[str, tuple[str, date]] = {
    "00": ("organisation", date(2026, 9, 30)),
    "01": ("organisation", date(2026, 9, 30)),
    "02": ("organisation", date(2026, 9, 30)),
    "03": ("organisation", date(2026, 9, 30)),
    "04": ("organisation", date(2026, 9, 30)),
    "05": ("strategy", date(2026, 1, 1)),
    "06": ("strategy", date(2026, 1, 1)),
    "07": ("risk_management", date(2026, 9, 30)),
    "08": ("risk_management", date(2026, 9, 30)),
    "09": ("risk_management", date(2026, 9, 30)),
    "10": ("performance", date(2026, 9, 30)),
    "11": ("fees", date(2026, 9, 30)),
    "12": ("organisation", date(2026, 9, 30)),
    "13": ("operations", date(2026, 1, 1)),
    "14": ("operations", date(2026, 1, 1)),
    "15": ("legal_regulatory", date(2026, 1, 1)),
    "16": ("esg", date(2026, 9, 30)),
    "17": ("cybersecurity", date(2026, 9, 30)),
    "18": ("organisation", date(2026, 9, 30)),
    "19": ("risk_management", date(2026, 9, 30)),
    "20": ("organisation", date(2026, 9, 30)),
}

# next_review derivation.
#
# CLAUDE.md says to take date from the mapping above and next_review "from
# the S20 document register table" -- but that table (section 20 of the
# real document) does not give a clean per-section ISO date for every
# section: it groups sections and describes a review cadence in prose, e.g.
# "annual review or on amendment" (S02, S11, S12), "quarterly verification"
# (S03, S04, S18), and gives an explicit date only for two groups ("next
# monthly update at 31 October" / "update at the end of October" for
# S07-S10). Four sections (S00, S01, S19, S20) are not named in any
# register row at all.
#
# So: where the register gives an explicit date, that date is used as-is.
# Where it gives a cadence word, next_review = date + that cadence. Where a
# section is not covered by the register, next_review defaults to +12
# months (the most common cadence in the table) from its own date.
NEXT_REVIEW_EXPLICIT: dict[str, date] = {
    "07": date(2026, 10, 31),
    "08": date(2026, 10, 31),
    "09": date(2026, 10, 31),
    "10": date(2026, 10, 31),
}
NEXT_REVIEW_MONTHS: dict[str, int] = {
    "03": 3, "04": 3, "18": 3,     # "quarterly verification/review"
    "02": 12, "11": 12, "12": 12,  # "annual review or on amendment"
    "05": 12, "06": 12,            # "annual review"
    # everything else not listed here (including 00, 01, 13-17, 19, 20)
    # falls back to 12 months below.
}


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_next_review(section_number: str, base_date: date) -> date:
    if section_number in NEXT_REVIEW_EXPLICIT:
        return NEXT_REVIEW_EXPLICIT[section_number]
    months = NEXT_REVIEW_MONTHS.get(section_number, 12)
    return add_months(base_date, months)


def iter_block_items(document: docx.Document):
    """Yields each top-level paragraph and table in document order (the
    order python-docx's .paragraphs / .tables do not preserve on their own)."""
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def table_to_markdown(table: Table) -> str:
    """Converts a docx table to a GitHub-flavoured Markdown table."""

    def clean_cell(text: str) -> str:
        return text.strip().replace("|", "\\|").replace("\n", "<br>")

    rows = [[clean_cell(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header, *body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


class Section:
    def __init__(self, number: str, title: str) -> None:
        self.number = number        # "00".."20"
        self.title = title          # e.g. "Guida al documento e perimetro dei dati", or "Overview" for S00
        self.reference: str | None = None   # e.g. "S01", from the "Riferimento DDQ" line
        self.owner: str | None = None
        self.body_parts: list[str] = []

    def add_heading2(self, text: str) -> None:
        self.body_parts.append(f"## {text}")

    def add_paragraph(self, text: str) -> None:
        self.body_parts.append(text)

    def add_table(self, table: Table) -> None:
        md = table_to_markdown(table)
        if md:
            self.body_parts.append(md)

    def body(self) -> str:
        # Collapse runs of blank entries to a single blank line between parts.
        parts = [p for p in self.body_parts if p is not None]
        return "\n\n".join(p for p in parts if p.strip() != "") + "\n"


def split_into_sections(document: docx.Document) -> list[Section]:
    sections: list[Section] = []
    current = Section("00", "Overview")
    sections.append(current)

    for item in iter_block_items(document):
        if isinstance(item, Paragraph):
            style = item.style.name if item.style else "Normal"
            text = item.text.strip()

            if style == "Heading 1":
                match = HEADING1_PATTERN.match(text)
                if not match:
                    raise ValueError(f"Heading 1 does not match 'NN  Title': {text!r}")
                number, title = match.group(1), match.group(2)
                current = Section(number, title)
                sections.append(current)
                continue

            if style == "Heading 2":
                if text:
                    current.add_heading2(text)
                continue

            # Normal / Title / Subtitle paragraphs.
            ref_match = DDQ_REFERENCE_PATTERN.match(text)
            if ref_match:
                current.reference, current.owner = ref_match.group(1), ref_match.group(2)
                continue  # redundant with the frontmatter fields: not duplicated in the body

            if text:
                current.add_paragraph(text)
        else:
            current.add_table(item)

    return sections


def section_filename(section: Section) -> str:
    if section.number == "00":
        return "S00 Overview.md"
    return f"S{section.number} {section.title}.md"


def write_note(section: Section, output_dir: Path) -> Path:
    category, base_date = SECTION_CATEGORY_DATE[section.number]
    next_review = compute_next_review(section.number, base_date)
    reference = section.reference or f"S{section.number}"

    metadata = {
        "type": "source",
        "reference": reference,
        "section": f"{section.number}  {section.title}" if section.number != "00" else section.title,
        "owner": section.owner,
        "date": base_date,
        "next_review": next_review,
        "category": category,
    }
    post = frontmatter.Post(section.body(), **metadata)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / section_filename(section)
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    return path


def convert(docx_path: str | Path, output_dir: Path = VAULT_MEMORANDUM_DIR) -> list[Path]:
    document = docx.Document(str(docx_path))
    sections = split_into_sections(document)
    return [write_note(section, output_dir) for section in sections]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python convert_memorandum.py <path-to-memorandum.docx>")
        raise SystemExit(1)
    paths = convert(sys.argv[1])
    print(f"Wrote {len(paths)} notes to {VAULT_MEMORANDUM_DIR}/")
    for path in paths:
        print(" -", path.name)


if __name__ == "__main__":
    main()
