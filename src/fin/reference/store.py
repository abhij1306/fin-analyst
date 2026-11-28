"""`fin reference add` (SPEC-10): store a third party's table apart from the company's facts.

The file is read as a company file is (`fin.ingest_read`: fin's reader, the unit the user gave, or
the assistant's reading file) and archived under archive/<company>/reference/. Its figures go into
`reference_values` in one transaction; nothing reaches facts, current_facts or metrics. Each line
not seen before gets a `reference_lines` row: mapped when the dictionary knows its label (as a
company fact's label is), else waiting for the assistant's proposal.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb

from fin.audit.errors import (
    DataFolderNotWritable,
    DependencyMissing,
    DuplicateFile,
    FileUnreadable,
    ReferenceInvalid,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect, no_company_message, transaction, utcnow
from fin.db.rows import fetch_one
from fin.ingest_read import (
    GivenUnit,
    Row,
    check_range,
    read_document,
    read_with_reading,
    sha256_of,
)
from fin.map.dictionary import LabelMap, find, label_key, load_label_map
from fin.paths import archive_dir, company_db_path

INSERT_VALUE = (
    "INSERT INTO reference_values (ref_file_id, line_id, sheet, cell, page, raw_value_text, "
    "raw_unit, rupees_per_unit, fiscal_year, period, period_months, period_end, scope, value, "
    "read_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@dataclass(frozen=True)
class Added:
    ref_file_id: int
    figures: int
    new_lines: int
    waiting: int  # lines of this file with no mapping yet

    @property
    def line(self) -> str:
        return (
            f"reference file #{self.ref_file_id}: {self.figures} figures, {self.new_lines} new "
            f"lines, {self.waiting} lines waiting for a mapping"
        )


def _archive(run: Run, company: str, source: Path, name: str) -> str:
    target = archive_dir(run.root, company) / "reference" / name
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {source.name} to {target}: {exc}") from exc
    return target.relative_to(run.root).as_posix()


def add_reference(
    run: Run,
    config: Config,
    company: str,
    path: Path,
    source: str,
    *,
    unit: GivenUnit | None = None,
    reading: Path | None = None,
) -> Added:
    """Load `path` as the reference `source`. E980 without a source name, E001 for a company with
    no database, E105 for a file added before; the reader's own errors (E101 to E207) as for
    `fin ingest`."""
    source = source.strip()
    if not source:
        raise ReferenceInvalid("name the third party the figures come from (--source)")
    db = company_db_path(run.root, company)
    if not db.is_file():
        raise DependencyMissing(no_company_message(company))
    if not path.is_file():
        raise FileUnreadable(f"{path} is not a file").with_context(path=str(path))
    sha256 = sha256_of(path)
    _refuse_duplicate(db, path.name, sha256)
    if reading is None:
        document = read_document(run, path, company, config, unit)
    else:
        document = read_with_reading(run, path, company, config, reading, unit)
    check_range(document, path.name)
    archived = _archive(run, company, path, f"{sha256}{path.suffix.lower()}")
    reading_path = None
    if reading is not None:
        reading_path = _archive(run, company, reading, f"{sha256_of(reading)}.reading.csv")
    label_map = load_label_map(run.root, company)
    with connect(db, write=True) as con, transaction(con):
        (ref_file_id,) = fetch_one(
            con,
            "INSERT INTO reference_files (source, file_name, sha256, archive_path, reading_path, "
            "unit_given, run_id, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING ref_file_id",
            [
                source,
                path.name,
                sha256,
                archived,
                reading_path,
                unit and unit.unit,
                run.run_id,
                utcnow(),
            ],
        )
        lines: dict[tuple[str, str, str], int] = {}
        new = 0
        for row in document.rows:
            key = label_key(row.statement, row.section, row.label)
            if key not in lines:
                lines[key], created = _line(run, con, label_map, row, key)
                new += created
        con.executemany(INSERT_VALUE, [_value(ref_file_id, lines, r) for r in document.rows])
        (waiting,) = fetch_one(
            con,
            "SELECT count(*) FROM reference_lines WHERE status NOT IN ('dictionary', 'confirmed') "
            "AND list_contains(?::INTEGER[], line_id)",
            [list(lines.values())],
        )
    added = Added(ref_file_id, len(document.rows), new, waiting)
    run.event("reference", f"{company}/{path.name} ({source}): {added.line}")
    return added


def _refuse_duplicate(db: Path, name: str, sha256: str) -> None:
    with connect(db) as con:
        found = con.execute(
            "SELECT ref_file_id FROM reference_files WHERE sha256 = ?", [sha256]
        ).fetchone()
    if found is not None:
        raise DuplicateFile(f"{name}: already added as reference file #{found[0]} (same sha256)")


def _line(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    label_map: LabelMap,
    row: Row,
    key: tuple[str, str, str],
) -> tuple[int, int]:
    """(line_id, 1 when this call added the line, else 0)."""
    found = con.execute(
        "SELECT line_id FROM reference_lines WHERE statement = ? AND section_key = ? "
        "AND label_key = ?",
        list(key),
    ).fetchone()
    if found is not None:
        return found[0], 0
    mapping = find(label_map, row.statement, row.section, row.label)
    mapped = mapping is not None and not mapping.per_share  # a per-share line is not money
    (line_id,) = fetch_one(
        con,
        "INSERT INTO reference_lines (statement, section_key, label_key, section, label, status, "
        "target_kind, target, actor, run_id, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING line_id",
        [
            *key,
            row.section,
            row.label,
            "dictionary" if mapped else "pending",
            "account" if mapped else None,
            mapping.account if mapped and mapping else None,
            "fin" if mapped else None,
            run.run_id,
            utcnow(),
        ],
    )
    return line_id, 1


def _value(ref_file_id: int, lines: dict[tuple[str, str, str], int], r: Row) -> list[object]:
    return [
        ref_file_id,
        lines[label_key(r.statement, r.section, r.label)],
        r.sheet,
        r.cell,
        r.page,
        r.raw,
        r.unit_text,
        r.rupees_per_unit,
        r.fiscal_year,
        r.period,
        r.period_months,
        r.period_end,
        r.scope,
        r.rupees,
        r.read_by,
    ]
