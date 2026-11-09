"""`fin verify`: compare a Claude reading of a document (`fin.extract.reading`) with what is loaded
for it. The user runs it whenever they want (/verify), on any loaded or held document, any number
of times: Claude reads the document again and fin compares that reading with what is stored.

The document is found by the sha256 of the original file (its current version). Lines are matched
by statement, scope, period and normalized label (`fin.map.normalize`); a label printed more than
once in one statement, scope and period (under different headings) is matched in printed order.
fin's side is the value fin uses now (`facts.value`, in rupees), not what it first transcribed,
so a figure corrected with `fin sql --write` is checked as corrected. The reading's figure is
converted to rupees with the reading's own unit line (a per-share figure, EPS, is compared as
printed, as fin stores it). The comparison is exact: a figure copied as printed agrees to the
paisa, so there is no tolerance. Outcomes per line and column:

- agree: same value;
- differ: both values and both sources are shown;
- missing_in_fin: the reading has a line the document did not load (or the document, or a whole
  statement, is not loaded);
- missing_in_reading: a loaded line of a statement, scope and period the reading covers that the
  reading does not have.

Every outcome other than agree is a finding (check `verify_reading`, `fin.findings`): shown to the
user, stored with the document (open until accepted with `fin findings accept`, or resolved when a
later verify with the same reading no longer finds it) and appended to the audit table `findings`.
It is not a failure, and the command exits 0 when it ran. fin is an analysis tool: differences are
for the user to decide, and to fix through `fin sql --write` or a new ingest; verify never changes
a figure. A verify finding neither holds nor releases a document: a loaded document stays loaded
and a held one stays held (verify works on held documents too, to tell whether a held document's
finding is a misread). A document that is not loaded has its findings in the audit log only.

Each run on a loaded or held document is recorded in the company database: one `verify_runs` row
(run, document, reading, the count of each outcome) and one `verify_lines` row per line compared.
Any reading can be compared, including the one the document was loaded with. Whether a figure read
by Claude is trusted is the user's decision; verify records what it found and marks nothing.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect, transaction, utcnow
from fin.db.schema import ensure_database
from fin.extract.reading import ReadingLine, printed_value, read_reading
from fin.extract.units import to_rupees
from fin.findings import record
from fin.ingest import sha256_of
from fin.map.dictionary import load_accounts
from fin.map.normalize import normalize_label
from fin.paths import company_db_path

PER_SHARE = "rupees_per_share"
OUTCOMES = ("agree", "differ", "missing_in_fin", "missing_in_reading")

_LOADED_LINES = """
SELECT f.fact_id, f.read_by, r.statement, f.scope, f.period, r.section, r.raw_label,
       r.raw_value_text, f.value, f.account IN (SELECT unnest(?)),
       coalesce(r.sheet || '!' || r.cell, 'page ' || r.page)
FROM facts f
JOIN raw_values r ON r.raw_id = f.raw_id
WHERE f.doc_id = ?
ORDER BY r.raw_id
"""


@dataclass(frozen=True)
class Loaded:
    """One line fin loaded for the document."""

    fact_id: int
    read_by: str
    statement: str
    scope: str
    period: str
    section: str
    label: str
    raw: str  # as printed
    value: Decimal  # facts.value: rupees, or rupees per share for a per-share account
    per_share: bool
    source: str


def _key(statement: str, scope: str, period: str, label: str) -> tuple[str, str, str, str]:
    return statement, scope, period, normalize_label(label)


def reading_value(line: ReadingLine, fact: Loaded | None) -> Decimal:
    """The reading's figure in the fact's unit: rupees, or as printed for a per-share fact."""
    if fact is not None and fact.per_share:
        return to_rupees(printed_value(line.raw, f"reading line {line.line}"), 1)
    return line.rupees


def compare(
    lines: list[ReadingLine], loaded: list[Loaded]
) -> list[tuple[str, ReadingLine | None, Loaded | None]]:
    """(outcome, reading line, loaded line) for every line of either side. Loaded lines of a
    statement, scope and period the reading does not cover are left out."""
    covered = {(line.statement, line.scope, line.period) for line in lines}
    pool: dict[tuple[str, str, str, str], list[Loaded]] = {}
    for fact in loaded:
        if (fact.statement, fact.scope, fact.period) in covered:
            pool.setdefault(_key(fact.statement, fact.scope, fact.period, fact.label), []).append(
                fact
            )
    results: list[tuple[str, ReadingLine | None, Loaded | None]] = []
    for line in lines:
        candidates = pool.get(_key(line.statement, line.scope, line.period, line.label), [])
        if not candidates:
            results.append(("missing_in_fin", line, None))
            continue
        fact = candidates.pop(0)
        agrees = fact.value == reading_value(line, fact)
        results.append(("agree" if agrees else "differ", line, fact))
    leftover = sorted((f for facts in pool.values() for f in facts), key=lambda f: f.fact_id)
    results.extend(("missing_in_reading", None, fact) for fact in leftover)
    return results


def _finding(
    company: str,
    doc_id: int | None,
    path: Path,
    reading: Path,
    reading_sha: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    """An audit `findings` row. The columns are general: `fin_*` is what is loaded, `other_*` what
    it is compared with (here a reading; for another check, another document)."""
    fin, other = row["fin_value"], row["reading_value"]
    return {
        "company": company,
        "check_name": "verify_reading",
        "finding": row["outcome"],
        "doc_id": doc_id,
        "file_name": path.name,
        "statement": row["statement"],
        "scope": row["scope"],
        "period": row["period"],
        "label": row["label"],
        "fin_value": fin,
        "other_value": other,
        "difference": other - fin if fin is not None and other is not None else None,
        "fin_printed": row["fin_printed"],
        "other_printed": row["reading_printed"],
        "fin_source": row["fin_source"],
        "other_source": row["reading_source"],
        "other_file": reading.name,
        "other_sha256": reading_sha,
        "fact_id": row["fact_id"],
        "read_by": row["read_by"],
        "message": f"{row['outcome']}: {_sides(row)}",
    }


def _sides(row: dict[str, Any]) -> str:
    """Both sides of a comparison: the value compared, then as printed and where."""
    sides = []
    for side in ("fin", "reading"):
        value, printed, source = (row[f"{side}_{k}"] for k in ("value", "printed", "source"))
        sides.append(
            f"{side} -" if source is None else f"{side} {value} (printed {printed}, {source})"
        )
    return "; ".join(sides)


def _loaded_document(root: Path, db: Path, sha256: str) -> tuple[Any, list[Loaded]]:
    """The newest loaded or held version of the document with this hash: its doc_id and its loaded
    lines. (None, []) when it is not loaded."""
    if not db.is_file():
        return None, []
    ensure_database(db, "company")
    with connect(db) as con:
        doc = con.execute(
            "SELECT doc_id FROM documents WHERE sha256 = ? AND status IN ('loaded', 'held') "
            "ORDER BY version DESC LIMIT 1",
            [sha256],
        ).fetchone()
        if doc is None:
            return None, []
        doc_id = doc[0]
        per_share = [a for a, account in load_accounts(root).items() if account.unit == PER_SHARE]
        rows = con.execute(_LOADED_LINES, [per_share, doc_id]).fetchall()
        loaded = [Loaded(*row[:9], bool(row[9]), row[10]) for row in rows]
    return doc_id, loaded


def _row(outcome: str, line: ReadingLine | None, fact: Loaded | None) -> dict[str, Any]:
    """One comparison as a result row."""
    either = line if line is not None else fact
    assert either is not None
    return {
        "outcome": outcome,
        "statement": either.statement,
        "scope": either.scope,
        "period": either.period,
        "label": either.label,
        "fin_value": fact.value if fact else None,
        "reading_value": reading_value(line, fact) if line else None,
        "fin_printed": fact.raw if fact else None,
        "reading_printed": line.raw if line else None,
        "fin_source": fact.source if fact else None,
        "reading_source": f"{line.source}, reading line {line.line}" if line else None,
        "fact_id": fact.fact_id if fact else None,
        "read_by": fact.read_by if fact else None,
    }


_LINE_COLUMNS = (
    "outcome", "statement", "scope", "period", "label", "fact_id", "read_by", "fin_value",
    "reading_value", "fin_printed", "reading_printed", "fin_source", "reading_source",
)  # fmt: skip


def _save(
    run: Run,
    company: str,
    db: Path,
    verify_run: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    """In one transaction: record the run and its lines, and the findings for the document."""
    with connect(db, write=True) as con, transaction(con):
        con.execute(
            f"INSERT INTO verify_runs ({', '.join(verify_run)}) "
            f"VALUES ({', '.join('?' for _ in verify_run)})",
            list(verify_run.values()),
        )
        if rows:
            con.executemany(
                f"INSERT INTO verify_lines (run_id, line_no, {', '.join(_LINE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in range(len(_LINE_COLUMNS) + 2))})",
                [
                    [run.run_id, n, *(row[c] for c in _LINE_COLUMNS)]
                    for n, row in enumerate(rows, start=1)
                ],
            )
        where = "doc_id = ? AND check_name = 'verify_reading' AND other_sha256 = ?"
        record(
            run, company, con, findings, where, [verify_run["doc_id"], verify_run["reading_sha256"]]
        )


def verify(run: Run, company: str, path: Path, reading: Path) -> dict[str, Any]:
    """Compare `reading` with what is loaded for document `path`, record the run, its lines and
    findings, and return the result (see the module docstring)."""
    config = load_config(run.root)
    lines = read_reading(reading, config.fiscal_year_start_month)
    sha256, reading_sha = sha256_of(path), sha256_of(reading)
    db = company_db_path(run.root, company)
    doc_id, loaded = _loaded_document(run.root, db, sha256)
    results = compare(lines, loaded)

    name = f"{company}/{path.name}"
    rows = [_row(outcome, line, fact) for outcome, line, fact in results]
    findings = [
        _finding(company, doc_id, path, reading, reading_sha, row)
        for row in rows
        if row["outcome"] != "agree"
    ]
    counts = {outcome: sum(1 for r in rows if r["outcome"] == outcome) for outcome in OUTCOMES}
    notes = []
    if doc_id is None:  # nothing loaded to attach them to: the audit log only
        notes.append(f"{path.name} is not loaded for {company}: every reading line is missing")
        for finding in findings:
            run.record("findings", finding)
    else:
        verify_run = {
            "run_id": run.run_id,
            "doc_id": doc_id,
            "file_name": path.name,
            "reading_file": reading.name,
            "reading_sha256": reading_sha,
            "verified_at": utcnow(),
            **counts,
        }
        _save(run, company, db, verify_run, rows, findings)
    run.event(
        "verify",
        f"{name} against {reading.name}: "
        + ", ".join(f"{outcome} {n}" for outcome, n in counts.items()),
        file=name,
        context={"doc_id": doc_id, "counts": counts, "notes": notes},
    )
    return {
        "company": company,
        "file": path.name,
        "doc_id": doc_id,
        "reading": reading.name,
        "run_id": run.run_id,
        "recorded": doc_id is not None,
        "counts": counts,
        "notes": notes,
        "lines": rows,
    }


def format_result(result: dict[str, Any]) -> str:
    """Plain-text summary: counts, then every line that does not agree."""
    counts = result["counts"]
    doc = f"doc_id {result['doc_id']}" if result["doc_id"] is not None else "not loaded"
    out = [
        f"verify {result['company']}/{result['file']} ({doc}) against {result['reading']}",
        "agree {agree}, differ {differ}, missing in fin {missing_in_fin}, "
        "missing in reading {missing_in_reading}".format(**counts),
        *(
            [f"recorded as verify run {result['run_id']} (verify_runs, verify_lines)"]
            if result["recorded"]
            else []
        ),
        *(f"note: {note}" for note in result["notes"]),
    ]
    for row in result["lines"]:
        if row["outcome"] == "agree":
            continue
        where = f"{row['statement']} {row['scope']} {row['period']} {row['label']!r}"
        out.append(f"{row['outcome']:<18} {where}: {_sides(row)}")
    return "\n".join(out)
