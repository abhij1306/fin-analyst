"""`fin evidence <query_id>`: an HTML page showing where every fact behind a query result came from.

For each fact: a highlighted crop of the PDF row, or a small grid of the Excel cells around the
value (read from the archived workbook). A missing source file is flagged on the page with E604;
the page is still written. Output: `evidence/<query_id>.html`, with crops in `evidence/<query_id>/`.
"""

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, get_column_letter
from openpyxl.workbook.workbook import Workbook

from fin.audit.errors import DataFolderNotWritable, EvidenceSourceMissing, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.extract.pdf import render_crop
from fin.paths import audit_db_path, company_db_path
from fin.query.run import result_hash, run_select, to_json_rows

GRID_ROWS_AROUND = 2  # rows shown above and below an Excel value

FACT_SQL = """
SELECT f.fact_id, f.account, f.fiscal_year, f.scope, f.value, f.confirmed,
       r.raw_label, r.raw_value_text, r.raw_unit, r.sheet, r.cell, r.page, r.bbox,
       d.file_name, d.archive_path
FROM raw_values r
JOIN documents d ON d.doc_id = r.doc_id
LEFT JOIN facts f ON f.raw_id = r.raw_id
WHERE {column} = ?
"""
ID_COLUMN = {"fact_id": "f.fact_id", "raw_id": "r.raw_id"}


@dataclass
class Logged:
    """The `queries` row a query_id refers to."""

    query_id: str
    company: str
    question: str | None
    sql: str
    row_count: int | None
    result_hash: str | None


def load_query(root: Path, query_id: str) -> Logged:
    with connect(audit_db_path(root)) as con:
        found = con.execute(
            "SELECT query_id, company, question, sql, row_count, result_hash FROM queries "
            "WHERE query_id = ? AND mode = 'read' ORDER BY recorded_at LIMIT 1",
            [query_id],
        ).fetchone()
    if found is None:
        raise SqlFailed(f"no `fin query` with query_id {query_id} in the audit log")
    logged = Logged(*found)
    if logged.row_count is None:
        raise SqlFailed(f"query {query_id} was rejected or failed; there is no result to trace")
    return logged


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


def _fact_section(run: Run, fact: dict[str, Any], folder: Path, books: dict[Path, Workbook]) -> str:
    """One fact's evidence: details, then the crop or grid, or an E604 flag."""
    ref = f"{fact['sheet']}!{fact['cell']}" if fact["sheet"] else f"page {fact['page']}"
    status = "confirmed" if fact["confirmed"] else "UNCONFIRMED mapping"
    title = (
        f"fact {fact['fact_id']}: {fact['account'] or '(unmapped)'} {fact['fiscal_year']} "
        f"{fact['scope']} = {fact['value']} rupees ({status})"
    )
    details = (
        f"<p>Source: <span class='ref'>{html.escape(fact['file_name'])}, {html.escape(ref)}"
        f"</span><br>As written: {html.escape(fact['raw_label'])} = "
        f"{html.escape(fact['raw_value_text'])} {html.escape(fact['raw_unit'])}</p>"
    )
    source = run.root / fact["archive_path"]
    if not source.is_file():
        error = EvidenceSourceMissing(
            f"{fact['file_name']}: archived source {fact['archive_path']} is missing"
        )
        run.failed += 1
        run.event("evidence", error.message, level="error", code=error.code, file=fact["file_name"])
        body = f"<p class='flag'>{html.escape(str(error))}</p>"
    elif fact["sheet"]:
        if source not in books:
            books[source] = load_workbook(source, data_only=True)
        body = _grid(books[source], fact["sheet"], fact["cell"])
    else:
        image = folder / f"fact_{fact['fact_id']}.png"
        render_crop(source, fact["page"], tuple(json.loads(fact["bbox"])), image)
        body = f"<img src='{folder.name}/{image.name}' alt='{html.escape(ref)}'>"
    return f"<section><h3>{html.escape(title)}</h3>{details}{body}</section>"


def _result_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>No rows.</p>"
    columns = list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row[c]))}</td>" for c in columns) + "</tr>"
        for row in to_json_rows(rows)
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


def write_evidence(run: Run, query_id: str) -> Path:
    """Write the evidence page for a logged query, record its path, and return it."""
    logged = load_query(run.root, query_id)
    rows = run_select(run.root, logged.company, logged.sql)
    flags = []
    if result_hash(rows) != logged.result_hash:
        flags.append("The data changed since the query ran; this page shows the current result.")
    column = "fact_id" if rows and "fact_id" in rows[0] else "raw_id"
    ids = list(dict.fromkeys(row[column] for row in rows if column in row))
    if not ids:
        flags.append("The result has no fact_id or raw_id column, so there is nothing to trace.")
    folder = run.root / "evidence" / query_id
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot create {folder}: {exc}") from exc
    books: dict[Path, Workbook] = {}
    sections = []
    with connect(company_db_path(run.root, logged.company)) as con:
        for id_ in ids:
            found = con.execute(FACT_SQL.format(column=ID_COLUMN[column]), [id_])
            names = [d[0] for d in found.description or []]
            for record in found.fetchall():
                fact = dict(zip(names, record, strict=True))
                sections.append(_fact_section(run, fact, folder, books))
    unconfirmed = sum(1 for row in rows if row.get("source_confirmed") is False)
    if unconfirmed:
        flags.append(f"{unconfirmed} row(s) use facts whose mapping is not confirmed.")
    flag_html = "".join(f"<p class='flag'>{html.escape(flag)}</p>" for flag in flags)
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Evidence {html.escape(query_id)}</title>
<style>{STYLE}</style></head><body>
<h1>Evidence for query {html.escape(query_id)}</h1>
<p>Company: {html.escape(logged.company)}<br>Question: {html.escape(logged.question or "")}<br>
Evidence run: {html.escape(run.run_id)}</p>
<h2>SQL</h2><pre>{html.escape(logged.sql)}</pre>
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
    relative = path.relative_to(run.root).as_posix()
    run.record(
        "queries",
        {
            "query_id": query_id,
            "company": logged.company,
            "question": logged.question,
            "sql": logged.sql,
            "mode": "read",
            "row_count": len(rows),
            "result_hash": result_hash(rows),
            "evidence_path": relative,
        },
    )
    return path
