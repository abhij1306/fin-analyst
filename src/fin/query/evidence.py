"""`fin sql --evidence`: an HTML page showing where every fact behind a query result came from.

For each fact: a highlighted crop of the PDF row, or a small grid of the Excel cells around the
value (read from the archived workbook). A value summed from several lines (a `fact_ids` column)
has a section per line. An archived source file that is missing or cannot be opened is flagged on
the page with E604; the page is still written. Output: `evidence/<query_id>.html`, with crops in
`evidence/<query_id>/` (the query_id is the run_id of the `fin sql` run).
"""

import html
import json
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook

from fin.audit.errors import DataFolderNotWritable, EvidenceSourceMissing
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.extract.pdf import render_crop
from fin.map.dictionary import load_accounts
from fin.paths import company_db_path

GRID_ROWS_AROUND = 2  # rows shown above and below an Excel value

FACT_SQL = """
SELECT f.fact_id, f.account, f.fiscal_year, f.period, f.scope, f.value, f.confirmed, f.read_by,
       r.raw_label, r.raw_value_text, r.raw_unit, r.sheet, r.cell, r.page, r.bbox,
       source_ref(r.sheet, r.cell, r.page) AS source_ref,
       d.file_name, d.archive_path
FROM raw_values r
JOIN documents d ON d.doc_id = r.doc_id
LEFT JOIN facts f ON f.raw_id = r.raw_id
WHERE {column} = ?
"""
ID_COLUMN = {"fact_ids": "f.fact_id", "fact_id": "f.fact_id", "raw_id": "r.raw_id"}


def _grid(workbook: Workbook, sheet: str, cell: str) -> str:
    """An HTML table of the cells around `sheet!cell`, from column A to one column past the value,
    with the value highlighted."""
    ws = workbook[sheet]
    _, row = coordinate_from_string(cell)
    target_column = ws[cell].column
    last_column = min(target_column + 1, ws.max_column)
    header = "".join(f"<th>{get_column_letter(c)}</th>" for c in range(1, last_column + 1))
    lines = [f"<table class='grid'><tr><th></th>{header}</tr>"]
    for r in range(max(1, row - GRID_ROWS_AROUND), min(ws.max_row, row + GRID_ROWS_AROUND) + 1):
        cells = []
        for c in range(1, last_column + 1):
            value = ws.cell(row=r, column=c).value
            text = "" if value is None else html.escape(str(value))
            mark = " class='hit'" if (r, c) == (row, target_column) else ""
            cells.append(f"<td{mark}>{text}</td>")
        lines.append(f"<tr><th>{r}</th>{''.join(cells)}</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _source_view(run: Run, fact: dict[str, Any], folder: Path, books: dict[Path, Workbook]) -> str:
    """The crop or grid of a fact's archived source. Raises E604 when the archived file is missing
    or cannot be opened."""
    source = run.root / fact["archive_path"]
    if not source.is_file():
        raise EvidenceSourceMissing(
            f"{fact['file_name']}: archived source {fact['archive_path']} is missing"
        )
    if fact["sheet"]:
        if source not in books:
            try:
                books[source] = load_workbook(source, data_only=True)
            except (OSError, BadZipFile, InvalidFileException) as exc:
                raise EvidenceSourceMissing(
                    f"{fact['file_name']}: archived source {fact['archive_path']} cannot be "
                    f"opened: {exc}"
                ) from exc
        return _grid(books[source], fact["sheet"], fact["cell"])
    image = folder / f"fact_{fact['fact_id']}.png"
    render_crop(source, fact["page"], tuple(json.loads(fact["bbox"])), image)
    return f"<img src='{folder.name}/{image.name}' alt='{html.escape(fact['source_ref'])}'>"


def _fact_section(
    run: Run,
    fact: dict[str, Any],
    folder: Path,
    books: dict[Path, Workbook],
    units: dict[str, str],
) -> str:
    """One fact's evidence: details, then the crop or grid, or an E604 flag. `units`: each
    account's unit (an unmapped figure is in rupees)."""
    ref = fact["source_ref"]
    status = "confirmed" if fact["confirmed"] else "UNCONFIRMED mapping"
    readers = {"claude": "Claude", "docling": "Docling"}
    status += f", read by {readers[fact['read_by']]}" if fact["read_by"] in readers else ""
    unit = units.get(fact["account"], "rupees").replace("_", " ")
    title = (
        f"fact {fact['fact_id']}: {fact['account'] or '(unmapped)'} {fact['period']} "
        f"{fact['scope']} = {fact['value']} {unit} ({status})"
    )
    details = (
        f"<p>Source: <span class='ref'>{html.escape(fact['file_name'])}, {html.escape(ref)}"
        f"</span><br>As written: {html.escape(fact['raw_label'])} = "
        f"{html.escape(fact['raw_value_text'])} {html.escape(fact['raw_unit'])}</p>"
    )
    try:
        body = _source_view(run, fact, folder, books)
    except EvidenceSourceMissing as error:
        run.failed += 1
        run.event("evidence", error.message, level="error", code=error.code, file=fact["file_name"])
        body = f"<p class='flag'>{html.escape(str(error))}</p>"
    return f"<section><h3>{html.escape(title)}</h3>{details}{body}</section>"


def _result_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>No rows.</p>"
    columns = list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row[c]))}</td>" for c in columns) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


STYLE = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2em; color: #222; }
table { border-collapse: collapse; margin: 0.5em 0; }
td, th { border: 1px solid #ccc; padding: 2px 6px; font-size: 13px; }
td.hit { background: #ffe680; outline: 2px solid #d00; }
pre { background: #f4f4f4; padding: 0.5em; white-space: pre-wrap; }
.flag { color: #b00; font-weight: bold; }
img { border: 1px solid #ccc; max-width: 100%; }
"""


def write_evidence(
    run: Run, company: str, question: str | None, sql: str, rows: list[dict[str, Any]]
) -> str:
    """Write the evidence page for this run's query result and return its path relative to the
    data folder. `rows` are JSON rows (`fin.query.run.to_json_rows`)."""
    query_id = run.run_id
    flags = []
    column = next((c for c in ("fact_ids", "fact_id") if rows and c in rows[0]), "raw_id")
    if column == "fact_ids":
        # Every fact behind each value: a summed value has one section per line.
        ids = list(dict.fromkeys(i for row in rows for i in row[column] or []))
        sums = sum(1 for row in rows if len(row[column] or []) > 1)
        if sums:
            flags.append(f"{sums} row(s) are sums of several source lines; each line is below.")
    else:
        ids = list(dict.fromkeys(row[column] for row in rows if column in row))
    if not ids:
        flags.append("The result has no fact_id or raw_id column, so there is nothing to trace.")
    folder = run.root / "evidence" / query_id
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot create {folder}: {exc}") from exc
    books: dict[Path, Workbook] = {}
    units = {name: account.unit for name, account in load_accounts(run.root).items()}
    sections = []
    with connect(company_db_path(run.root, company)) as con:
        for id_ in ids:
            for fact in fetch_dicts(con, FACT_SQL.format(column=ID_COLUMN[column]), [id_]):
                sections.append(_fact_section(run, fact, folder, books, units))
    unconfirmed = sum(1 for row in rows if row.get("source_confirmed") is False)
    if unconfirmed:
        flags.append(f"{unconfirmed} row(s) use facts whose mapping is not confirmed.")
    flag_html = "".join(f"<p class='flag'>{html.escape(flag)}</p>" for flag in flags)
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Evidence {html.escape(query_id)}</title>
<style>{STYLE}</style></head><body>
<h1>Evidence for query {html.escape(query_id)}</h1>
<p>Company: {html.escape(company)}<br>Question: {html.escape(question or "")}</p>
<h2>SQL</h2><pre>{html.escape(sql)}</pre>
{flag_html}
<h2>Result</h2>{_result_table(rows)}
<h2>Sources</h2>
{"".join(sections)}
</body></html>
"""
    path = run.root / "evidence" / f"{query_id}.html"
    try:
        path.write_text(page, encoding="utf-8")
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc
    return path.relative_to(run.root).as_posix()
