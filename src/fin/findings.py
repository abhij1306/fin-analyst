"""Findings: what a company filed that does not add up, shown to the user instead of rejected.

fin is an analysis tool, not an opinionated one. A balance sheet that does not tie, a subtotal not
equal to its lines, closing cash that does not reconcile, a figure that differs between documents
(a restatement, a quarter vs its year) or from a Claude reading (`fin verify`) is a finding. Only a
file with nothing readable (E1xx/E2xx) fails.

A finding lives in the company database's `findings` table with a status (open, accepted, resolved,
superseded; see db/schema/company.sql), and every finding and status change is appended to the
audit table `findings`.

- Ingest stages each document and runs the validation gates (`fin.validate.gates`) on it. A
  document with open findings is HELD (`documents.status = 'held'`): its facts are kept but left out
  of current figures and metrics until the user decides.
- Save as is: `fin findings accept` marks findings accepted. A held document with no open findings
  left is loaded exactly as filed; its accepted findings stay attached and visible.
- Fix: Claude changes the data with the existing tools. Every held document of the company is
  checked again after `fin sql --write` and `fin review confirm` (`recheck_held`), and so is every
  loaded document whose facts a confirmation mapped (a check that could not run before may run now;
  a loaded document that fails it is held). A new version from `fin ingest --force` is checked like
  any document (it supersedes the earlier version and its findings). A finding a check no longer
  finds becomes resolved; a held document with no open findings is loaded. `fin verify` resolves
  its own findings the same way when run again.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import FindingNotOpen
from fin.audit.logger import Run
from fin.db.conn import connect, transaction, utcnow
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.validate.gates import validate_document

FIELDS = (
    "doc_id",
    "file_name",
    "check_name",
    "code",
    "finding",
    "statement",
    "scope",
    "period",
    "label",
    "fin_value",
    "other_value",
    "difference",
    "fin_printed",
    "other_printed",
    "fin_source",
    "other_source",
    "other_file",
    "other_sha256",
    "fact_id",
    "read_by",
    "message",
)
# Two findings are the same finding when these match.
SAME = (
    "doc_id",
    "check_name",
    "finding",
    "statement",
    "scope",
    "period",
    "label",
    "fin_value",
    "other_value",
    "other_file",
)


def _audit(run: Run, company: str, con: duckdb.DuckDBPyConnection, ids: list[int]) -> None:
    """Append the findings `ids`, as they are now, to the audit table `findings`."""
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    result = con.execute(
        f"SELECT finding_id, status, {', '.join(FIELDS)} FROM findings "
        f"WHERE finding_id IN ({marks}) ORDER BY finding_id",
        ids,
    )
    columns = [c[0] for c in result.description]
    for row in result.fetchall():
        run.record("findings", {"company": company, **dict(zip(columns, row, strict=True))})


def set_status(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, ids: list[int], status: str
) -> None:
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    con.execute(
        f"UPDATE findings SET status = ?, status_run_id = ?, status_at = ? "
        f"WHERE finding_id IN ({marks})",
        [status, run.run_id, utcnow(), *ids],
    )
    _audit(run, company, con, ids)


def record(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    rows: list[dict[str, Any]],
    where: str,
    params: list[Any],
) -> None:
    """Store one run of a check: each row not already an open or accepted finding is added (open);
    an open or accepted finding matching `where` (the scope the check covered) that the run did not
    find again is resolved."""
    result = con.execute(
        f"SELECT finding_id, {', '.join(SAME)} FROM findings "
        f"WHERE status IN ('open', 'accepted') AND {where}",
        params,
    )
    existing = {tuple(row[1:]): row[0] for row in result.fetchall()}
    found, added = set(), []
    for row in rows:
        key = tuple(row.get(field) for field in SAME)
        if key in existing:
            found.add(existing[key])
            continue
        values = [row.get(field) for field in FIELDS]
        new = con.execute(
            f"INSERT INTO findings ({', '.join(FIELDS)}, status, run_id, recorded_at, "
            f"status_run_id, status_at) VALUES ({', '.join('?' for _ in FIELDS)}, 'open', ?, "
            "?, ?, ?) RETURNING finding_id",
            [*values, run.run_id, utcnow(), run.run_id, utcnow()],
        ).fetchone()
        assert new is not None
        existing[key] = new[0]
        found.add(new[0])
        added.append(new[0])
    _audit(run, company, con, added)
    set_status(run, company, con, [i for i in existing.values() if i not in found], "resolved")


def _settle(con: duckdb.DuckDBPyConnection, doc_id: int) -> int:
    """Hold the document while it has open findings, else load it. Returns its open findings."""
    row = con.execute(
        "SELECT count(*) FROM findings WHERE doc_id = ? AND status = 'open'", [doc_id]
    ).fetchone()
    assert row is not None
    open_findings = int(row[0])
    con.execute(
        "UPDATE documents SET status = ? WHERE doc_id = ?",
        ["held" if open_findings else "loaded", doc_id],
    )
    return open_findings


def check_document(run: Run, company: str, con: duckdb.DuckDBPyConnection, doc_id: int) -> int:
    """Run the validation gates on a staged or held document, record its findings, and hold the
    document while it has open ones (else load it). Returns the number of open findings."""
    rows = validate_document(run, company, con, doc_id)
    record(run, company, con, rows, "doc_id = ? AND check_name <> 'verify_reading'", [doc_id])
    return _settle(con, doc_id)


def supersede(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, doc_id: int, sha256: str
) -> None:
    """Mark the earlier versions of a file (loaded or held) and their findings superseded by
    `doc_id`."""
    earlier = [
        d
        for (d,) in con.execute(
            "UPDATE documents SET status = 'superseded', superseded_by = ? "
            "WHERE sha256 = ? AND doc_id <> ? AND status IN ('loaded', 'held') RETURNING doc_id",
            [doc_id, sha256, doc_id],
        ).fetchall()
    ]
    if earlier:
        marks = ", ".join("?" for _ in earlier)
        ids = con.execute(
            f"SELECT finding_id FROM findings WHERE doc_id IN ({marks}) "
            "AND status IN ('open', 'accepted')",
            earlier,
        ).fetchall()
        set_status(run, company, con, [i for (i,) in ids], "superseded")


def recheck_held(run: Run, company: str, changed: Iterable[int] = ()) -> list[dict[str, Any]]:
    """Check every held document of the company again (after a fix), and the loaded documents in
    `changed` (whose facts the fix changed): a loaded one that now fails is held. Returns one row
    per document: doc_id, file, open findings, status now."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        return []
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        held = con.execute(
            "SELECT doc_id, file_name FROM documents WHERE status = 'held' "
            "OR (status = 'loaded' AND list_contains(?::BIGINT[], doc_id)) ORDER BY doc_id",
            [list(changed)],
        ).fetchall()
        out = []
        for doc_id, file_name in held:
            with transaction(con):
                open_findings = check_document(run, company, con, doc_id)
            status = "held" if open_findings else "loaded"
            out.append(
                {"doc_id": doc_id, "file": file_name, "open": open_findings, "status": status}
            )
            run.event(
                "findings",
                f"rechecked doc_id {doc_id}: {open_findings} open findings, {status}",
                file=f"{company}/{file_name}",
            )
    return out


def accept(run: Run, company: str, ids: list[int]) -> list[dict[str, Any]]:
    """Save as is: mark open findings accepted. A held document with no open findings left is
    loaded as filed. E406 (nothing changed) for an id that is not an open finding of the company.
    Returns one row per document the findings belong to: doc_id, file, open findings, status."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        raise FindingNotOpen(f"no database for company {company!r}; nothing accepted")
    ensure_database(path, "company")
    with connect(path, write=True) as con, transaction(con):
        for finding_id in ids:
            row = con.execute(
                "SELECT status FROM findings WHERE finding_id = ?", [finding_id]
            ).fetchone()
            if row is None or row[0] != "open":
                state = "no such finding" if row is None else f"it is {row[0]}"
                raise FindingNotOpen(
                    f"finding {finding_id} of {company} is not open ({state}); "
                    "nothing accepted. Open findings: `fin findings list`"
                )
        set_status(run, company, con, ids, "accepted")
        marks = ", ".join("?" for _ in ids)
        docs = con.execute(
            "SELECT d.doc_id, d.file_name, d.status, "
            "(SELECT count(*) FROM findings o WHERE o.doc_id = d.doc_id AND o.status = 'open') "
            f"FROM documents d WHERE d.doc_id IN (SELECT doc_id FROM findings "
            f"WHERE finding_id IN ({marks})) ORDER BY d.doc_id",
            ids,
        ).fetchall()
        out = []
        for doc_id, file_name, status, open_findings in docs:
            if status == "held" and _settle(con, doc_id) == 0:
                status = "loaded"
                run.event(
                    "findings",
                    f"doc_id {doc_id} loaded as filed: its findings were accepted",
                    file=f"{company}/{file_name}",
                )
            out.append(
                {"doc_id": doc_id, "file": file_name, "open": open_findings, "status": status}
            )
    return out


_LIST_SQL = """
SELECT f.finding_id, f.status, f.code, f.check_name, f.finding, f.doc_id, f.file_name,
       d.status AS document_status, f.statement, f.scope, f.period, f.label, f.fin_value,
       f.other_value, f.difference, f.fin_printed, f.other_printed, f.fin_source, f.other_source,
       f.other_file, f.message
FROM findings f
LEFT JOIN documents d ON d.doc_id = f.doc_id
WHERE f.status IN ('open', 'accepted')
ORDER BY f.status DESC, f.doc_id, f.finding_id
"""


def list_findings(run: Run, company: str) -> list[dict[str, Any]]:
    """Open findings first, then accepted ones, with both values and both sources."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        return []
    ensure_database(path, "company")
    with connect(path) as con:
        result = con.execute(_LIST_SQL)
        columns = [c[0] for c in result.description]
        return [
            {"company": company, **dict(zip(columns, row, strict=True))}
            for row in result.fetchall()
        ]


def _shown(printed: str | None, value: Any) -> str:
    """The figure as the document printed it (in its own unit), else the value in rupees."""
    if printed:
        return f"{printed} as printed"
    return f"₹{Decimal(str(value)):,}" if value is not None else "-"


def format_finding(row: dict[str, Any]) -> str:
    """Two lines: what and where, then this document's figure and what it was compared with."""
    document = f"{row['file_name']} (doc_id {row['doc_id']}, {row['document_status']})"
    head = (
        f"#{row['finding_id']} {row['status']} {row['code'] or row['check_name']} "
        f"{row['company']}/{document}: {row['message']}"
    )
    fin = f"{_shown(row['fin_printed'], row['fin_value'])} at {row['fin_source'] or '-'}"
    other_where = " ".join(x for x in (row["other_file"], row["other_source"]) if x) or "-"
    other = f"{_shown(row['other_printed'], row['other_value'])} at {other_where}"
    return f"{head}\n    this document: {fin}; compared with: {other}"
