"""The marked-up workbook of a query export.

Sheets: `Summary` (one row per issue), `Q<id>` (an issue's lines, with the count and value per kind
of difference) and, for each source workbook an issue cites, a copy of the cited sheets from the
archive (`S1 <sheet>`, ...) in their own layout with the cited rows highlighted and the query named
beside each. A source that is not a workbook (a PDF page) is named in the issue's lines, not marked.
Rows come from the lines' source references (`<file> <sheet>!<rows>`): a line names at most five
rows per sheet (the rest are counted), and only the named rows are highlighted. Real names come from
the archived original and from `Unmasker`.
"""

import re
from collections import defaultdict
from copy import copy
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from openpyxl.worksheet.worksheet import Worksheet

from fin.audit.errors import ExportFileError
from fin.diligence.engagement import rupees
from fin.diligence.export_data import Issue, Unmasker
from fin.output import escape
from fin.paths import inside
from fin.reconcile.format import LEFT
from fin.reconcile.items import line_rupees

HIGHLIGHT = PatternFill("solid", start_color="FFFF00", end_color="FFFF00")
BOLD = Font(bold=True)
REF = re.compile(r"^(?P<sheet>.+)!(?P<spec>[A-Za-z]*\d+(?:, ?[A-Za-z]*\d+)*)(?: and \d+ more)?$")
BAD_TITLE = re.compile(r"[\[\]:*?/\\]")


def _table(sheet: Worksheet, row: int, headers: list[str], rows: list[list[Any]]) -> None:
    for col, head in enumerate(headers, 1):
        sheet.cell(row, col, head).font = BOLD
    for r, values in enumerate(rows, row + 1):
        for col, value in enumerate(values, 1):
            cell = sheet.cell(r, col, value)
            cell.alignment = Alignment(wrap_text=isinstance(value, str), vertical="top")
            if isinstance(value, int | float) or hasattr(value, "quantize"):
                cell.number_format = "#,##0.00"
    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        longest = max([len(headers[col - 1])] + [len(str(v[col - 1] or "")) for v in rows])
        sheet.column_dimensions[letter].width = min(60, max(12, longest + 2))


def _summary(book: Workbook, issues: list[Issue], u: Unmasker) -> None:
    sheet = book.active
    assert sheet is not None
    sheet.title = "Summary"
    rows = [
        [
            f"Q{i.query_id}",
            i.title,
            i.query["amount"],
            len(i.lines) or None,
            i.difference_text() or None,
            u(i.query["question"]),
            u(i.query["sources"]),
        ]
        for i in issues
    ]
    heads = ["Query", "Period", "Amount (Rs)", "Lines", "Differences", "Question", "Sources"]
    _table(sheet, 1, heads, rows)


def _issue_sheet(book: Workbook, issue: Issue, u: Unmasker) -> None:
    sheet = book.create_sheet(f"Q{issue.query_id}")
    sheet["A1"] = f"Query {issue.query_id}: {issue.title}"
    sheet["A1"].font = BOLD
    sheet["A2"] = u(issue.query["question"])
    sheet["A3"] = (
        "" if issue.query["amount"] is None else f"Amount at issue: {rupees(issue.query['amount'])}"
    )
    sheet["A4"] = u(issue.query["sources"]) if not issue.lines else issue.difference_text()
    rows = []
    for line in issue.lines:
        first, second = LEFT.get(line["name"], ("first", "second"))
        rows.append(
            [
                line["item_id"],
                f"{first} vs {second}",
                line["kind"].replace("_", " "),
                u(line["key"]),
                line["left_amount"],
                line["right_amount"],
                line_rupees(line),
                line["currency"] or "INR",
                u(line["left_source"]),
                u(line["right_source"]),
                u(line["note"]),
            ]
        )
    heads = ["Line", "Compared", "Kind", "Key", "First figure", "Second figure", "Difference (Rs)"]
    _table(sheet, 6, [*heads, "Figures in", "First source", "Second source", "Note"], rows)


def _parse(
    ref: str, files: list[tuple[int, str, str, str, str]], period: str | None
) -> tuple[int, str, list[str]] | None:
    """(file_id, sheet, rows or cells) for '<file> <sheet>!<rows>', or None when it is not one. Of
    several files with the name (a corrected file keeps its name), the one loaded for the query's
    period, else the latest loaded."""
    for name in sorted({f[1] for f in files}, key=lambda n: -len(n)):
        if not ref.startswith(name + " "):
            continue
        match = REF.match(ref[len(name) + 1 :].strip())
        if match:
            named = [f for f in files if f[1] == name]
            best = min(named, key=lambda f: (f[4] != period, f[3] != "loaded", -f[0]))
            return best[0], match["sheet"], [s.strip() for s in match["spec"].split(",")]
    return None


def _issue_refs(issue: Issue) -> list[tuple[str, str]]:
    """(source reference, label) of an issue: its lines' sources, or its own sources text."""
    if not issue.lines:
        return [(part, f"Q{issue.query_id}") for part in issue.query["sources"].split("; ")]
    refs = []
    for line in issue.lines:
        tag = f"Q{issue.query_id}: {line['kind'].replace('_', ' ')}"
        refs += [(line[side], tag) for side in ("left_source", "right_source") if line[side]]
    return refs


Marks = dict[tuple[int, str], dict[int, tuple[set[str], set[str]]]]


def _mark(marks: Marks, key: tuple[int, str], spec: str, tag: str) -> None:
    cell = "" if spec.isdigit() else spec.upper()
    row = int(spec) if not cell else int(coordinate_from_string(cell)[1])
    cells, labels = marks[key].setdefault(row, (set(), set()))
    cells.add(cell)
    labels.add(tag)


def source_marks(issues: list[Issue], files: list[tuple[int, str, str, str, str]]) -> Marks:
    """(file_id, sheet) -> row -> (cells to highlight, labels). A row given as a number is
    highlighted whole; one given as a cell, that cell only."""
    marks: Marks = defaultdict(dict)
    for issue in issues:
        for text, tag in _issue_refs(issue):
            parsed = [_parse(part, files, issue.query["period"]) for part in text.split("; ")]
            for file_id, sheet, specs in (p for p in parsed if p is not None):
                for spec in specs:
                    _mark(marks, (file_id, sheet), spec, tag)
    return marks


def _copy_sheet(source: Worksheet, target: Worksheet) -> None:
    for row in source.iter_rows():
        for cell in row:
            if cell.row is None or cell.column is None:
                continue
            new = target.cell(cell.row, cell.column, cell.value)
            if cell.has_style:
                # openpyxl's stubs type a copied style as its proxy; copy() returns the style itself
                old: Any = cell
                new.font, new.border, new.fill = copy(old.font), copy(old.border), copy(old.fill)
                new.number_format, new.alignment = cell.number_format, copy(old.alignment)
    for key, dim in source.column_dimensions.items():
        target.column_dimensions[key].width = dim.width
    for merged in source.merged_cells.ranges:
        target.merge_cells(str(merged))


def _title(book: Workbook, number: int, sheet: str) -> str:
    base = f"S{number} {BAD_TITLE.sub('', sheet)}"[:31]
    title, n = base, 1
    while title in book.sheetnames:
        n += 1
        title = f"{base[: 31 - len(str(n)) - 1]} {n}"
    return title


def _load(root: Path, name: str, archive: str) -> Any:
    """The archived workbook (values as saved), or None for a file that is not a workbook."""
    path = inside(root, root / archive)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    try:
        return load_workbook(path, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise ExportFileError(
            f"source file {name} cannot be read from the archive ({path}): {exc}; nothing written"
        ) from exc


def _highlight(target: Worksheet, width: int, rows: dict[int, tuple[set[str], set[str]]]) -> None:
    note_col = width + 2
    for row, (cells, labels) in rows.items():
        columns = None if "" in cells else [coordinate_from_string(c)[0] for c in cells]
        for col in range(1, width + 1):
            if columns is None or get_column_letter(col) in columns:
                target.cell(row, col).fill = HIGHLIGHT
        target.cell(row, note_col, "; ".join(sorted(labels))).font = BOLD
    target.column_dimensions[get_column_letter(note_col)].width = 40


def _source_sheets(
    book: Workbook, root: Path, files: list[tuple[int, str, str, str, str]], marks: dict[Any, Any]
) -> None:
    paths = {file_id: (name, archive) for file_id, name, archive, _, _ in files}
    numbers: dict[int, int] = {}
    for (file_id, sheet_name), rows in marks.items():
        source_book = _load(root, *paths[file_id])
        if source_book is None or sheet_name not in source_book.sheetnames:
            continue
        source = source_book[sheet_name]
        numbers.setdefault(file_id, len(numbers) + 1)
        target = book.create_sheet(_title(book, numbers[file_id], sheet_name))
        _copy_sheet(source, target)
        _highlight(target, source.max_column, rows)


def write_workbook(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    engagement_id: int,
    issues: list[Issue],
    u: Unmasker,
    path: Path,
) -> None:
    """Write the marked-up workbook to `path`. E859 when a cited workbook cannot be read."""
    files = [
        (r[0], r[1], r[2], r[3], r[4])
        for r in con.execute(
            "SELECT file_id, file_name, archive_path, status, period FROM source_files "
            "WHERE engagement_id = ? UNION ALL "
            "SELECT -doc_id, file_name, archive_path, status, fiscal_year FROM documents "
            "ORDER BY 1 DESC",
            [engagement_id],
        ).fetchall()
    ]
    book = Workbook()
    _summary(book, issues, u)
    for issue in issues:
        _issue_sheet(book, issue, u)
    _source_sheets(book, root, files, source_marks(issues, files))
    try:
        escape.excel_book(book).save(path)
    except OSError as exc:
        raise ExportFileError(f"cannot write {path}: {exc}") from exc
