"""Load documents into a company database (PLAN.md Phase 3).

For each file, `ingest_file`:

1. Hashes it. A file whose sha256 is already loaded is skipped with E105, unless `force`, which
   loads it again as a new version and marks the earlier one superseded.
2. Extracts its values: Excel (`fin.extract.excel`) or PDF (`fin.extract.pdf`; notes and the
   auditor's report also go to `notes/<company>/` as Markdown). With the Docling add-on on
   (`[pdf] docling = true`, `fin.extract.docling_run`), the statement pages pdfplumber skipped go
   to Docling, and a scanned results PDF is read by Docling's OCR; those facts have
   `read_by = 'docling'` and are unverified until `fin verify` agrees. Without it, the run logs
   which pages were not read and how to read them.
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
or cells are loaded for each statement and scope neither fin nor Docling read (all of them when
the file could not be read at all), through the same stage -> validate -> commit. Their facts have
`read_by = 'claude'` and point at the original document's page or cell; they stay unverified until
`fin verify` finds an independent reading that agrees.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from fin.audit.errors import (
    EXIT_FAILURES,
    DataFolderNotWritable,
    DocumentFailure,
    DuplicateFile,
    FileUnreadable,
    NoTablesFound,
    ParseError,
    PeriodsNotDetected,
    ReadingInvalid,
    ScannedPdf,
    UnitNotDetected,
    UnmappedLabel,
    UnsupportedFormat,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect, utcnow
from fin.extract import docling_run
from fin.extract.excel import ExcelValue, extract_workbook
from fin.extract.pdf import PdfValue, SkippedPage, extract_pdf
from fin.extract.periods import fiscal_year_of, period_of, resolve_months
from fin.extract.reading import ReadingLine, read_reading
from fin.extract.text import write_notes_markdown
from fin.extract.units import rupees_per_unit
from fin.findings import check_document, supersede
from fin.map.dictionary import LabelMap, map_rows
from fin.paths import archive_dir, company_db_path, review_dir

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
    read_by: str = "fin"  # "docling" (the PDF add-on) or "claude" (a reading file)


@dataclass(frozen=True)
class Document:
    rows: list[Row]
    notes: list[tuple[str, Path]]  # (section, markdown file)
    notices: list[str]  # things worth logging, e.g. an assumed scope
    warnings: list[str] = field(default_factory=list)  # e.g. a statement page that was skipped
    # Statement pages fin could not read: page, statement, code, reason (SkippedPage.as_dict).
    unread: list[dict[str, object]] = field(default_factory=list)


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


def read_document(run: Run, path: Path, company: str, config: Config) -> Document:
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
            _row(
                v,
                start,
                months=months[(v.sheet, v.cell)],
                period=period_of(
                    v.period_end, months[(v.sheet, v.cell)], start, where=f"{path.name} {v.cell}"
                ),
                scope=v.scope or config.default_scope,
                unit_text=v.unit_text,
                sheet=v.sheet,
                cell=v.cell,
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
        return _read_pdf(run, path, company, config)
    raise UnsupportedFormat(f"{path.name}: {suffix or 'no extension'} files are not supported")


@dataclass(frozen=True)
class _PdfRead:
    by_fin: list[PdfValue]  # pdfplumber's values (notes tables included)
    by_docling: list[PdfValue]
    unread: list[SkippedPage]  # statement pages neither read
    scanned: bool  # no text layer: Docling's OCR read it


def _read_pdf(run: Run, path: Path, company: str, config: Config) -> Document:
    """pdfplumber's values, then Docling's for the statement pages pdfplumber skipped (when the
    add-on is on, `fin.extract.docling_run`); a scanned results PDF is read by Docling alone. Fails
    as before only when neither reads a statement page."""
    read = _extract_pdf(run, path, f"{company}/{path.name}", config)
    rows = [
        _row(
            v,
            config.fiscal_year_start_month,
            months=v.months,
            period=v.period,
            scope=v.scope or config.default_scope,
            unit_text=v.unit,
            page=v.page,
            bbox=json.dumps(list(v.bbox)),
            read_by=read_by,
        )
        for values, read_by in ((read.by_fin, "fin"), (read.by_docling, "docling"))
        for v in values
        if v.statement != "notes"
    ]
    notices: list[str] = []  # Docling's own are logged as it runs (`_log_docling`)
    notes: list[tuple[str, Path]] = []
    if not read.scanned:
        written = write_notes_markdown(path, run.root / "notes" / company)
        notes = [(note.name[len(path.stem) + 1 : -len(".md")], note) for note in written]
        in_notes = sum(1 for v in read.by_fin if v.statement == "notes")
        notices.append(
            f"{in_notes} note-table values kept in the notes Markdown, not loaded as facts"
        )
    values = read.by_fin + read.by_docling
    assumed = sorted({v.page for v in values if v.scope is None and v.statement != "notes"})
    if assumed:
        notices.append(
            f"pages {', '.join(map(str, assumed))} do not say standalone or consolidated; "
            f"used defaults.scope = {config.default_scope} from config.toml"
        )
    warnings = [
        f"{s.statement} page {s.page} skipped: {s.error.code} {s.error.message}"
        for s in read.unread
    ]
    return Document(rows, notes, notices, warnings, [s.as_dict() for s in read.unread])


def _extract_pdf(run: Run, path: Path, name: str, config: Config) -> _PdfRead:
    """pdfplumber first; Docling on what it could not read. When nothing could be read, the
    pdfplumber error is raised again, listing the pages still unread."""
    start = config.fiscal_year_start_month
    try:
        pdf = extract_pdf(path, start)
    except ScannedPdf as exc:
        found = _docling_scanned(run, path, name, config)
        _raise_if_empty(exc, found)
        assert found is not None
        return _PdfRead([], found.values, found.unread, scanned=True)
    except (NoTablesFound, PeriodsNotDetected, UnitNotDetected, ParseError) as exc:
        if not exc.pages:
            raise
        found = _docling_skipped(run, path, name, config, _skipped_pages(exc.pages))
        _raise_if_empty(exc, found)
        assert found is not None
        return _PdfRead([], found.values, found.unread, scanned=False)
    if not pdf.skipped:
        return _PdfRead(pdf.values, [], [], scanned=False)
    found = _docling_skipped(run, path, name, config, pdf.skipped)
    if found is None:
        return _PdfRead(pdf.values, [], pdf.skipped, scanned=False)
    return _PdfRead(pdf.values, found.values, found.unread, scanned=False)


def _raise_if_empty(exc: DocumentFailure, found: docling_run.DoclingResult | None) -> None:
    """Raise `exc` again when Docling was not used or read nothing (with the pages it left)."""
    if found is not None and found.values:
        return
    if found is not None:
        exc.pages = tuple(s.as_dict() for s in found.unread)
    raise exc


_PAGE_ERRORS: dict[str, type[DocumentFailure]] = {
    cls.code: cls for cls in (NoTablesFound, PeriodsNotDetected, UnitNotDetected, ParseError)
}


def _skipped_pages(pages: tuple[dict[str, object], ...]) -> list[SkippedPage]:
    """The unread pages an E2xx error lists, as SkippedPage again."""
    return [
        SkippedPage(
            int(str(p["page"])), str(p["statement"]), _PAGE_ERRORS[str(p["code"])](str(p["reason"]))
        )
        for p in pages
    ]


def _docling_off(run: Run, name: str, config: Config, pages: list[int]) -> bool:
    """True (after one notice naming the unread pages and the two ways to read them) when the
    Docling add-on is not to be used: off in config.toml, or not installed."""
    if config.docling and docling_run.installed():
        return False
    why = (
        "the Docling add-on is not installed"
        if config.docling
        else "the Docling add-on is off ([pdf] docling = false in config.toml)"
    )
    run.event(
        "extract",
        f"pages {', '.join(map(str, pages))} not read, and {why}: install it "
        "(`./install.sh --docling`) or give a Claude reading (`fin ingest --reading`)",
        file=name,
    )
    return True


def _docling_skipped(
    run: Run, path: Path, name: str, config: Config, skipped: list[SkippedPage]
) -> docling_run.DoclingResult | None:
    """Docling's reading of the skipped statement pages, or None when the add-on is not used."""
    if _docling_off(run, name, config, sorted({s.page for s in skipped})):
        return None
    found = docling_run.read_skipped(path, skipped, config.fiscal_year_start_month)
    _log_docling(run, name, found)
    return found


def _docling_scanned(
    run: Run, path: Path, name: str, config: Config
) -> docling_run.DoclingResult | None:
    """Docling's OCR reading of a scanned PDF, or None (add-on not used, or too long: E104)."""
    if not (config.docling and docling_run.installed()):
        return None
    found = docling_run.read_scanned(path, config.fiscal_year_start_month)
    if found is None:
        run.event(
            "extract",
            f"scanned PDF longer than {docling_run.SCANNED_PAGE_LIMIT} pages: not read by Docling",
            file=name,
        )
        return None
    _log_docling(run, name, found)
    return found


def _log_docling(run: Run, name: str, found: docling_run.DoclingResult) -> None:
    """Log what Docling did (also when the file then fails): notices, then warnings."""
    for notice in found.notices:
        run.event("extract", notice, file=name)
    for code, message in found.warnings:
        run.event("extract", message, level="warning", code=code, file=name)


def _row(
    value: ExcelValue | PdfValue | ReadingLine,
    start_month: int,
    *,
    months: int,
    period: str,
    scope: str,
    unit_text: str,
    sheet: str | None = None,
    cell: str | None = None,
    page: int | None = None,
    bbox: str | None = None,
    read_by: str = "fin",
) -> Row:
    """The Row for one extracted value (Excel, PDF or reading); where it was read and what each
    source names differently are passed in."""
    return Row(
        sheet=sheet,
        cell=cell,
        page=page,
        bbox=bbox,
        statement=value.statement,
        section=value.section,
        label=value.label,
        raw=value.raw,
        unit_text=unit_text,
        rupees_per_unit=rupees_per_unit(value.unit),
        period_end=value.period_end,
        fiscal_year=fiscal_year_of(value.period_end, start_month),
        period=period,
        period_months=months,
        scope=scope,
        rupees=value.rupees,
        read_by=read_by,
    )


def _reading_row(line: ReadingLine, start_month: int) -> Row:
    return _row(
        line,
        start_month,
        months=line.period_months,
        period=line.period,
        scope=line.scope,
        unit_text=line.unit_text,
        sheet=line.sheet,
        cell=line.cell,
        page=line.page,
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
        document = read_document(run, path, company, config)
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
    rows = document.rows + [_reading_row(line, config.fiscal_year_start_month) for line in used]
    return Document(rows, document.notes, notices, document.warnings, document.unread)


def _copy(source: Path, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {source} to {target}: {exc}") from exc


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
                "scope, status, archive_path, run_id, ingested_at, reading_sha256, reading_path, "
                "unread_pages) VALUES (?, ?, ?, ?, ?, ?, 'loaded', ?, ?, ?, ?, ?, ?) "
                "RETURNING doc_id",
                [
                    path.name,
                    sha256,
                    version,
                    latest.fiscal_year,
                    latest.period_end,
                    ",".join(sorted({r.scope for r in rows})),
                    archive,
                    run.run_id,
                    utcnow(),
                    *(reading or (None, None)),
                    json.dumps(document.unread) if document.unread else None,
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


def _refuse_duplicate(run: Run, company: str, name: str, sha256: str, force: bool) -> None:
    """Raise DuplicateFile when a file with this sha256 was already ingested for the company,
    unless `force` (load it again as a new version)."""
    with connect(company_db_path(run.root, company)) as con:
        earlier = con.execute(
            "SELECT doc_id FROM documents WHERE sha256 = ? ORDER BY version DESC", [sha256]
        ).fetchone()
    if earlier is not None and not force:
        raise DuplicateFile(
            f"{name}: already ingested as doc_id {earlier[0]} (same sha256); "
            "use --force to load it again as a new version"
        )


def _archive_reading(run: Run, company: str, reading: Path) -> tuple[str, str]:
    """Copy the reading into the company archive (once) and return its sha256 and archive path
    relative to the data folder."""
    reading_sha = sha256_of(reading)
    reading_archive = archive_dir(run.root, company) / f"{reading_sha}.reading.csv"
    if not reading_archive.exists():
        _copy(reading, reading_archive)
    return reading_sha, reading_archive.relative_to(run.root).as_posix()


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
    document: Document | None = None
    try:
        sha256 = sha256_of(path)
        _refuse_duplicate(run, company, name, sha256, force)
        archived_reading = None
        if reading is None:
            document = read_document(run, path, company, config)
        else:
            document = read_with_reading(run, path, company, config, reading)
            archived_reading = _archive_reading(run, company, reading)
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
        unread = list(exc.pages) if document is None else document.unread
        _to_review(run, company, path, exc, reading, unread)
        return (
            f"failed ({exc}); copied to review/{company}/, reason in "
            f"review/{company}/{path.name}.error.json" + _unread_lines(unread)
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
    readers = {"claude": "Claude", "docling": "Docling"}
    counts = {who: sum(1 for r in document.rows if r.read_by == who) for who in readers}
    details = f"doc_id {doc_id} version {version}: {len(document.rows)} facts, " + (
        f"{len(unmapped)} unmapped labels"
    )
    details += "".join(
        f", {n} read by {readers[who]} (unverified)" for who, n in counts.items() if n
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
    return message + _unread_lines(document.unread)


def _unread_lines(unread: list[dict[str, object]]) -> str:
    """The statement pages fin could not read, one per line, for the screen."""
    if not unread:
        return ""
    return f"; {len(unread)} statement page(s) not read:" + "".join(
        f"\n  not read: {u['statement']} page {u['page']}: {u['code']} {u['reason']}"
        for u in unread
    )


def _to_review(
    run: Run,
    company: str,
    path: Path,
    error: DocumentFailure,
    reading: Path | None = None,
    unread: list[dict[str, object]] | None = None,
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
    if unread:
        reason["unread_pages"] = unread  # statement pages fin could not read: offer a reading
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{path.name}.error.json").write_text(
            json.dumps(reason, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write to {folder}: {exc}") from exc
