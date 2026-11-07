"""Load documents into a company database (PLAN.md Phase 3).

For each file, `ingest_file`:

1. Hashes it. A file whose sha256 is already loaded is skipped with E105, unless `force`, which
   loads it again as a new version and marks the earlier one superseded.
2. Extracts its values: Excel (`fin.extract.excel`) or PDF (`fin.extract.pdf`; notes and the
   auditor's report also go to `notes/<company>/` as Markdown).
3. Copies the original to `archive/<company>/<sha256>.<ext>`.
4. In ONE transaction: inserts the document, its raw values and their facts in rupees, marks
   earlier versions of the same file superseded, runs the checks (`fin.findings.check_document`:
   the validation gates), then commits. Any error rolls the whole document back.

A failed check is a finding, not a failure: the document is committed but HELD (status 'held',
left out of current figures and metrics) until the user accepts its findings or a fix clears them
(`fin.findings`). A label the dictionary does not map still loads, as a fact with no account and
confirmed = false, and is logged with E301. Only a file with nothing readable fails (E1xx/E2xx): it
is copied to `review/<company>/` with a `.error.json` file giving the code and reason; the next
file goes on.

With a reading (`--reading`, `fin.extract.reading`): figures Claude read from the document's pages
or cells are loaded for each statement and scope fin's own extraction did not read (all of them when
fin could not read the file at all), through the same stage -> validate -> commit. Their facts have
`read_by = 'claude'` and point at the original document's page or cell; they stay unverified until
`fin verify` finds an independent reading that agrees.
"""

import hashlib
import json
import re
import shutil
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
    ReadingInvalid,
    UnmappedLabel,
    UnsupportedFormat,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect
from fin.extract.excel import extract_workbook
from fin.extract.pdf import extract_pdf
from fin.extract.periods import fiscal_year_of, period_of, resolve_months
from fin.extract.reading import ReadingLine, read_reading
from fin.extract.text import write_notes_markdown
from fin.extract.units import RUPEES_PER_UNIT
from fin.findings import check_document, supersede
from fin.map.dictionary import LabelMap, map_rows
from fin.paths import archive_dir, company_db_path, review_dir

COMPANY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EXCEL_SUFFIXES = (".xlsx", ".xlsm")


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
    period: str  # "FY25", "FY26-Q1", "FY26-H1" (fin.extract.periods)
    period_months: int
    scope: str
    rupees: Decimal
    read_by: str = "fin"  # or "claude": from a reading file


@dataclass(frozen=True)
class Document:
    rows: list[Row]
    notes: list[tuple[str, Path]]  # (section, markdown file)
    notices: list[str]  # things worth logging, e.g. an assumed scope
    warnings: list[str] = field(default_factory=list)  # e.g. a statement page that was skipped


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
        months = {
            (v.sheet, v.cell): resolve_months(
                v.statement, v.period_end, v.months, start, where=f"{path.name} sheet {v.sheet!r}"
            )
            for v in extraction.values
        }
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
                period=period_of(
                    v.period_end, months[(v.sheet, v.cell)], start, where=f"{path.name} {v.cell}"
                ),
                period_months=months[(v.sheet, v.cell)],
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
                period=v.period,
                period_months=v.months,
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


def _reading_row(line: ReadingLine) -> Row:
    return Row(
        sheet=line.sheet,
        cell=line.cell,
        page=line.page,
        bbox=None,
        statement=line.statement,
        section=line.section,
        label=line.label,
        raw=line.raw,
        unit_text=line.unit_text,
        rupees_per_unit=int(RUPEES_PER_UNIT[line.unit]),
        period_end=line.period_end,
        fiscal_year=line.fiscal_year,
        period=line.period,
        period_months=line.period_months,
        scope=line.scope,
        rupees=line.rupees,
        read_by="claude",
    )


def read_with_reading(
    run: Run, path: Path, company: str, config: Config, reading: Path
) -> Document:
    """fin's own extraction, plus the reading's lines for every statement and scope it did not
    read. When fin cannot read the file at all (E1xx/E2xx), every statement comes from the
    reading. E205 when the reading has nothing fin did not read."""
    name = f"{company}/{path.name}"
    lines = read_reading(reading, config.fiscal_year_start_month)
    try:
        document = read_document(path, run.root, company, config)
    except DocumentFailure as exc:
        run.event(
            "extract",
            f"fin could not read the file ({exc}); every statement comes from reading "
            f"{reading.name}",
            level="warning",
            code=exc.code,
            file=name,
        )
        document = Document([], [], [])
    read = {(r.statement, r.scope) for r in document.rows}
    used = [line for line in lines if (line.statement, line.scope) not in read]
    if not used:
        raise ReadingInvalid(
            f"{reading.name}: fin read every statement and scope the reading has; nothing to load "
            f"from it (compare them with `fin verify {company} <file> --reading <reading>`)"
        )
    notices = list(document.notices)
    parts = sorted({f"{line.scope} {line.statement}" for line in used})
    notices.append(
        f"{len(used)} figures read by Claude from reading {reading.name} ({', '.join(parts)}); "
        "unverified until `fin verify` with a second, independent reading agrees"
    )
    if len(used) < len(lines):
        notices.append(
            f"{len(lines) - len(used)} reading lines not loaded: fin read those statements itself"
        )
    rows = document.rows + [_reading_row(line) for line in used]
    return Document(rows, document.notes, notices, document.warnings)


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
    "raw_id, doc_id, account, fiscal_year, period, period_months, period_end, scope, value, "
    "mapping_id, confirmed, run_id, read_by"
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
    reading: tuple[str, str] | None = None,
) -> tuple[int, int, list[tuple[str, str]], int]:
    """Stage, check and commit one document. Returns (doc_id, version, unmapped labels, open
    findings); with open findings the document is held."""
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
                "INSERT INTO documents (file_name, sha256, version, fiscal_year, period_end, "
                "scope, status, archive_path, run_id, ingested_at, reading_sha256, reading_path) "
                "VALUES (?, ?, ?, ?, ?, ?, 'loaded', ?, ?, ?, ?, ?) RETURNING doc_id",
                [
                    path.name,
                    sha256,
                    version,
                    latest.fiscal_year,
                    latest.period_end,
                    ",".join(sorted({r.scope for r in rows})),
                    archive,
                    run.run_id,
                    _now(),
                    *(reading or (None, None)),
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
            keys = ((r.statement, r.scope, r.section, r.label) for r in rows)
            for raw_id, r, mapping in zip(raw_ids, rows, map_rows(label_map, keys), strict=True):
                if mapping is None:
                    unmapped[(r.statement, r.label)] = None
                # A per-share figure (EPS) is stored as printed: the statement unit is not its unit.
                value = r.rupees / r.rupees_per_unit if mapping and mapping.per_share else r.rupees
                raw_values.append(
                    [raw_id, doc_id, r.sheet, r.cell, r.page, r.bbox, r.statement, r.section]
                    + [r.label, r.raw, r.unit_text, r.rupees_per_unit, run.run_id]
                )
                facts.append(
                    [raw_id, doc_id, mapping and mapping.account, r.fiscal_year, r.period]
                    + [r.period_months, r.period_end]
                    + [r.scope, value, mapping and mapping.mapping_id, mapping is not None]
                    + [run.run_id, r.read_by]
                )
            _insert_many(con, "raw_values", RAW_VALUE_COLUMNS, raw_values)
            _insert_many(con, "facts", FACT_COLUMNS, facts)
            for section, note in document.notes:
                con.execute(
                    "INSERT INTO notes VALUES (?, ?, ?)",
                    [doc_id, section, note.relative_to(run.root).as_posix()],
                )
            supersede(run, company, con, doc_id, sha256)
            open_findings = check_document(run, company, con, doc_id)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return doc_id, version, list(unmapped), open_findings


def ingest_file(
    run: Run,
    config: Config,
    company: str,
    path: Path,
    label_map: LabelMap,
    *,
    force: bool,
    reading: Path | None = None,
) -> str:
    """Ingest one file (with a reading: see the module docstring) and return a one-line outcome.
    Per-document failures are logged, counted on `run` and sent to review; they do not raise. A
    document with findings is held (counted in `run.held`), not failed."""
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
        archived_reading = None
        if reading is None:
            document = read_document(path, run.root, company, config)
        else:
            document = read_with_reading(run, path, company, config, reading)
            reading_sha = sha256_of(reading)
            reading_archive = archive_dir(run.root, company) / f"{reading_sha}.reading.csv"
            if not reading_archive.exists():
                _copy(reading, reading_archive)
            archived_reading = (reading_sha, reading_archive.relative_to(run.root).as_posix())
        for notice in document.notices:
            run.event("extract", notice, file=name)
        for warning in document.warnings:
            run.event("extract", warning, level="warning", file=name)
        archive = archive_dir(run.root, company) / f"{sha256}{path.suffix.lower()}"
        if not archive.exists():
            _copy(path, archive)
        archive_rel = archive.relative_to(run.root).as_posix()
        doc_id, version, unmapped, open_findings = _load(
            run, company, path, sha256, archive_rel, document, label_map, archived_reading
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
        _to_review(run, company, path, exc, reading)
        return (
            f"failed ({exc}); copied to review/{company}/, reason in "
            f"review/{company}/{path.name}.error.json"
        )
    for statement, label in unmapped:
        run.event(
            "map",
            f"{statement}: no dictionary mapping for {label!r}; loaded unconfirmed",
            level="warning",
            code=UnmappedLabel.code,
            file=name,
            context={"doc_id": doc_id, "statement": statement, "label": label},
        )
    run.pending_review += len(unmapped)
    claude = sum(1 for r in document.rows if r.read_by == "claude")
    details = (
        f"doc_id {doc_id} version {version}: {len(document.rows)} facts, "
        f"{len(unmapped)} unmapped labels"
        + (f", {claude} read by Claude (unverified)" if claude else "")
    )
    if open_findings:
        run.held += 1
        message = (
            f"held as {details}, {open_findings} findings; not in current figures until they "
            f"are accepted or fixed (fin findings list {company})"
        )
    else:
        run.loaded += 1
        message = f"loaded as {details}"
    run.event("ingest", message, file=name, context={"doc_id": doc_id, "archive": archive_rel})
    return message


def _to_review(
    run: Run, company: str, path: Path, error: DocumentFailure, reading: Path | None = None
) -> None:
    folder = review_dir(run.root, company)
    copy = folder / path.name
    if path.is_file() and path.resolve() != copy.resolve():  # not when ingesting the review/ copy
        _copy(path, copy)
    reason: dict[str, object] = {
        "file": path.name,
        "code": error.code,
        "message": error.message,
        "run_id": run.run_id,
    }
    if reading is not None:
        reason["reading"] = str(reading)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{path.name}.error.json").write_text(
            json.dumps(reason, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write to {folder}: {exc}") from exc
