"""GST returns (docs/fdd.md F6): `fin ingest <co> <file> --kind gst --profile gstr1|gstr3b`.

The file is read like a trial balance (`fin.extract.ledger.read_table`, a `gst` profile) and loaded
by `fin.diligence.sources` in one transaction, stored as its return (`source_files.kind` 'gstr1' or
'gstr3b'), so a later file of the same return and period supersedes the earlier one. Each row is one
return month (`gst_returns`, db/schema/fdd_gst.sql) with its sheet and row. A month that cannot be
read, falls outside the file's period or appears twice is E950 and nothing loads; months of the
period with no row are listed, not refused (a return not filed is for a query).
"""

from datetime import date, datetime
from typing import Any

import duckdb

from fin.audit.errors import GstReturnInvalid
from fin.audit.logger import Run
from fin.diligence.loading import insert_rows
from fin.extract.ledger import Table, amount
from fin.extract.profiles import GST_RETURNS, Profile, norm

RETURNS = GST_RETURNS  # the source_files kinds a gst file is stored as
AMOUNTS = ("taxable_value", "igst", "cgst", "sgst", "cess")
EXTRA_3B = ("itc", "rcm_liability", "paid_cash", "paid_credit")
COLUMNS = (
    'file_id, sheet, "row", return_type, period, month, '
    + ", ".join((*AMOUNTS, *EXTRA_3B))
    + ", run_id"
)


def stored_kind(kind: str, content: Any) -> str:
    """The source_files kind of a file loaded with `--kind`: a gst file is stored as its return."""
    return content.profile.gst_return if kind == "gst" else kind


def month_of(value: Any, profile: Profile, where: str) -> date:
    """The first day of the month a cell names (an Excel date, or text in the profile's
    month_format). E950 otherwise."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), profile.month_format).date().replace(day=1)
        except ValueError:
            pass
    raise GstReturnInvalid(
        f"{where}: {value!r} is not a month ({profile.month_format}, profile {profile.name}); "
        "nothing loaded"
    )


def months_between(start: date, end: date) -> list[date]:
    """The first day of every month from start's to end's."""
    out, day = [], start.replace(day=1)
    while day <= end:
        out.append(day)
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return out


def rows(table: Table, file: dict[str, Any], run_id: str) -> list[list[Any]]:
    """The `gst_returns` values (COLUMNS) of the table's month rows. E950 (see the module)."""
    profile, name = table.profile, file["file_name"]
    first, last = file["period_start"].replace(day=1), file["period_end"]
    is_3b = profile.gst_return == "gstr3b"
    seen: dict[date, int] = {}
    out = []
    for r in table.rows:
        cell = r.cells.get("month")
        if isinstance(cell, str) and norm(cell) in profile.skip:
            continue
        where = f"{name} {r.sheet}!{r.row}"
        month = month_of(cell, profile, where)
        if not first <= month <= last:
            raise GstReturnInvalid(
                f"{where}: {month:%b-%Y} is outside the file's period {file['period']} "
                f"({file['period_start']} to {last}); nothing loaded"
            )
        if month in seen:
            raise GstReturnInvalid(
                f"{where}: {month:%b-%Y} is also on row {seen[month]}; nothing loaded"
            )
        seen[month] = r.row
        values = [amount(r.cells.get(f), profile, where) for f in AMOUNTS]
        extra = [amount(r.cells.get(f), profile, where) if is_3b else None for f in EXTRA_3B]
        out.append(
            [
                file["file_id"],
                r.sheet,
                r.row,
                profile.gst_return,
                file["period"],
                month,
                *values,
                *extra,
                run_id,
            ]
        )
    if not out:
        raise GstReturnInvalid(f"{name}: no month rows below the column header; nothing loaded")
    return out


def load(run: Run, con: duckdb.DuckDBPyConnection, table: Table, file: dict[str, Any]) -> list[str]:
    """Insert the return's month rows; the lines to show."""
    found = rows(table, file, run.run_id)
    insert_rows(con, "gst_returns", COLUMNS, found)
    have = {row[5] for row in found}
    missing = [m for m in months_between(file["period_start"], file["period_end"]) if m not in have]
    what = table.profile.gst_return.upper().replace("GSTR", "GSTR-")
    lines = [f"{what} {file['period']}: {len(found)} months"]
    if missing:
        lines.append(
            f"{len(missing)} months of the period have no row (not filed, or not in this file): "
            + ", ".join(f"{m:%Y-%m}" for m in missing)
        )
    return lines
