"""GSTR-2B (SPEC-20): `fin ingest <co> <file> --kind gst --profile gstr2b`.

The portal's Excel download is read like the other GST files (`fin.extract.ledger.read_table`, a
`gst` profile with `gst_return = "gstr2b"`): its B2B and B2B-CDNR sheets, one row per supplier
document, under a two-row header (templates/profiles/gstr2b.toml; the layout and its sources:
docs/decisions/forensic.md). Loaded by `fin.diligence.sources` in one transaction and stored as
'gstr2b' (`gstr2b_lines`, db/schema/fdd_gst.sql). The supplier's trade or legal name is masked as
kind `vendor` on load, so it shares the purchase register's token; the GSTIN is not stored. A credit
note's values are stored negative; a blank invoice value is stored as unknown, a blank tax head as
0. A row with no supplier name, document number or taxable value, a date that cannot be read or
falls outside the file's period, a filing date that cannot be read, or the same supplier and
document twice is E996 and nothing loads.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import Gstr2bLineInvalid
from fin.audit.logger import Run
from fin.diligence.loading import insert_rows
from fin.diligence.mask import Masker
from fin.extract.ledger import Table, amount, parse_date
from fin.extract.profiles import norm

AMOUNTS = ("invoice_value", "taxable_value", "igst", "cgst", "sgst", "cess")
COLUMNS = (
    'file_id, sheet, "row", period, vendor, invoice_no, document_type, invoice_date, '
    + ", ".join(AMOUNTS)
    + ", return_period, filing_date, itc_available, run_id"
)


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _bad(where: str, why: str) -> Gstr2bLineInvalid:
    return Gstr2bLineInvalid(f"{where}: {why}; nothing loaded").with_context(row=where)


def _signed(value: Any, sign: Decimal, profile: Any, where: str) -> Decimal | None:
    """An amount with the document's sign; None when the cell is blank."""
    if not _text(value):
        return None
    return sign * abs(amount(value, profile, where))


def rows(table: Table, file: dict[str, Any], run_id: str, masker: Masker) -> list[list[Any]]:
    """The `gstr2b_lines` values (COLUMNS) of the table's rows. E996 (see the module)."""
    profile, name = table.profile, file["file_name"]
    seen: dict[tuple[str, str], str] = {}
    out = []
    for r in table.rows:
        where = f"{name} {r.sheet}!{r.row}"
        vendor, number = _text(r.cells.get("vendor")), _text(r.cells.get("invoice_no"))
        if norm(vendor) in profile.skip or norm(number) in profile.skip:
            continue
        if not vendor or not number:
            raise _bad(where, "a GSTR-2B line needs the supplier's name and the document number")
        day = parse_date(r.cells.get("invoice_date"), profile)
        if day is None or not file["period_start"] <= day <= file["period_end"]:
            raise _bad(
                where,
                f"document date {r.cells.get('invoice_date')!r} is not a date in the file's "
                f"period {file['period']} ({file['period_start']} to {file['period_end']})",
            )
        kind = _text(r.cells.get("document_type")) or "Regular"
        key = (norm(vendor), f"{norm(kind)} {norm(number)}")
        if key in seen:
            raise _bad(where, f"the same supplier and document as {seen[key]}")
        seen[key] = f"{r.sheet}!{r.row}"
        sign = Decimal(-1) if norm(kind) == "credit note" else Decimal(1)
        value, taxable, *taxes = [_signed(r.cells.get(f), sign, profile, where) for f in AMOUNTS]
        if taxable is None:
            raise _bad(where, "a GSTR-2B line needs its taxable value")
        # a blank invoice value stays unknown; a blank tax head is a tax that does not apply
        values = [value, taxable, *(t if t is not None else Decimal("0.00") for t in taxes)]
        filed = r.cells.get("filing_date")
        filed_on = parse_date(filed, profile) if _text(filed) else None
        if _text(filed) and filed_on is None:
            raise _bad(where, f"filing date {filed!r} is not a date")
        out.append(
            [
                file["file_id"],
                r.sheet,
                r.row,
                file["period"],
                masker.token("vendor", vendor),
                number,
                kind,
                day,
                *values,
                _text(r.cells.get("return_period")) or None,
                filed_on,
                _text(r.cells.get("itc_available")) or None,
                run_id,
            ]
        )
    if not out:
        raise Gstr2bLineInvalid(
            f"{name}: no supplier documents below the column header; nothing loaded"
        )
    return out


def load(
    run: Run, con: duckdb.DuckDBPyConnection, table: Table, file: dict[str, Any], masker: Masker
) -> list[str]:
    """Insert the file's supplier documents; the lines to show (counts only, no names)."""
    found = rows(table, file, run.run_id, masker)
    insert_rows(con, "gstr2b_lines", COLUMNS, found)
    notes = sum(1 for row in found if row[9] < 0)
    suppliers = len({row[4] for row in found})
    return [
        f"GSTR-2B {file['period']}: {len(found)} supplier documents ({notes} credit notes) from "
        f"{suppliers} suppliers"
    ]
