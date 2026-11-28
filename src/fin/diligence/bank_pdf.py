"""PDF bank statements: the tables of the PDF add-on the user chose, read with a bank profile.

The statement's pages go to the engine in `[pdf] parser` (Docling or LiteParse on this Mac,
LlamaParse in the cloud: README, "Install"), every page, once per file: its tables (grids of
text) and text lines are saved in the data folder as `cache/bank/<company>/<sha256>.json`,
keyed by the company and the file's sha256, with each account number as the company's token
(`fin.diligence.bank_mask`). Ingesting the same file again, or after a failed load, reads that
saved parse and calls the engine no more; every loaded row traces to it
(`bank_statements.parse_path`; `bank_lines` page and row). Delete the file to parse again; a
saved parse holding a token the company has no number for is E818. LlamaParse's key comes only
from the environment
(`fin.extract.llamaparse_run`); a missing key or a failed call raises E206 and nothing is loaded.

`rows_of` reads the saved parse with a bank profile, the same profiles as the Excel statements:
a table whose header row has every column of the profile starts the transactions; a table of the
same width without a header (the next page's) continues them; any other table or text line is a
header or summary line (account, period, opening balance). No layout special cases: a layout the
profile does not read is E807 (its title and header lines listed, never data rows), and a page the
engine could not read, a missing add-on or a parse with no table is E816.
"""

import json
from pathlib import Path
from typing import Any

from fin.audit.errors import (
    BankParseUnreadable,
    BankPdfUnreadable,
    LayoutUnknown,
    SourcePeriodInvalid,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.diligence.bank_mask import hide_numbers, mask_parse, unmask_parse
from fin.diligence.bank_raw import Pages, Raw, RawRow
from fin.extract.addon import Request, page_count
from fin.extract.fallback import cache_dir, engine
from fin.extract.ledger import is_data, parse_date
from fin.extract.profiles import Profile, load_profile, norm, profiles_for
from fin.paths import company_db_path

HEADER_SEARCH_ROWS = 8  # a table's column header is looked for in its first rows
SHOWN_ROWS = 12  # header lines listed when no profile matches


def saved_parse(root: Path, company: str, sha256: str) -> Path:
    return root / "cache" / "bank" / company / f"{sha256}.json"


def read_parse(
    run: Run, config: Config, path: Path, sha256: str, masking: tuple[str, list[Profile]]
) -> tuple[dict[str, Any], str]:
    """(the parse of the PDF with its account numbers, its path relative to the data folder): the
    saved one (tokens turned back into the numbers in memory), else the engine's, saved with the
    numbers the `profiles` find as tokens. E816 (no add-on, a page not read), E206 (the engine
    failed), E818. `masking`: (the company, the bank profiles to find numbers with)."""
    company, profiles = masking
    db = company_db_path(run.root, company)
    saved = saved_parse(run.root, company, sha256)
    rel = saved.relative_to(run.root).as_posix()
    if saved.is_file():
        try:
            data = json.loads(saved.read_text(encoding="utf-8"))
            if not (isinstance(data["pages"], dict) and isinstance(data["engine"], str)):
                raise TypeError("pages and engine are not as expected")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BankParseUnreadable(
                f"{path.name}: the saved parse {rel} cannot be read ({type(exc).__name__}); "
                "delete it to parse the PDF again. Nothing loaded"
            ) from exc
        run.event(
            "ingest", f"{path.name}: saved parse {rel} used, no parser called", file=path.name
        )
        return data | {"pages": unmask_parse(db, data["pages"], f"{path.name}: {rel}")}, rel
    chosen = engine(config.pdf_parser)
    if chosen is None:
        raise BankPdfUnreadable(
            f"{path.name}: a PDF bank statement is read by the PDF add-on and [pdf] parser is "
            "none. Choose one (`fin parser use docling|liteparse|llamaparse`; a local one keeps "
            "the pages on this Mac) and ingest again. Nothing loaded"
        )
    if not chosen.installed() or chosen.unsupported():
        raise BankPdfUnreadable(
            f"{path.name}: the PDF add-on {chosen.LABEL} is not installed or cannot run here "
            "(`uv run fin doctor`, `fin parser use`). Nothing loaded"
        )
    pages = list(range(1, page_count(path) + 1))
    notices: list[str] = []
    found = chosen.convert(Request(path, pages, cache_dir(run.root, chosen.NAME)), notices)
    for notice in notices:
        run.event("ingest", f"{path.name}: {notice}", file=path.name)
    unread = [f"page {n}: {r.error.message}" for n, r in sorted(found.items()) if r.error]
    if unread:
        raise BankPdfUnreadable(
            f"{path.name}: {chosen.LABEL} could not read " + "; ".join(unread) + ". Nothing loaded"
        )
    data = {
        "engine": chosen.NAME,
        "sha256": sha256,
        "pages": {
            str(n): {
                "tables": [[[c.text for c in row] for row in t.grid] for t in read.tables],
                "texts": list(read.texts),
            }
            for n, read in sorted(found.items())
        },
    }
    masked = data | {"pages": mask_parse(run, db, data["pages"], profiles)}
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(masked), encoding="utf-8")
    return data, rel


def _header_text(rows: list[list[str]], column: int) -> str:
    """The text of one column over the header rows (a repeated line once)."""
    parts: list[str] = []
    for row in rows:
        text = norm(row[column]) if column < len(row) else ""
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " ".join(parts)


def _header(grid: list[list[str]], profile: Profile) -> tuple[int, dict[int, str]] | None:
    """(first data row, column -> field) where the profile's column header is in the grid."""
    width = max(len(row) for row in grid)
    for top in range(min(len(grid), HEADER_SEARCH_ROWS)):
        if top + profile.header_rows > len(grid):
            break
        found: dict[int, str] = {}
        for column in range(width):
            text = _header_text(grid[top : top + profile.header_rows], column)
            field = profile.columns.get(text)
            if field is not None and field not in found.values():
                found[column] = field
        if len(found) == len(set(profile.columns.values())):
            return top + profile.header_rows, found
    return None


def _table_start(
    grid: list[list[str]], profile: Profile, mapping: dict[int, str] | None, width: int
) -> tuple[int | None, dict[int, str] | None, int]:
    """(the first transaction row, None for a summary table; the column mapping; the width)."""
    header = _header(grid, profile)
    if header is not None:
        return header[0], header[1], max(len(r) for r in grid)
    if mapping is not None and all(len(r) == width for r in grid):
        return 0, mapping, width  # the table goes on from the page before
    return None, mapping, width


def _split_table(
    grid: list[list[str]],
    start: int | None,
    mapping: dict[int, str] | None,
    seen: tuple[int, int],
) -> tuple[list[RawRow], list[str]]:
    """(the transaction rows, the other lines) of one table; `seen` is (page number, rows seen
    on the page before this table)."""
    number, at = seen
    rows: list[RawRow] = []
    lines: list[str] = []
    for i, row in enumerate(grid):
        if start is None or i < start or mapping is None:
            text = " ".join(" ".join(c.split()) for c in row if c.strip())
            lines.extend([text] if text else [])
        else:
            cells = {f: row[c] for c, f in mapping.items() if c < len(row)}
            rows.append(RawRow(f"page {number}", number, at + i + 1, cells))
    return rows, lines


def rows_of(pages: Pages, profile: Profile) -> tuple[list[RawRow], list[str]] | None:
    """(the transaction rows, the header and summary lines) of a parse read with `profile`, or
    None when no table has the profile's columns."""
    rows: list[RawRow] = []
    lines: list[str] = []
    mapping: dict[int, str] | None = None
    width = 0
    for key in sorted(pages, key=int):
        page, number = pages[key], int(key)
        at = 0  # rows seen on the page, over all its tables
        for grid in page["tables"]:
            if not grid:
                continue
            start, mapping, width = _table_start(grid, profile, mapping, width)
            table_rows, table_lines = _split_table(grid, start, mapping, (number, at))
            rows.extend(table_rows)
            lines.extend(table_lines)
            at += len(grid)
        lines.extend(page["texts"])
    return (rows, lines) if mapping is not None else None


def _top_lines(pages: Pages) -> list[str]:
    """Title and header lines to list when no profile matches: each table's rows above its first
    row with a number or a date, and the page text lines."""
    out = []
    for key in sorted(pages, key=int):
        for grid in pages[key]["tables"]:
            for row in grid:
                cells = [" ".join(c.split()) for c in row if c.strip()]
                if any(is_data(c) for c in cells):
                    break
                if cells:
                    out.append(f"page {key}: " + " | ".join(cells))
        out.extend(f"page {key}: {' '.join(t.split())}" for t in pages[key]["texts"][:3])
    return out[:SHOWN_ROWS]


def _period(lines: list[str], profile: Profile) -> tuple[Any, Any] | None:
    for line in lines:
        match = profile.period.search(line)
        if match is not None:
            start, end = (parse_date(match.group(g), profile) for g in ("start", "end"))
            if start is not None and end is not None:
                return start, end
    return None


def read_pdf(
    run: Run, config: Config, path: Path, sha256: str, profile_name: str | None, company: str
) -> Raw:
    """A PDF statement as a `Raw` (account numbers as filed, in memory). See the module
    docstring; E807 when no profile of the kind reads the tables (checked first for a profile
    named), E808 when no line gives the period."""
    candidates = [load_profile(profile_name)] if profile_name else profiles_for("bank")
    for profile in candidates:
        if profile.kind != "bank":
            raise LayoutUnknown(f"column profile {profile.name!r} reads {profile.kind}, not bank")
    data, rel = read_parse(run, config, path, sha256, (company, candidates))
    pages: Pages = data["pages"]
    if not any(p["tables"] for p in pages.values()):
        raise BankPdfUnreadable(
            f"{path.name}: {data['engine']} found no table on any page (saved parse {rel}). "
            "Nothing loaded. The saved parse is used again on every ingest of this file: to read "
            "it with another add-on (`fin parser use`), delete the saved parse first"
        )
    for profile in candidates:
        read = rows_of(pages, profile)
        if read is None:
            continue
        rows, lines = read
        period = _period(lines, profile)
        if period is None:
            raise SourcePeriodInvalid(
                f"{path.name}: no header line gives the period covered (profile {profile.name}, "
                f"pattern {profile.period.pattern!r}; saved parse {rel})"
            )
        return Raw(profile, period[0], period[1], lines, rows, data["engine"], rel)
    names = ", ".join(p.name for p in candidates) or "none"
    shown = hide_numbers(" / ".join(_top_lines(pages))) or "none"
    raise LayoutUnknown(
        f"{path.name}: no bank column profile matches the tables {data['engine']} read (tried: "
        f"{names}). Title and header lines: {shown}. "
        "Write a profile in templates/profiles/ (docs/development.md) and ingest again with "
        "--profile"
    )
