"""A data-room file's kind (SPEC-13): from its type and from which column profile's headers it has.

An Excel workbook whose headers fit a column profile (`templates/profiles/`, the test `fin ingest
--kind` uses) has that profile's kind; else one with a sheet a diligence databook has
(`REFERENCE_SHEETS`, matched on sheet names like a profile's headers) is `reference_databook`,
loaded as a reference (`fin reference add`, SPEC-10), never as company data; else one with
statement sheets (the test `fin ingest` uses) is `statements`. A
text PDF fin reads statements from is `statements`. fin does not guess from file names or contents
beyond that: every other file is listed with a reason and one next step (`REASONS`).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl.workbook.workbook import Workbook

from fin.audit.errors import DocumentFailure, FinError, NoTablesFound
from fin.extract.excel import extract_workbook, open_workbook
from fin.extract.ledger import has_header, read_table
from fin.extract.pdf import extract_pdf
from fin.extract.profiles import KINDS, Profile, norm, profiles_for
from fin.extract.registers import fits
from fin.ingest_read import EXCEL_SUFFIXES

ARCHIVE_SUFFIXES = (".zip",)
OTHER_ARCHIVES = (".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz")
# A plan row's kind: a profile's kind, statements, a reference databook, an archive, or unknown.
PLAN_KINDS = (*KINDS, "statements", "reference_databook", "archive", "unknown")
# Sheet names (whole words, any case) that mark an adviser's or banker's diligence databook.
REFERENCE_SHEETS = re.compile(
    r"\b(databook|data book|quality of earnings|qoe|ebitda adjustments|net debt|"
    r"net working capital|nwc)\b"
)

# Why fin cannot use a file -> (the reason shown, the one next step).
REASONS = {
    "link": (
        "a link, not a file: fin does not follow links",
        "Copy the file itself into the folder and scan again",
    ),
    "file_type": (
        "fin reads Excel workbooks (.xlsx, .xlsm, .xls) and PDFs, not this type of file",
        "Ask the company for the Excel export",
    ),
    "unreadable": (
        "fin cannot open the workbook: it is password protected or damaged",
        "Ask the company for an unprotected copy",
    ),
    "no_layout": (
        "its headers fit no column profile and it has no statement sheet",
        "Write a column profile for its headers (docs/development.md) and scan again",
    ),
    "several_kinds": (
        "its headers fit column profiles of more than one kind",
        "Load it with fin ingest --kind <kind> --profile <name>, naming the one it is",
    ),
    "statements_unreadable": (
        "it has statement sheets fin cannot read",
        "Have the assistant read it (fin ingest --reading)",
    ),
    "pdf_no_statement": (
        "fin reads statements from text PDFs and finds none it can read in this one (a ledger, "
        "register or scanned PDF is this case)",
        "Ask the company for the Excel export, or have the assistant read it "
        "(fin ingest --reading)",
    ),
    "archive_type": (
        "fin unpacks .zip archives only",
        "Unpack it into the folder yourself and scan again",
    ),
    "archive_unsafe": (
        "the archive has an entry that would be written outside its folder, a link, or two "
        "entries with the same path; nothing was unpacked",
        "Ask the company for the archive again without that entry",
    ),
    "archive_too_large": (
        "the archive unpacks to more than [dataroom] archive_limit_mb in config.toml, or to more "
        "than 100,000 files, with the archives inside it; nothing was unpacked",
        "Unpack it into the folder yourself, or raise archive_limit_mb, and scan again",
    ),
    "archive_unreadable": (
        "fin cannot read the archive: it is damaged or password protected; nothing was unpacked",
        "Ask the company for the archive again",
    ),
    "archive_depth": (
        "an archive inside more archives than fin unpacks",
        "Unpack it into the folder yourself and scan again",
    ),
}


@dataclass(frozen=True)
class FileKind:
    kind: str  # one of PLAN_KINDS
    profile: str | None = None
    period: str | None = None  # as the file states it; None when it states none
    reason: str | None = None  # a key of REASONS when fin cannot use the file


def unusable(reason: str, kind: str = "unknown") -> FileKind:
    return FileKind(kind, reason=reason)


def kind_of(path: Path, start_month: int) -> FileKind:
    """The kind of the file at `path` (not an archive: see `fin.dataroom.unpack`)."""
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return _excel(path)
    if suffix == ".pdf":
        return _pdf(path, start_month)
    if suffix in OTHER_ARCHIVES:
        return unusable("archive_type", "archive")
    return unusable("file_type")


def _excel(path: Path) -> FileKind:
    try:
        book = open_workbook(path, data_only=True)
    except DocumentFailure:
        return unusable("unreadable")
    matches = _profile_matches(book)
    if len(matches) > 1:
        return unusable("several_kinds")
    if matches:
        (profile,) = matches.values()
        return FileKind(profile.kind, profile.name, _source_period(path, profile))
    if any(REFERENCE_SHEETS.search(norm(title)) for title in book.sheetnames):
        return Kind("reference_databook")
    try:
        values = extract_workbook(path).values
    except NoTablesFound:
        return unusable("no_layout")
    except DocumentFailure:
        return unusable("statements_unreadable", "statements")
    return FileKind("statements", period=_ends(v.period_end for v in values))


def _profile_matches(book: Workbook) -> dict[str, Profile]:
    """kind -> the first profile of the kind (by name) whose headers the workbook has."""
    found: dict[str, Profile] = {}
    for kind in KINDS:
        test = fits if kind == "registers" else has_header
        hit = next((p for p in profiles_for(kind) if test(book, p)), None)
        if hit is not None:
            found[kind] = hit
    return found


def _source_period(path: Path, profile: Profile) -> str | None:
    """The period the export's header states, as read for `fin ingest --kind`; None when it states
    none (a register has no period header) or it cannot be read: the load reports why."""
    if profile.kind == "registers":
        return None
    try:
        table = read_table(path, profile.kind, profile.name)
    except FinError:
        return None
    if table.start == table.end:
        return table.start.isoformat()
    return f"{table.start.isoformat()} to {table.end.isoformat()}"


def _pdf(path: Path, start_month: int) -> FileKind:
    try:
        values = extract_pdf(path, start_month).values
    except DocumentFailure:
        return unusable("pdf_no_statement")
    return FileKind("statements", period=_ends(v.period_end for v in values))


def _ends(dates: Iterable[date]) -> str:
    """The distinct period ends, oldest first: "2024-03-31, 2025-03-31"."""
    return ", ".join(d.isoformat() for d in sorted(set(dates)))
