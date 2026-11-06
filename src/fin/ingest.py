"""Load documents into a company database (PLAN.md Phase 3).

For each file, `ingest_file`:

1. Hashes it. A file whose sha256 is already loaded is skipped with E105, unless `force`, which
   loads it again as a new version and marks the earlier one superseded.
2. Extracts its values: Excel (`fin.extract.excel`) or PDF (`fin.extract.pdf`; notes and the
   auditor's report also go to `notes/<company>/` as Markdown).
3. Copies the original to `archive/<company>/<sha256>.<ext>`.
4. In ONE transaction: inserts the document, its raw values and their facts in rupees, runs every
   function in `VALIDATORS` (the validation gates, `fin.validate.gates`), then commits. Any failure
   rolls the whole document back.

A label the dictionary does not map still loads, as a fact with no account and confirmed = false,
and is logged with E301. A file that fails (E1xx-E4xx) is copied to `review/<company>/` with a
`.error.json` file giving the code and reason; the next file goes on.
"""

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from fin.audit.errors import (
    EXIT_FAILURES,
    DataFolderNotWritable,
    DocumentFailure,
    DuplicateFile,
    FileUnreadable,
    UnmappedLabel,
    UnsupportedFormat,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect
from fin.extract.excel import extract_workbook
from fin.extract.pdf import extract_pdf
from fin.extract.text import write_notes_markdown
from fin.extract.units import RUPEES_PER_UNIT
from fin.map.dictionary import LabelMap, find
from fin.paths import archive_dir, company_db_path, review_dir
from fin.validate.gates import validate_document

COMPANY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EXCEL_SUFFIXES = (".xlsx", ".xlsm")

# Checks run on a staged document before commit: f(run, company, connection, doc_id). Raise a
# DocumentFailure (E4xx) to roll the document back.
Validator = Callable[[Run, str, duckdb.DuckDBPyConnection, int], None]
VALIDATORS: list[Validator] = [validate_document]


@dataclass(frozen=True)
class Row:
    """One value as it goes into raw_values and facts."""

    sheet: str | None
    cell: str | None
    page: int | None
    bbox: str | None
    statement: str
    section: str
    label: str
    raw: str
    unit_text: str
    rupees_per_unit: int
    period_end: date
    fiscal_year: str
    scope: str
    rupees: Decimal


@dataclass(frozen=True)
class Document:
    rows: list[Row]
    notes: list[tuple[str, Path]]  # (section, markdown file)
    notices: list[str]  # things worth logging, e.g. an assumed scope
    warnings: list[str] = field(default_factory=list)  # e.g. a statement page that was skipped


def fiscal_year_of(period_end: date, start_month: int) -> str:
    """'FY25' for the fiscal year that ends in 2025 (31 March 2025 with an April start)."""
    end_year = period_end.year if period_end.month < start_month else period_end.year + 1
    return f"FY{end_year % 100:02d}"


def files_in(path: Path) -> list[Path]:
    """`path` itself, or the files in folder `path` (sorted, hidden files left out). E101 if it
    does not exist."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileUnreadable(f"{path} does not exist")
    return sorted(p for p in path.iterdir() if p.is_file() and not p.name.startswith("."))


def sha256_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc


def read_document(path: Path, root: Path, company: str, config: Config) -> Document:
    """Extract the values of an Excel or PDF file. E103 for any other format."""
    suffix = path.suffix.lower()
    start = config.fiscal_year_start_month
    if suffix in EXCEL_SUFFIXES:
        extraction = extract_workbook(path)
        rows = [
            Row(
                sheet=v.sheet,
                cell=v.cell,
                page=None,
                bbox=None,
                statement=v.statement,
                section=v.section,
                label=v.label,
                raw=v.raw,
                unit_text=v.unit_text,
                rupees_per_unit=int(RUPEES_PER_UNIT[v.unit]),
                period_end=v.period_end,
                fiscal_year=fiscal_year_of(v.period_end, start),
                scope=v.scope or config.default_scope,
                rupees=v.rupees,
            )
            for v in extraction.values
        ]
        notices = [
            f"sheet {name!r} names no statement; skipped" for name in extraction.skipped_sheets
        ]
        assumed = sorted({v.sheet for v in extraction.values if v.scope is None})
        if assumed:
            notices.append(
                f"sheets {', '.join(assumed)} do not say standalone or consolidated; used "
                f"defaults.scope = {config.default_scope} from config.toml"
            )
        return Document(rows, [], notices)
    if suffix == ".pdf":
        pdf = extract_pdf(path)
        rows = [
            Row(
                sheet=None,
                cell=None,
                page=v.page,
                bbox=json.dumps(list(v.bbox)),
                statement=v.statement,
                section=v.section,
                label=v.label,
                raw=v.raw,
                unit_text=v.unit,
                rupees_per_unit=int(RUPEES_PER_UNIT[v.unit]),
                period_end=v.period_end,
                fiscal_year=v.fiscal_year,
                scope=v.scope or config.default_scope,
                rupees=v.rupees,
            )
            for v in pdf.values
            if v.statement != "notes"
        ]
        written = write_notes_markdown(path, root / "notes" / company)
        notes = [(note.name[len(path.stem) + 1 : -len(".md")], note) for note in written]
        skipped = sum(1 for v in pdf.values if v.statement == "notes")
        notices = [f"{skipped} note-table values kept in the notes Markdown, not loaded as facts"]
        assumed = sorted({v.page for v in pdf.values if v.scope is None and v.statement != "notes"})
        if assumed:
            notices.append(
                f"pages {', '.join(map(str, assumed))} do not say standalone or consolidated; "
                f"used defaults.scope = {config.default_scope} from config.toml"
            )
        warnings = [
            f"{s.statement} page {s.page} skipped: {s.error.code} {s.error.message}"
            for s in pdf.skipped
        ]
        return Document(rows, notes, notices, warnings)
    raise UnsupportedFormat(f"{path.name}: {suffix or 'no extension'} files are not supported")


def _copy(source: Path, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {source} to {target}: {exc}") from exc


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


RAW_VALUE_COLUMNS = (
    "raw_id, doc_id, sheet, cell, page, bbox, statement, section, raw_label, raw_value_text, "
    "raw_unit, rupees_per_unit, run_id"
)
FACT_COLUMNS = (
    "raw_id, doc_id, account, fiscal_year, period_end, scope, value, mapping_id, confirmed, run_id"
)


def _insert_many(
    con: duckdb.DuckDBPyConnection, table: str, columns: str, rows: list[list[object]]
) -> None:
    """Insert rows with one multi-row INSERT (much faster in DuckDB than one per row)."""
    if not rows:
        return
    marks = "(" + ", ".join("?" for _ in rows[0]) + ")"
    values = ", ".join(marks for _ in rows)
    params = [value for row in rows for value in row]
    con.execute(f"INSERT INTO {table} ({columns}) VALUES {values}", params)


def _load(
    run: Run,
    company: str,
    path: Path,
    sha256: str,
    archive: str,
    document: Document,
    label_map: LabelMap,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Stage, validate and commit one document. Returns (doc_id, version, unmapped labels)."""
    unmapped: dict[tuple[str, str], None] = {}
    rows = document.rows
    with connect(company_db_path(run.root, company), write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            row = con.execute(
                "SELECT coalesce(max(version), 0) FROM documents WHERE sha256 = ?", [sha256]
            ).fetchone()
            version = (row[0] if row else 0) + 1
            latest = max(rows, key=lambda r: r.period_end)
            doc = con.execute(
                "INSERT INTO documents (file_name, sha256, version, fiscal_year, scope, status, "
                "archive_path, run_id, ingested_at) VALUES (?, ?, ?, ?, ?, 'loaded', ?, ?, ?) "
                "RETURNING doc_id",
                [
                    path.name,
                    sha256,
                    version,
                    latest.fiscal_year,
                    ",".join(sorted({r.scope for r in rows})),
                    archive,
                    run.run_id,
                    _now(),
                ],
            ).fetchone()
            assert doc is not None
            doc_id = doc[0]
            raw_ids = [
                raw_id
                for (raw_id,) in con.execute(
                    "SELECT nextval('raw_values_seq') FROM range(?)", [len(rows)]
                ).fetchall()
            ]
            raw_values, facts = [], []
            for raw_id, r in zip(raw_ids, rows, strict=True):
                mapping = find(label_map, r.statement, r.section, r.label)
                if mapping is None:
                    unmapped[(r.statement, r.label)] = None
                raw_values.append(
                    [raw_id, doc_id, r.sheet, r.cell, r.page, r.bbox, r.statement, r.section]
                    + [r.label, r.raw, r.unit_text, r.rupees_per_unit, run.run_id]
                )
                facts.append(
                    [raw_id, doc_id, mapping and mapping.account, r.fiscal_year, r.period_end]
                    + [r.scope, r.rupees, mapping and mapping.mapping_id, mapping is not None]
                    + [run.run_id]
                )
            _insert_many(con, "raw_values", RAW_VALUE_COLUMNS, raw_values)
            _insert_many(con, "facts", FACT_COLUMNS, facts)
            for section, note in document.notes:
                con.execute(
                    "INSERT INTO notes VALUES (?, ?, ?)",
                    [doc_id, section, note.relative_to(run.root).as_posix()],
                )
            con.execute(
                "UPDATE documents SET status = 'superseded', superseded_by = ? "
                "WHERE sha256 = ? AND doc_id <> ? AND status = 'loaded'",
                [doc_id, sha256, doc_id],
            )
            for validate in VALIDATORS:
                validate(run, company, con, doc_id)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return doc_id, version, list(unmapped)


def ingest_file(
    run: Run, config: Config, company: str, path: Path, label_map: LabelMap, *, force: bool
) -> str:
    """Ingest one file and return a one-line outcome. Per-document failures are logged, counted on
    `run` and sent to review; they do not raise."""
    name = f"{company}/{path.name}"
    try:
        sha256 = sha256_of(path)
        with connect(company_db_path(run.root, company)) as con:
            earlier = con.execute(
                "SELECT doc_id FROM documents WHERE sha256 = ? ORDER BY version DESC", [sha256]
            ).fetchone()
        if earlier is not None and not force:
            raise DuplicateFile(
                f"{name}: already ingested as doc_id {earlier[0]} (same sha256); "
                "use --force to load it again as a new version"
            )
        document = read_document(path, run.root, company, config)
        for notice in document.notices:
            run.event("extract", notice, file=name)
        for warning in document.warnings:
            run.event("extract", warning, level="warning", file=name)
        archive = archive_dir(run.root, company) / f"{sha256}{path.suffix.lower()}"
        if not archive.exists():
            _copy(path, archive)
        archive_rel = archive.relative_to(run.root).as_posix()
        doc_id, version, unmapped = _load(
            run, company, path, sha256, archive_rel, document, label_map
        )
    except DuplicateFile as exc:
        run.skipped += 1
        run.event("ingest", exc.message, level="warning", code=exc.code, file=name)
        return f"skipped ({exc})"
    except DocumentFailure as exc:
        run.failed += 1
        run.failure_codes.append(exc.code)
        run.exit_code = EXIT_FAILURES
        run.event("ingest", exc.message, level="error", code=exc.code, file=name)
        _to_review(run, company, path, exc)
        return f"failed ({exc}); copied to review/{company}/"
    for statement, label in unmapped:
        run.event(
            "map",
            f"{statement}: no dictionary mapping for {label!r}; loaded unconfirmed",
            level="warning",
            code=UnmappedLabel.code,
            file=name,
            context={"doc_id": doc_id, "statement": statement, "label": label},
        )
    run.loaded += 1
    run.pending_review += len(unmapped)
    message = (
        f"loaded as doc_id {doc_id} version {version}: {len(document.rows)} facts, "
        f"{len(unmapped)} unmapped labels"
    )
    run.event("ingest", message, file=name, context={"doc_id": doc_id, "archive": archive_rel})
    return message


def _to_review(run: Run, company: str, path: Path, error: DocumentFailure) -> None:
    folder = review_dir(run.root, company)
    if path.is_file():
        _copy(path, folder / path.name)
    reason: dict[str, object] = {
        "file": path.name,
        "code": error.code,
        "message": error.message,
        "run_id": run.run_id,
    }
    if error.details:
        reason["checks"] = list(error.details)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{path.name}.error.json").write_text(
            json.dumps(reason, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write to {folder}: {exc}") from exc
