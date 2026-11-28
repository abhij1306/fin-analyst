"""`fin adjust import <co> <file> [--sheet]`: propose one adjustment per row of the user's walk
sheet, as `fin adjust propose` does for one (SPEC-09).

The file is CSV or Excel (.xlsx, .xlsm) with a header row. Columns, named in any case, with spaces
or underscores: period, scope, category, description (each row needs them), revenue effect and
cost effect (signed rupees as --revenue-effect and --cost-effect; a row needs at least one),
account and source reference (optional). A column fin does not know is refused rather than
ignored. Each adjustment's evidence is the file and the row's description cell
(`walk.xlsx Adjustments!G5`), then the row's source reference. The file is archived like a source.

All rows or none: every row is checked before any is added, so a bad row refuses the whole file
(E847) and names the row; nothing is imported. A sheet already imported into the engagement (a
row's evidence is an adjustment that is not rejected) is refused the same way.
"""

import csv
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from fin.audit.errors import AdjustmentImportInvalid, AdjustmentInvalid, DiligenceChangeRefused
from fin.audit.logger import Run
from fin.config import SCOPES
from fin.db.conn import transaction
from fin.diligence import adjustments
from fin.diligence.engagement import company_database, parse_rupees, require_open
from fin.diligence.loading import archive_copy
from fin.ingest_read import sha256_of

SUFFIXES = (".csv", ".xlsx", ".xlsm")
REQUIRED = ("period", "scope", "category", "description")
EFFECTS = ("revenue effect", "cost effect")
COLUMNS = (*REQUIRED, *EFFECTS, "account", "source reference")
# What a workbook that is not readable raises, on opening or while its rows are read.
UNREADABLE = (OSError, KeyError, ValueError, zipfile.BadZipFile, InvalidFileException, ParseError)


@dataclass(frozen=True)
class Row:
    """One row of the walk sheet, parsed."""

    where: str  # the row, the file and its description cell: 'row 5 (walk.xlsx Walk!G5)'
    period: str
    scope: str
    category: str
    description: str
    revenue: Decimal | None
    cost: Decimal | None
    account: str | None
    source: str | None

    @property
    def evidence(self) -> str:
        cell = self.where.partition("(")[2].removesuffix(")")
        return f"{cell}; {self.source}" if self.source else cell


def _refuse(message: str) -> AdjustmentImportInvalid:
    return AdjustmentImportInvalid(f"{message}; nothing imported")


def _grid(path: Path, sheet: str | None) -> tuple[str | None, list[list[Any]]]:
    """(the sheet's name, None for CSV; the rows of cells). E847 when the file cannot be read."""
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise _refuse(f"{path.name}: fin imports adjustments from {', '.join(SUFFIXES)} files")
    if suffix == ".csv":
        if sheet is not None:
            raise _refuse(f"{path.name} is a CSV file: it has no sheets, drop --sheet")
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                return None, [list(cells) for cells in csv.reader(handle)]
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise _refuse(f"cannot read {path.name}: {exc}") from exc
    try:
        book = load_workbook(path, read_only=True, data_only=True)
    except UNREADABLE as exc:
        raise _refuse(f"cannot read {path.name}: {exc}") from exc
    try:
        chosen = sheet or book.sheetnames[0]
        if chosen not in book.sheetnames:
            sheets = ", ".join(book.sheetnames)
            raise _refuse(f"{path.name} has no sheet {sheet!r} (sheets: {sheets})")
        # read-only: the sheet's XML is read while its rows are iterated
        return chosen, [list(cells) for cells in book[chosen].iter_rows(values_only=True)]
    except UNREADABLE as exc:
        raise _refuse(f"cannot read {path.name}: {exc}") from exc
    finally:
        book.close()


def _text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _name(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").lower().split())


def _columns(name: str, head: list[Any]) -> dict[str, int]:
    """Column name -> its index. E847 for a missing, unknown or repeated column."""
    index: dict[str, int] = {}
    for i, cell in enumerate(head):
        column = _name(cell)
        if not column:
            continue
        if column not in COLUMNS:
            raise _refuse(f"{name}: unknown column {cell!r}; the columns are {', '.join(COLUMNS)}")
        if column in index:
            raise _refuse(f"{name}: the column {column!r} is there twice")
        index[column] = i
    missing = [c for c in REQUIRED if c not in index]
    if not any(c in index for c in EFFECTS):
        missing.append("revenue effect or cost effect")
    if missing:
        raise _refuse(f"{name}: no column {', '.join(missing)} in the header row")
    return index


def _cell(cells: list[Any], i: int | None) -> Any:
    """The cell at index i of a row, None when the column is not there or the row is short."""
    return cells[i] if i is not None and i < len(cells) else None


def _row(where: str, cells: dict[str, Any]) -> Row:
    """One row, or E847 naming it."""
    text = {column: _text(value) for column, value in cells.items()}
    for column in REQUIRED:
        if text[column] is None:
            raise _refuse(f"{where}: no {column}")
    if text["scope"] not in SCOPES:
        raise _refuse(f"{where}: scope {text['scope']!r} is not one of {', '.join(SCOPES)}")
    money: dict[str, Decimal | None] = {}
    for column in EFFECTS:
        try:
            money[column] = None if text[column] is None else parse_rupees(str(text[column]))
        except ValueError as exc:
            raise _refuse(f"{where}: {column}: {exc}") from exc
    if all(value is None for value in money.values()):
        raise _refuse(f"{where}: give a revenue effect or a cost effect")
    return Row(
        where,
        str(text["period"]),
        str(text["scope"]),
        str(text["category"]),
        str(text["description"]),
        money["revenue effect"],
        money["cost effect"],
        text["account"],
        text["source reference"],
    )


def read_rows(path: Path, sheet: str | None = None) -> list[Row]:
    """Every row of the walk sheet under its header (empty rows skipped). E847 for a file that
    cannot be read, a column that is missing or unknown, no rows, or a row that is not valid."""
    sheet_name, grid = _grid(path, sheet)
    filled = [(n, cells) for n, cells in enumerate(grid, start=1) if any(map(_text, cells))]
    label = f"{path.name} {sheet_name}" if sheet_name else path.name
    if not filled:
        raise _refuse(f"{label} is empty")
    index = _columns(label, filled[0][1])
    prefix = f"{path.name} {sheet_name}!" if sheet_name else f"{path.name} "
    cell = get_column_letter(index["description"] + 1)
    rows = [
        _row(f"row {n} ({prefix}{cell}{n})", {c: _cell(cells, index.get(c)) for c in COLUMNS})
        for n, cells in filled[1:]
    ]
    if not rows:
        raise _refuse(f"{label} has a header row and no adjustments under it")
    return rows


def _checked(row: Row, step: Any) -> Any:
    """Run a check of the row; an E804 or E805 becomes E847 naming the row."""
    try:
        return step()
    except (AdjustmentInvalid, DiligenceChangeRefused) as exc:
        raise _refuse(f"{row.where}: {exc.code} {exc.message}").with_context(
            row=row.where, code=exc.code
        ) from exc


def _refuse_repeat(con: Any, engagement: dict[str, Any], rows: list[Row]) -> None:
    """E847 when a row's evidence is already an adjustment of the engagement that is not rejected:
    the same sheet imported twice would count every adjustment twice."""
    by_evidence = {row.evidence: row for row in rows}
    placeholders = ", ".join("?" for _ in by_evidence)
    earlier = con.execute(
        "SELECT adj_id, source_ref FROM adjustments WHERE engagement_id = ? "
        f"AND status <> 'rejected' AND source_ref IN ({placeholders}) ORDER BY adj_id",
        [engagement["engagement_id"], *by_evidence],
    ).fetchone()
    if earlier is not None:
        row = by_evidence[earlier[1]]
        raise _refuse(
            f"{row.where}: adjustment #{earlier[0]} already has this evidence; this sheet was "
            "imported before (reject those adjustments, or import a changed sheet under a new name)"
        ).with_context(row=row.where, adj_id=earlier[0])


def import_file(
    run: Run, company: str, path: Path, sheet: str | None = None
) -> list[dict[str, Any]]:
    """Propose an adjustment for every row of the file, in one transaction, after checking every
    row (`adjustments.check_in`). E847 naming the first bad row, or a row already imported; E803
    without an open engagement. Returns the adjustments proposed."""
    rows = read_rows(path, sheet)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        _refuse_repeat(con, engagement, rows)
        planned = []
        for row in rows:
            effects = _checked(
                row, lambda r=row: adjustments.resolve_effects(None, r.revenue, r.cost)
            )
            fields = {"period": row.period, "category": row.category, "account": row.account}
            evidence = adjustments.Evidence(source_ref=row.evidence)
            _checked(
                row,
                lambda e=effects, f=fields, v=evidence: adjustments.check_in(
                    run, con, company, engagement, e, evidence=v, **f
                ),
            )
            planned.append((row, effects, fields, evidence))
        archived = archive_copy(run, company, path, sha256_of(path))
        added = [
            adjustments.propose_in(
                run,
                con,
                company,
                engagement,
                effects,
                scope=row.scope,
                description=row.description,
                evidence=evidence,
                **fields,
            )
            for row, effects, fields, evidence in planned
        ]
    run.event(
        "adjust import",
        f"{company}: {len(added)} adjustments proposed from {path.name} (archived as {archived})",
        context={"company": company, "file": path.name, "archive": archived, "sheet": sheet},
    )
    return added
