"""Payroll snapshot (DILIGENCE_PLAN.md D3): rows, the unit check and G1's payroll line.

`fin ingest <co> <file> --kind payroll` reads the Sparsh snapshot (one row per employee) with a
column profile (`templates/profiles/sparsh_payroll.toml`, `fin.extract.ledger`). This module says
what the rows mean and holds the rest of the payroll contract:

- the snapshot date from the header gives the file's period: the fiscal year of the engagement
  that contains it, or the stub when it falls inside the stub's dates (E808 otherwise);
- employee name and code are stored as tokens (`EMP-0001`, `ECODE-0001`); a row with neither is
  E821, as is an unreadable date; a blank amount is NULL, never 0;
- gross salary and CTC are annual. fin cannot know the unit, so the user confirms it
  (`fin payroll confirm-unit`, table `payroll_units`), and fin warns when the amounts look monthly
  (`looks_monthly`: a median below `MONTHLY_BELOW` rupees a year) at load, in `confirm_unit` and
  in G1;
- gate G1 gains one line per loaded payroll file, "unit confirmed annual by the user" (`checklist`,
  used by `fin.diligence.gates`); an engagement with no payroll file has no such line.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import PayrollFileNotFound, PayrollRowInvalid, SourcePeriodInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.diligence.engagement import company_database, require_open
from fin.diligence.loading import clean_text as _text
from fin.diligence.mask import Masker
from fin.extract.excel import parse_number
from fin.extract.ledger import Table, parse_date
from fin.extract.periods import fiscal_year_of
from fin.extract.profiles import Profile, norm

MONTHLY_BELOW = Decimal(150_000)  # a median gross salary or CTC under this a year looks monthly
UNITS = ("annual", "monthly")
NOT_A_DATE = {"", "-", "na", "n/a", "nil"}  # a resign date cell that means "still employed"

Line = tuple[bool, str]
COLUMNS = (
    'file_id, sheet, "row", snapshot_date, employee_name, employee_code, designation, department, '
    "vertical, sub_vertical, joining_date, resign_date, gross_salary, ctc, run_id"
)


def snapshot_period(
    table: Table, engagement: dict[str, Any], start_month: int, name: str
) -> tuple[str, str]:
    """(engagement period, basis) of the snapshot date: the annual period of the engagement it
    falls in ('audited'), or the stub ('unaudited'). E808 when it is in neither."""
    day = table.end
    periods = engagement["periods"].split(",")
    stub = engagement.get("stub")
    if stub and engagement["stub_start"] <= day <= engagement["stub_end"]:
        return stub, "unaudited"
    period = fiscal_year_of(day, start_month)
    if period in periods:
        return period, "audited"
    scope = ", ".join(periods) + (f"; stub {stub}" if stub else "")
    raise SourcePeriodInvalid(
        f"{name}: its snapshot date {day.isoformat()} is in {period}, which is not a period of the "
        f"open engagement ({scope}). Nothing loaded"
    )


def _day(value: Any, profile: Profile, where: str) -> date | None:
    if value is None or norm(value) in NOT_A_DATE:
        return None
    found = parse_date(value, profile)
    if found is None:
        raise PayrollRowInvalid(
            f"{where}: {str(value).strip()!r} is not a date (profile {profile.name} reads "
            f"{profile.date_format})"
        )
    return found


def _money(value: Any, profile: Profile, where: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    _, number = parse_number(value, where)
    return (number * profile.unit).quantize(Decimal("0.01"))


def rows(table: Table, file: dict[str, Any], run_id: str, masker: Masker) -> list[list[Any]]:
    """The `payroll_lines` values (`COLUMNS`) of the table's rows. E821 for a row with neither
    employee name nor code or with a bad date, E204 for an amount that is not a number."""
    profile, out = table.profile, []
    for r in table.rows:
        name, code = _text(r.cells.get("employee_name")), _text(r.cells.get("employee_code"))
        if (name is not None and norm(name) in profile.skip) or (
            code is not None and norm(code) in profile.skip
        ):
            continue
        where = f"{file['file_name']} {r.sheet}!row {r.row}"
        if name is None or code is None:
            raise PayrollRowInvalid(
                f"{where}: no employee {'name' if name is None else 'code'} on a row with other "
                "cells; fin cannot mask or trace it. Nothing loaded"
            )
        cells = r.cells
        out.append(
            [file["file_id"], r.sheet, r.row, table.end]
            + [masker.token("employee_name", name), masker.token("employee_code", code)]
            + [_text(cells.get(f)) for f in ("designation", "department", "vertical")]
            + [_text(cells.get("sub_vertical"))]
            + [_day(cells.get(f), profile, f"{where} {f}") for f in ("joining_date", "resign_date")]
            + [_money(cells.get(f), profile, f"{where} {f}") for f in ("gross_salary", "ctc")]
            + [run_id]
        )
    return out


@dataclass(frozen=True)
class Monthly:
    looks: bool
    gross: Decimal | None  # median gross salary (positive values), else None
    ctc: Decimal | None


def looks_monthly(con: duckdb.DuckDBPyConnection, file_id: int) -> Monthly:
    """Whether a loaded file's amounts look monthly: the median gross salary or median CTC is under
    `MONTHLY_BELOW` rupees. Computed in SQL over the positive amounts."""
    row = con.execute(
        "SELECT median(gross_salary) FILTER (WHERE gross_salary > 0), "
        "median(ctc) FILTER (WHERE ctc > 0) FROM payroll_lines WHERE file_id = ?",
        [file_id],
    ).fetchone()
    assert row is not None
    gross, ctc = (None if v is None else Decimal(str(v)) for v in row)
    return Monthly(any(v is not None and v < MONTHLY_BELOW for v in (gross, ctc)), gross, ctc)


def _medians(m: Monthly) -> str:
    shown = [f"{label} Rs {v:,.0f}" for label, v in (("gross", m.gross), ("CTC", m.ctc)) if v]
    return "median " + " and ".join(shown) if shown else "no positive amounts"


def summary(
    con: duckdb.DuckDBPyConnection, company: str, file: dict[str, Any], masker: Masker
) -> list[str]:
    """What to show after a payroll load: employees, masking, the unit warning and the question."""
    (count,) = con.execute(
        "SELECT count(*) FROM payroll_lines WHERE file_id = ?", [file["file_id"]]
    ).fetchone() or (0,)
    lines = [
        f"payroll {file['period']}: {count} employees as on {file['period_end'].isoformat()}, "
        f"{masker.added} names newly masked (amounts are read as annual rupees)"
    ]
    m = looks_monthly(con, file["file_id"])
    if m.looks:
        lines.append(
            f"WARNING: the amounts look monthly ({_medians(m)}; under Rs {MONTHLY_BELOW:,.0f})"
        )
    lines.append(
        f"unit not confirmed: ask the user whether gross salary and CTC are annual or monthly, "
        "then "
        f"`fin payroll confirm-unit {company} {file['file_name']} --annual|--monthly`"
    )
    return lines


def confirm_unit(run: Run, company: str, ref: str, unit: str) -> list[str]:
    """Record the user's statement of a payroll file's unit (`ref`: its file name or file_id) in
    one transaction. E803 without an open engagement; E820 for no loaded payroll file named."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        found = con.execute(
            "SELECT file_id, file_name, period, status FROM source_files WHERE kind = 'payroll' "
            "AND engagement_id = ? AND (file_name = ? OR CAST(file_id AS VARCHAR) = ?) "
            "ORDER BY file_id",
            [engagement["engagement_id"], ref, ref],
        ).fetchall()
        loaded = [f for f in found if f[3] == "loaded"]
        if len(loaded) != 1:
            why = (
                f"{len(loaded)} loaded payroll files match, use the file_id"
                if loaded
                else "a later snapshot superseded it"
                if found
                else "no payroll file of that name or id"
            )
            known = ", ".join(
                f"file_id {i} {n}"
                for i, n in con.execute(
                    "SELECT file_id, file_name FROM source_files WHERE kind = 'payroll' AND "
                    "status = 'loaded' AND engagement_id = ? ORDER BY file_id",
                    [engagement["engagement_id"]],
                ).fetchall()
            )
            raise PayrollFileNotFound(
                f"{ref!r}: {why}. Loaded payroll files: {known or 'none'}. Nothing recorded"
            )
        file_id, name, period, _ = loaded[0]
        con.execute("DELETE FROM payroll_units WHERE file_id = ?", [file_id])
        con.execute(
            "INSERT INTO payroll_units (file_id, unit, run_id, confirmed_at) VALUES (?, ?, ?, ?)",
            [file_id, unit, run.run_id, utcnow()],
        )
        m = looks_monthly(con, file_id)
    lines = [f"payroll file_id {file_id} ({name}, {period}): the user confirmed the unit is {unit}"]
    if unit == "annual" and m.looks:
        lines.append(f"note: the amounts look monthly ({_medians(m)}); recorded as the user said")
    if unit == "monthly":
        lines.append(
            "gate G1 fails until an annual snapshot is loaded: fin does not convert the amounts"
        )
    run.event(
        "payroll",
        "; ".join(lines),
        file=f"{company}/{name}",
        context={"file_id": file_id, "unit": unit, "looks_monthly": m.looks},
    )
    return lines


def _files(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[Any]:
    return con.execute(
        "SELECT s.file_id, s.file_name, s.period, u.unit FROM source_files s "
        "LEFT JOIN payroll_units u USING (file_id) WHERE s.kind = 'payroll' AND "
        "s.status = 'loaded' AND s.engagement_id = ? ORDER BY s.file_id",
        [engagement_id],
    ).fetchall()


def checklist(ctx: Any) -> list[Line]:
    """G1's payroll lines (`ctx`: a `fin.diligence.gates.Context`): one per loaded payroll file;
    none when no payroll file is loaded."""
    lines: list[Line] = []
    for file_id, name, period, unit in _files(ctx.con, ctx.engagement["engagement_id"]):
        m = looks_monthly(ctx.con, file_id)
        look = f" (the amounts look monthly: {_medians(m)})" if m.looks else ""
        label = f"payroll {name} ({period})"
        if unit == "annual":
            lines.append((True, f"{label}: unit confirmed annual by the user{look}"))
        elif unit == "monthly":
            lines.append(
                (False, f"{label}: the user says the amounts are monthly; load an annual snapshot")
            )
        else:
            lines.append(
                (
                    False,
                    f"{label}: unit not confirmed{look} (`fin payroll confirm-unit {ctx.company} "
                    f"{name} --annual|--monthly`, when the user says which)",
                )
            )
    return lines


def inputs(ctx: Any) -> dict[str, Any]:
    """G1's payroll input: each loaded payroll file's unit confirmation (the files themselves are
    in the source files input)."""
    return {
        "payroll units": [
            list(row)
            for row in ctx.con.execute(
                "SELECT u.file_id, u.unit FROM payroll_units u JOIN source_files s USING (file_id) "
                "WHERE s.status = 'loaded' AND s.engagement_id = ? ORDER BY u.file_id",
                [ctx.engagement["engagement_id"]],
            ).fetchall()
        ]
    }
