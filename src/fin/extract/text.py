"""Write the auditor's report and the notes of a text PDF annual report as Markdown.

The auditor's report starts on a page titled "Independent Auditor's Report"; the notes start on a
page titled "Notes ...". Each runs until the next statement page. Bold lines become headings, note
tables become Markdown tables (columns from the same period detection as the value extractor), and
other lines are joined into paragraphs (a line ending well short of the right margin ends one).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin.extract.pdf import (
    TITLE_LINES,
    Column,
    header_starting,
    line_text,
    open_pdf,
    page_parts,
    split_row,
    statement_of,
)

_AUDITOR_TITLE = re.compile(r"auditor['’]?s['’]? report", re.I)
SECTIONS = ("auditor_report", "notes")
SHORT_LINE = 0.8  # a paragraph line ending before this share of the right margin ends the paragraph


def write_notes_markdown(path: Path, out_dir: Path) -> list[Path]:
    """Write `<pdf stem>.auditor_report.md` and `<pdf stem>.notes.md` into `out_dir`.

    Returns the files written; a section the PDF does not have is not written.
    Raises E101/E102/E104 like the value extractor.
    """
    pages: dict[str, list[tuple[int, list[list[dict[str, Any]]]]]] = {s: [] for s in SECTIONS}
    current: str | None = None
    with open_pdf(path) as pdf:
        for page in pdf.pages:
            for lines in page_parts(page):
                current = _section_of(lines, current)
                if current is not None:
                    pages[current].append((page.page_number, lines))

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for section in SECTIONS:
        if not pages[section]:
            continue
        numbers = ", ".join(str(n) for n in dict.fromkeys(n for n, _ in pages[section]))
        blocks = [f"Source: {path.name}, page(s) {numbers}"]
        for _, lines in pages[section]:
            blocks.extend(_markdown_blocks(lines))
        out = out_dir / f"{path.stem}.{section}.md"
        out.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        written.append(out)
    return written


def _section_of(lines: list[list[dict[str, Any]]], current: str | None) -> str | None:
    """The section a page belongs to. Untitled pages continue the section before them."""
    if any(_AUDITOR_TITLE.search(line_text(line)) for line in lines[:TITLE_LINES]):
        return "auditor_report"
    statement = statement_of(lines)
    if statement == "notes":
        return "notes"
    if statement is not None:
        return None
    return current


def _is_bold(line: list[dict[str, Any]]) -> bool:
    return all("bold" in w["fontname"].lower() for w in line)


@dataclass
class _Blocks:
    """Markdown blocks written so far, and the paragraph and table being built."""

    right_margin: float
    blocks: list[str] = field(default_factory=list)
    paragraph: list[str] = field(default_factory=list)
    table: list[str] = field(default_factory=list)

    def flush(self) -> None:
        """End the paragraph and the table being built."""
        if self.paragraph:
            self.blocks.append(" ".join(self.paragraph))
            self.paragraph.clear()
        if self.table:
            self.blocks.append("\n".join(self.table))
            self.table.clear()

    def start_table(self, columns: list[Column], header: list[dict[str, Any]]) -> None:
        """Start a table with a header row: the words left of the columns, then each column."""
        self.flush()
        left = line_text([w for w in header if w["x1"] <= columns[0].x0]) or "Particulars"
        years = [c.label for c in columns]
        self.table.append("| " + " | ".join([left, *years]) + " |")
        self.table.append("|" + "---|" * (len(years) + 1))

    def add_row(
        self, words: list[dict[str, Any]], row: dict[str, Any], columns: list[Column]
    ) -> None:
        cells = [row[c.key]["text"] if c.key in row else "" for c in columns]
        self.table.append("| " + " | ".join([line_text(words), *cells]) + " |")

    def add_text(self, line: list[dict[str, Any]], first: bool) -> None:
        """A bold line is a heading (top level on the first line); any other line joins the
        paragraph, and ends it when it stops well short of the right margin."""
        if _is_bold(line):
            self.flush()
            self.blocks.append(("# " if first else "## ") + line_text(line))
            return
        if self.table:
            self.flush()
        self.paragraph.append(line_text(line))
        if line[-1]["x1"] < SHORT_LINE * self.right_margin:
            self.flush()


def _markdown_blocks(lines: list[list[dict[str, Any]]]) -> list[str]:
    out = _Blocks(right_margin=max((line[-1]["x1"] for line in lines), default=0.0))
    columns: list[Column] = []
    i = 0
    while i < len(lines):
        found = header_starting(lines, i)
        if found is not None:
            columns, end = found
            out.start_table(columns, [w for line in lines[i:end] for w in line])
            i = end
            continue
        line = lines[i]
        i += 1
        words, row = split_row(line, columns) if columns else ([], {})
        if row and out.table:
            out.add_row(words, row, columns)
            continue
        columns = []
        out.add_text(line, first=i == 1)
    out.flush()
    return out.blocks
