"""Read one document into the rows fin loads (step 2 of `fin.ingest`): Excel, PDF (with the PDF
add-on's engine for the pages pdfplumber skipped) or, with a reading, the figures Claude read.
Nothing here writes to a database; `fin.ingest` stages, checks and commits the rows.

A statement sheet or page with no unit line holds the document with the question "which unit?"
(E203 with `question = "unit"` in its context): the user's answer (`GivenUnit`, `fin ingest
--unit`) is used for those sheets and pages only, and their rows say who gave it. A given unit no
sheet or page needed is refused (E207)."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from fin.actor import CLAUDE, USER
from fin.audit.errors import (
    DocumentFailure,
    FileUnreadable,
    ReadingInvalid,
    UnitNotDetected,
    UnitNotUsed,
    UnsupportedFormat,
    ValueOutOfRange,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.extract import fallback
from fin.extract.excel import ExcelValue, extract_workbook
from fin.extract.pdf_model import PdfValue
from fin.extract.periods import fiscal_year_of, period_of, resolve_months
from fin.extract.reading import ReadingLine, read_reading
from fin.extract.text import write_notes_markdown
from fin.extract.units import rupees_per_unit
from fin.paths import notes_dir

EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xls")
MAX_RUPEES = Decimal(10) ** 16  # facts.value is DECIMAL(20, 2); a figure this large is a misread


# The answers to "which unit?" (`fin ingest --unit`, the UI's choice) and the unit each names.
UNIT_ANSWERS = {
    "lakhs": "lakh",
    "crores": "crore",
    "millions": "million",
    "thousands": "thousand",
    "rupees": "rupees",
}


@dataclass(frozen=True)
class GivenUnit:
    """The unit the user gave for a document whose statements name none, and who entered it."""

    unit: str  # lakh, crore, million, thousand, rupees (a value of UNIT_ANSWERS)
    by: str  # the actor (fin.actor): 'user', or 'ai:claude' entering the user's answer

    @property
    def text(self) -> str:
        """`raw_values.raw_unit` for its rows: shown wherever the figures are sourced."""
        who = "user" if self.by == USER else "user, entered by Claude"
        return f"{self.unit} (unit given by {who})"


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
    read_by: str = "fin"  # the PDF add-on's engine ("docling", ...) or "ai:claude" (a reading)


@dataclass(frozen=True)
class Document:
    rows: list[Row]
    notes: list[tuple[str, Path]]  # (section, markdown file)
    notices: list[str]  # things worth logging, e.g. an assumed scope
    warnings: list[str] = field(default_factory=list)  # e.g. a statement page that was skipped
    # Statement pages (and Excel sheets held for values with no label) fin could not read: page or
    # sheet, statement, code, reason (SkippedPage.as_dict, HeldSheet.as_dict).
    unread: list[dict[str, object]] = field(default_factory=list)
    unit_given: GivenUnit | None = None  # set when the user's unit was used for some rows


def files_in(path: Path) -> list[Path]:
    """`path` itself, or the files in folder `path` (sorted, hidden files left out). E101 if it
    does not exist."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileUnreadable(
            f"{path} does not exist; put the company's files in <data folder>/inbox/<company>/ "
            "(lower-case name) or pass a path"
        )
    return sorted(p for p in path.iterdir() if p.is_file() and not p.name.startswith("."))


def sha256_of(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc


def read_document(
    run: Run,
    path: Path,
    company: str,
    config: Config,
    unit: GivenUnit | None = None,
    *,
    ask_unit: bool = True,
) -> Document:
    """Extract the values of an Excel or PDF file, with `unit` for statements that name none
    (E203 question without it, E207 when none needed it). E103 for any other format. With
    `ask_unit` False (a reading fills what fin skips), a PDF page with no unit line stays unread
    instead of holding the document."""
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        document = _read_excel(path, config, unit)
    elif suffix == ".pdf":
        document = _read_pdf(run, path, company, config, unit, ask_unit=ask_unit)
    else:
        raise UnsupportedFormat(f"{path.name}: {suffix or 'no extension'} files are not supported")
    if unit is None:
        return document
    if not any(r.unit_text == unit.text for r in document.rows):
        raise UnitNotUsed(
            f"{path.name}: --unit {unit.unit} was given, but every statement sheet or page names "
            "its own unit; nothing loaded. Ingest it without --unit"
        )
    # dataclasses.replace returns the type it is given (Sonar S5886 reads it as any dataclass)
    return replace(document, unit_given=unit)  # NOSONAR


def _read_excel(path: Path, config: Config, unit: GivenUnit | None) -> Document:
    """The rows of a workbook; the sheets held for values with no label are listed as unread."""
    start = config.fiscal_year_start_month
    extraction = extract_workbook(path, unit.unit if unit else None)
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
            unit_text=unit.text if unit and v.unit_given else v.unit_text,
            sheet=v.sheet,
            cell=v.cell,
        )
        for v in extraction.values
    ]
    notices = [f"sheet {name!r} names no statement; skipped" for name in extraction.skipped_sheets]
    assumed = sorted({v.sheet for v in extraction.values if v.scope is None})
    if assumed:
        notices.append(
            f"sheets {', '.join(assumed)} do not say standalone or consolidated; used "
            f"defaults.scope = {config.default_scope} from config.toml"
        )
    warnings = [f"{h.error.message} (E204)" for h in extraction.held]
    return Document(rows, [], notices, warnings, [h.as_dict() for h in extraction.held])


def _which_unit(path: Path, pages: list[dict[str, object]]) -> UnitNotDetected | None:
    """The question "which unit?" for the statement pages with no unit line (E203), or None."""
    numbers = [p["page"] for p in pages if p["code"] == UnitNotDetected.code]
    if not numbers:
        return None
    return UnitNotDetected(
        f"{path.name}: which unit? no unit line such as '₹ in Lakhs' on statement pages "
        f"{', '.join(map(str, numbers))}"
    ).with_context(question="unit", pages=numbers)


def _read_pdf(
    run: Run, path: Path, company: str, config: Config, unit: GivenUnit | None, *, ask_unit: bool
) -> Document:
    """pdfplumber's values, then the PDF add-on's for the statement pages pdfplumber skipped (when
    one is chosen, `fin.extract.fallback`); a scanned results PDF is read by the add-on alone. Fails
    only when neither reads a statement page. Without `unit`, a statement page with no unit line
    holds the whole document with the question "which unit?" (unless not `ask_unit`)."""
    name = f"{company}/{path.name}"
    try:
        read = fallback.extract(run, path, name, config, unit.unit if unit else None)
    except DocumentFailure as exc:
        question = _which_unit(path, list(exc.pages)) if ask_unit and not unit else None
        if question is not None:
            raise question from exc
        raise
    unread = [s.as_dict() for s in read.unread]
    question = _which_unit(path, unread) if ask_unit and not unit else None
    if question is not None:
        raise question
    given = unit.text if unit else ""
    rows = [
        _row(
            v,
            config.fiscal_year_start_month,
            months=v.months,
            period=v.period,
            scope=v.scope or config.default_scope,
            unit_text=given if v.unit_given else v.unit,
            page=v.page,
            bbox=json.dumps(list(v.bbox)),
            read_by=read_by,
        )
        for values, read_by in ((read.by_fin, "fin"), (read.by_addon, read.reader))
        for v in values
        if v.statement != "notes"
    ]
    notes, notices = _notes(run, path, company, read)
    values = read.by_fin + read.by_addon
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
    return Document(rows, notes, notices, warnings, unread)


def _notes(
    run: Run, path: Path, company: str, read: fallback.PdfRead
) -> tuple[list[tuple[str, Path]], list[str]]:
    """The notes Markdown of a text PDF (none for a scanned one) and the notice about it."""
    notices: list[str] = []  # the add-on's own are logged as it runs (`fallback._log`)
    notes: list[tuple[str, Path]] = []
    if not read.scanned:
        # one folder per file content: two different PDFs with the same name keep their own notes
        written = write_notes_markdown(path, notes_dir(run.root, company) / sha256_of(path)[:12])
        notes = [(note.name[len(path.stem) + 1 : -len(".md")], note) for note in written]
        in_notes = sum(1 for v in read.by_fin if v.statement == "notes")
        notices.append(
            f"{in_notes} note-table values kept in the notes Markdown, not loaded as facts"
        )
    return notes, notices


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
        read_by=CLAUDE,
    )


def read_with_reading(
    run: Run,
    path: Path,
    company: str,
    config: Config,
    reading: Path,
    unit: GivenUnit | None = None,
) -> Document:
    """fin's own extraction, plus the reading's lines for every statement and scope it did not
    read. When fin cannot read the file at all (E1xx/E2xx), every statement comes from the
    reading. E205 when the reading has nothing fin did not read."""
    name = f"{company}/{path.name}"
    lines = read_reading(reading, config.fiscal_year_start_month)
    try:
        document = read_document(run, path, company, config, unit, ask_unit=False)
    except UnitNotUsed:
        raise
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
        "check them against another reading with `fin verify` whenever wanted"
    )
    if len(used) < len(lines):
        notices.append(
            f"{len(lines) - len(used)} reading lines not loaded: fin read those statements itself"
        )
    rows = document.rows + [_reading_row(line, config.fiscal_year_start_month) for line in used]
    return Document(
        rows, document.notes, notices, document.warnings, document.unread, document.unit_given
    )


def check_range(document: Document, name: str) -> None:
    """E890 when a figure is 10**16 rupees or more (it cannot be stored); names where it is."""
    for r in document.rows:
        if abs(r.rupees) >= MAX_RUPEES:
            where = f"{r.sheet}!{r.cell}" if r.sheet else f"page {r.page}"
            raise ValueOutOfRange(
                f"{name}: {r.label!r} at {where} is {r.raw!r} {r.unit_text}, too large to store "
                f"(Rs {r.rupees:,} or more than 10^16); a wrong unit or a misread cell. Nothing "
                "loaded"
            )
