"""Write the auditor's report and the notes of a text PDF annual report as Markdown.

The auditor's report starts on a page titled "Independent Auditor's Report"; the notes start on a
page titled "Notes ...". Each runs until the next statement page. Bold lines become headings, note
tables become Markdown tables (columns from the same period detection as the value extractor), and
other lines are joined into paragraphs (a line ending well short of the right margin ends one).
"""

import re
from pathlib import Path
from typing import Any

from fin.extract.pdf import (
    TITLE_LINES,
    Column,
    header_columns,
    line_text,
    open_pdf,
    page_lines,
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
            lines = page_lines(page)
            current = _section_of(lines, current)
            if current is not None:
                pages[current].append((page.page_number, lines))

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for section in SECTIONS:
        if not pages[section]:
            continue
        numbers = ", ".join(str(n) for n, _ in pages[section])
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


def _markdown_blocks(lines: list[list[dict[str, Any]]]) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []
    table: list[str] = []
    columns: list[Column] = []
    right_margin = max((line[-1]["x1"] for line in lines), default=0.0)

    def flush() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for i, line in enumerate(lines):
        header = header_columns(line)
        if header:
            flush()
            columns = header
            left = line_text([w for w in line if w["x1"] <= header[0].x0]) or "Particulars"
            years = [c.fiscal_year for c in columns]
            table.append("| " + " | ".join([left, *years]) + " |")
            table.append("|" + "---|" * (len(years) + 1))
            continue
        words, row = split_row(line, columns) if columns else ([], {})
        if row and table:
            cells = [row[c.fiscal_year]["text"] if c.fiscal_year in row else "" for c in columns]
            table.append("| " + " | ".join([line_text(words), *cells]) + " |")
            continue
        columns = []
        if _is_bold(line):
            flush()
            blocks.append(("# " if i == 0 else "## ") + line_text(line))
            continue
        if table:
            flush()
        paragraph.append(line_text(line))
        if line[-1]["x1"] < SHORT_LINE * right_margin:
            flush()
    flush()
    return blocks
