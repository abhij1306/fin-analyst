"""`fin verify`: compare an independent reading (`fin.extract.reading`) with what is loaded for a
document.

The document is found by the sha256 of the original file (its current version). Lines are matched
by statement, scope, period and normalized label (`fin.map.normalize`); a label printed more than
once in one statement, scope and period (under different headings) is matched in printed order.
Values are compared in rupees after unit scaling, exactly: a figure copied as printed agrees to the
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
a figure. A document that is not loaded has its findings in the audit log only.

The only thing verify writes to the company database is the `facts.verified` flag. A fact that
agrees becomes verified (in a loaded or a held document). A fact read by
Claude is verified only by a reading other than the one it was loaded from (a different file; the
same file again verifies nothing). A fact that differs is set back to unverified.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.extract.reading import ReadingLine, printed_value, read_reading
from fin.extract.units import to_rupees
from fin.findings import record
from fin.ingest import sha256_of
from fin.map.normalize import normalize_label
from fin.paths import company_db_path

OUTCOMES = ("agree", "differ", "missing_in_fin", "missing_in_reading")

_LOADED_LINES = """
SELECT f.fact_id, f.read_by, r.statement, f.scope, f.period, r.section, r.raw_label,
       r.raw_value_text, r.rupees_per_unit, coalesce(r.sheet || '!' || r.cell, 'page ' || r.page)
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
    raw: str
    rupees: Decimal
    source: str


def _key(statement: str, scope: str, period: str, label: str) -> tuple[str, str, str, str]:
    return statement, scope, period, normalize_label(label)


def _loaded(row: tuple[Any, ...]) -> Loaded:
    fact_id, read_by, statement, scope, period, section, label, raw, per_unit, source = row
    rupees = to_rupees(printed_value(raw, f"fact {fact_id}"), per_unit)
    return Loaded(fact_id, read_by, statement, scope, period, section, label, raw, rupees, source)


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
        results.append(("agree" if fact.rupees == line.rupees else "differ", line, fact))
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
        "message": f"{row['outcome']}: fin {row['fin_printed'] or '-'} ({row['fin_source'] or '-'})"
        f", reading {row['reading_printed'] or '-'} ({row['reading_source'] or '-'})",
    }


Comparison = tuple[str, ReadingLine | None, Loaded | None]


def _loaded_document(db: Path, sha256: str) -> tuple[Any, Any, list[Loaded]]:
    """The newest loaded or held version of the document with this hash: its doc_id, the hash of
    the reading it was loaded with, and its loaded lines. (None, None, []) when it is not loaded."""
    if not db.is_file():
        return None, None, []
    ensure_database(db, "company")
    with connect(db) as con:
        doc = con.execute(
            "SELECT doc_id, reading_sha256 FROM documents WHERE sha256 = ? AND status IN "
            "('loaded', 'held') ORDER BY version DESC LIMIT 1",
            [sha256],
        ).fetchone()
        if doc is None:
            return None, None, []
        doc_id, loaded_reading = doc
        loaded = [_loaded(row) for row in con.execute(_LOADED_LINES, [doc_id]).fetchall()]
    return doc_id, loaded_reading, loaded


def _marks(results: list[Comparison], same_reading: bool) -> tuple[list[Any], list[Any]]:
    """Fact ids to mark verified (they agree, and were not read by Claude from this same reading)
    and unverified (they differ)."""
    verified, unverified = [], []
    for outcome, _, fact in results:
        if fact is None:
            continue
        if outcome == "agree" and not (fact.read_by == "claude" and same_reading):
            verified.append(fact.fact_id)
        elif outcome == "differ":
            unverified.append(fact.fact_id)
    return verified, unverified


def _row(
    outcome: str, line: ReadingLine | None, fact: Loaded | None, verified: list[Any]
) -> dict[str, Any]:
    """One comparison as a result row."""
    either = line if line is not None else fact
    assert either is not None
    return {
        "outcome": outcome,
        "statement": either.statement,
        "scope": either.scope,
        "period": either.period,
        "label": either.label,
        "fin_value": fact.rupees if fact else None,
        "reading_value": line.rupees if line else None,
        "fin_printed": fact.raw if fact else None,
        "reading_printed": line.raw if line else None,
        "fin_source": fact.source if fact else None,
        "reading_source": f"{line.source}, reading line {line.line}" if line else None,
        "fact_id": fact.fact_id if fact else None,
        "read_by": fact.read_by if fact else None,
        "marked_verified": fact is not None and fact.fact_id in verified,
    }


def _save(
    run: Run,
    company: str,
    db: Path,
    doc_id: Any,
    reading_sha: str,
    marks: tuple[list[Any], list[Any]],
    findings: list[dict[str, Any]],
) -> None:
    """In one transaction: set the verified flags and record the findings for the document."""
    with connect(db, write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for ids, flag in zip(marks, (True, False), strict=True):
                if ids:
                    placeholders = ", ".join("?" for _ in ids)
                    con.execute(
                        f"UPDATE facts SET verified = ? WHERE fact_id IN ({placeholders})",
                        [flag, *ids],
                    )
            where = "doc_id = ? AND check_name = 'verify_reading' AND other_sha256 = ?"
            record(run, company, con, findings, where, [doc_id, reading_sha])
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def _notes(company: str, path: Path, doc_id: Any, same_reading: bool) -> list[str]:
    """Notes shown with the result: the document is not loaded, or this is its own reading."""
    notes = []
    if doc_id is None:
        notes.append(f"{path.name} is not loaded for {company}: every reading line is missing")
    if same_reading and doc_id is not None:
        notes.append(
            "this is the reading the document was loaded with: figures read by Claude are not "
            "verified by it; a second, independent reading is needed"
        )
    return notes


def verify(run: Run, company: str, path: Path, reading: Path) -> dict[str, Any]:
    """Compare `reading` with what is loaded for document `path`; mark agreeing facts verified,
    record every comparison, and return the result (see the module docstring)."""
    config = load_config(run.root)
    lines = read_reading(reading, config.fiscal_year_start_month)
    sha256, reading_sha = sha256_of(path), sha256_of(reading)
    db = company_db_path(run.root, company)
    doc_id, loaded_reading, loaded = _loaded_document(db, sha256)
    same_reading = loaded_reading == reading_sha
    results = compare(lines, loaded)
    verified, unverified = _marks(results, same_reading)

    name = f"{company}/{path.name}"
    rows = [_row(outcome, line, fact, verified) for outcome, line, fact in results]
    findings = [
        _finding(company, doc_id, path, reading, reading_sha, row)
        for row in rows
        if row["outcome"] != "agree"
    ]
    if doc_id is None:  # nothing loaded to attach them to: the audit log only
        for finding in findings:
            run.record("findings", finding)
    else:
        _save(run, company, db, doc_id, reading_sha, (verified, unverified), findings)
    counts = {outcome: sum(1 for r in rows if r["outcome"] == outcome) for outcome in OUTCOMES}
    notes = _notes(company, path, doc_id, same_reading)
    run.event(
        "verify",
        f"{name} against {reading.name}: "
        + ", ".join(f"{outcome} {n}" for outcome, n in counts.items())
        + f"; {len(verified)} facts marked verified",
        file=name,
        context={"doc_id": doc_id, "counts": counts, "notes": notes},
    )
    return {
        "company": company,
        "file": path.name,
        "doc_id": doc_id,
        "reading": reading.name,
        "counts": counts,
        "verified_facts": len(verified),
        "verified_claude_facts": sum(
            1 for _, _, f in results if f and f.fact_id in verified and f.read_by == "claude"
        ),
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
        f"marked verified: {result['verified_facts']} facts "
        f"({result['verified_claude_facts']} read by Claude)",
        *(f"note: {note}" for note in result["notes"]),
    ]
    for row in result["lines"]:
        if row["outcome"] == "agree":
            continue
        where = f"{row['statement']} {row['scope']} {row['period']} {row['label']!r}"
        fin, reading = "fin -", "reading -"
        if row["fact_id"] is not None:
            fin = f"fin {row['fin_printed']} ({row['fin_source']})"
        if row["reading_source"] is not None:
            reading = f"reading {row['reading_printed']} ({row['reading_source']})"
        out.append(f"{row['outcome']:<18} {where}: {fin}; {reading}")
    return "\n".join(out)
