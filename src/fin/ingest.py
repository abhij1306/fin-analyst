"""Load documents into a company database (docs/product.md).

For each file, `ingest_file`:

1. Hashes it. A file whose sha256 is already loaded is skipped with E105, unless `force`, which
   loads it again as a new version and marks the earlier one superseded.
2. Extracts its values: Excel (`fin.extract.excel`) or PDF (`fin.extract.pdf`; notes and the
   auditor's report also go to `notes/<company>/<sha256[:12]>/` as Markdown). With a PDF add-on
   chosen
   (`[pdf] parser`: Docling, LiteParse or LlamaParse; `fin.extract.fallback`), the statement pages
   pdfplumber skipped go to it, and a scanned results PDF is read by its OCR; those facts have
   `read_by` = the engine's name (`fin verify` compares them with a Claude reading on request).
   Without one, the run logs which pages were not read and how to read them.
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

A file whose statements name no unit is not loaded but held with the question "which unit?"
(E203, counted in `run.held`; its `.error.json` has `"question": "which unit?"`). Ingesting it
again with the user's answer (`unit`, `fin ingest --unit`) stores the unit and who gave it on
the document. When statement pages were not read and no PDF add-on is chosen, the outcome offers
the add-ons (`fin.extract.fallback.offer`).

With a reading (`--reading`, `fin.extract.reading`): figures Claude read from the document's pages
or cells are loaded for each statement and scope neither fin nor the PDF add-on read (all of them
when the file could not be read at all), through the same stage -> validate -> commit. Their facts
have `read_by = 'ai:claude'` and point at the original document's page or cell. Whether they are
trusted is the user's decision; `fin verify` compares the document with another reading whenever
asked.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb

from fin.actor import CLAUDE
from fin.audit.errors import (
    EXIT_FAILURES,
    DataFolderNotWritable,
    DocumentFailure,
    DuplicateFile,
    LoadRejected,
    ScannedPdf,
    UnitNotDetected,
    UnmappedLabel,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect, transaction, utcnow
from fin.extract.addon import ADDON_LABELS
from fin.extract.diagnostics import failure_context
from fin.extract.fallback import offer
from fin.findings import check_document, supersede
from fin.ingest_read import (
    UNIT_ANSWERS,
    Document,
    GivenUnit,
    Row,
    check_range,
    files_in,
    read_document,
    read_with_reading,
    sha256_of,
)
from fin.map.dictionary import LabelMap, copy_review_accounts, map_rows
from fin.paths import archive_dir, company_db_path, review_dir

__all__ = ["UNIT_ANSWERS", "GivenUnit", "files_in", "ingest_file", "read_document", "sha256_of"]


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
        try:
            with transaction(con):
                row = con.execute(
                    "SELECT coalesce(max(version), 0) FROM documents WHERE sha256 = ?", [sha256]
                ).fetchone()
                version = (row[0] if row else 0) + 1
                latest = max(rows, key=lambda r: r.period_end)
                doc = con.execute(
                    "INSERT INTO documents (file_name, sha256, version, fiscal_year, period_end, "
                    "scope, status, archive_path, run_id, ingested_at, reading_sha256, "
                    "reading_path, unread_pages, unit_given, unit_given_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'loaded', ?, ?, ?, ?, ?, ?, ?, ?) "
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
                        document.unit_given and document.unit_given.unit,
                        document.unit_given and document.unit_given.by,
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
                raw_values, facts = _staged(run, doc_id, raw_ids, rows, label_map, unmapped)
                _insert_many(con, "raw_values", RAW_VALUE_COLUMNS, raw_values)
                _insert_many(con, "facts", FACT_COLUMNS, facts)
                copy_review_accounts(run.root, con)
                for section, note in document.notes:
                    con.execute(
                        "INSERT INTO notes VALUES (?, ?, ?)",
                        [doc_id, section, note.relative_to(run.root).as_posix()],
                    )
                supersede(run, company, con, doc_id, sha256)
                open_findings = check_document(run, company, con, doc_id)
        except duckdb.Error as exc:
            raise LoadRejected(
                f"{path.name}: the database refused its rows: {' '.join(str(exc).split())}. "
                "Nothing loaded"
            ) from exc
    return doc_id, version, list(unmapped), open_findings


def _staged(
    run: Run,
    doc_id: int,
    raw_ids: list[int],
    rows: list[Row],
    label_map: LabelMap,
    unmapped: dict[tuple[str, str], None],
) -> tuple[list[list[Any]], list[list[Any]]]:
    """The raw_values and facts rows of a document's values; labels with no mapping are added to
    `unmapped`."""
    raw_values, facts = [], []
    keys = ((r.statement, r.scope, r.section, r.label) for r in rows)
    for raw_id, r, mapping in zip(raw_ids, rows, map_rows(label_map, keys), strict=True):
        if mapping is None:
            unmapped[(r.statement, r.label)] = None
        # A per-share figure (EPS) is stored as printed: the statement unit is not its unit.
        value = r.rupees / r.rupees_per_unit if mapping and mapping.per_share else r.rupees
        raw_values.append(
            [
                raw_id,
                doc_id,
                r.sheet,
                r.cell,
                r.page,
                r.bbox,
                r.statement,
                r.section,
                r.label,
                r.raw,
                r.unit_text,
                r.rupees_per_unit,
                run.run_id,
            ]
        )
        facts.append(
            [
                raw_id,
                doc_id,
                mapping and mapping.account,
                r.fiscal_year,
                r.period,
                r.period_months,
                r.period_end,
                r.scope,
                value,
                mapping and mapping.mapping_id,
                mapping is not None,
                run.run_id,
                r.read_by,
            ]
        )
    return raw_values, facts


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
    unit: GivenUnit | None = None,
) -> str:
    """Ingest one file (with a reading, or the unit the user gave: see the module docstring) and
    return a one-line outcome. Per-document failures are logged, counted on `run` and sent to
    review; they do not raise. A document with findings, or waiting for its unit, is held (counted
    in `run.held`), not failed."""
    name = f"{company}/{path.name}"
    document: Document | None = None
    try:
        sha256 = sha256_of(path)
        _refuse_duplicate(run, company, name, sha256, force)
        archived_reading = None
        if reading is None:
            document = read_document(run, path, company, config, unit)
        else:
            document = read_with_reading(run, path, company, config, reading, unit)
            archived_reading = _archive_reading(run, company, reading)
        check_range(document, path.name)
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
    except UnitNotDetected as exc:
        if (exc.context or {}).get("question") != "unit":
            return _failed(run, config, company, path, exc, reading, document)
        return _held_for_unit(run, company, path, exc, reading)
    except DocumentFailure as exc:
        return _failed(run, config, company, path, exc, reading, document)
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
    readers = {CLAUDE: "Claude", **ADDON_LABELS}
    counts = {who: sum(1 for r in document.rows if r.read_by == who) for who in readers}
    details = f"doc_id {doc_id} version {version}: {len(document.rows)} facts, " + (
        f"{len(unmapped)} unmapped labels"
    )
    details += "".join(f", {n} read by {readers[who]}" for who, n in counts.items() if n)
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
    return message + _unread_lines(document.unread, config)


def _failed(
    run: Run,
    config: Config,
    company: str,
    path: Path,
    exc: DocumentFailure,
    reading: Path | None,
    document: Document | None,
) -> str:
    """Log a failed file, copy it to review/ and return its outcome."""
    name = f"{company}/{path.name}"
    run.failed += 1
    run.failure_codes.append(exc.code)
    run.exit_code = EXIT_FAILURES
    exc.context = failure_context(path, config, exc, reading)
    run.event("ingest", exc.message, level="error", code=exc.code, file=name, context=exc.context)
    unread = list(exc.pages) if document is None else document.unread
    _to_review(run, company, path, exc, reading, unread)
    return (
        f"failed ({exc}); copied to review/{company}/, reason in "
        f"review/{company}/{path.name}.error.json"
        + _unread_lines(unread, config, scanned=isinstance(exc, ScannedPdf))
    )


def _held_for_unit(
    run: Run, company: str, path: Path, exc: UnitNotDetected, reading: Path | None
) -> str:
    """A file whose statements name no unit: nothing loaded, held with the question "which unit?"
    for the user (the file and its .error.json in review/)."""
    run.held += 1
    answers = "|".join(UNIT_ANSWERS)
    run.event(
        "ingest",
        f"held: {exc.message}; ask the user and ingest again with --unit {answers}",
        level="warning",
        code=exc.code,
        file=f"{company}/{path.name}",
        context=exc.context,
    )
    _to_review(run, company, path, exc, reading)
    return (
        f"held: {exc.message}. Nothing loaded: fin does not guess a unit. Ask the user, then "
        f"`fin ingest {company} <file> --unit {answers}`"
    )


def _unread_lines(unread: list[dict[str, object]], config: Config, *, scanned: bool = False) -> str:
    """The statement pages (and Excel sheets) fin could not read, one per line, for the screen;
    then the add-on offer when PDF pages were not read and no add-on is chosen."""
    lines = "".join(
        f"\n  not read: {u['statement']} {_where(u)}: {u['code']} {u['reason']}" for u in unread
    )
    head = f"; {len(unread)} statement page(s) or sheet(s) not read:" if unread else ""
    pdf_pages = scanned or any(u.get("page") is not None for u in unread)
    if pdf_pages and config.pdf_parser == "none":
        lines += f"\n  {offer(scanned)}"
    return head + lines


def _where(unread: dict[str, object]) -> str:
    sheet = unread.get("sheet")
    return f"sheet {sheet!r}" if sheet is not None else f"page {unread['page']}"


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
    if error.context:
        reason["context"] = error.context
    if (error.context or {}).get("question") == "unit":
        reason["question"] = "which unit?"
        reason["answers"] = list(UNIT_ANSWERS)
    if unread:
        reason["unread_pages"] = unread  # statement pages fin could not read: offer a reading
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{path.name}.error.json").write_text(
            json.dumps(reason, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write to {folder}: {exc}") from exc
