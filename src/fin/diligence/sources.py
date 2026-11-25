"""`fin ingest <co> <file> --kind trial_balance|ledger_vouchers|registers|payroll`: load a file.

For one file, `ingest_source`:

1. Needs an open engagement (E803), refuses a file already loaded (same sha256, E105, even when
   superseded) and reads it with a column profile (`fin.extract.ledger`; E807 when no profile
   matches, its header lines listed with known masked names replaced).
2. Checks the period its header lines give is an annual period of the open engagement, or exactly
   its stub (basis unaudited: unaudited management accounts) (E808). A payroll snapshot has one
   date, which must fall in an annual period or the stub (`fin.diligence.payroll`).
   Ledger Vouchers need their period's trial balance loaded first (E811): it says which ledgers
   are parties. A registers workbook has no period header: its period comes from its invoice dates
   (`fin.diligence.registers`), and a later registers file replaces the earlier whatever its period.
3. Copies the original to `archive/<company>/<sha256>.<ext>`.
4. In ONE transaction: adds the `source_files` row, the rows (`fin.diligence.tally`) with sheet and
   row, names masked (`fin.diligence.mask`), the trial balance's group findings, and marks an
   earlier file of the same kind and period superseded (and its findings). A trial balance adds its
   new ledgers to `ledger_accounts` as pending (`fin.diligence.ledgers`). Any error rolls it back.

It returns what was loaded: the ledgers present, and for Ledger Vouchers the trial balance ledgers
missing from it (ask the user for the export with all ledgers) and its ledgers not in the trial
balance (masked: fin cannot tell whether they are parties).
"""

import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import (
    DataFolderNotWritable,
    DuplicateFile,
    SourcePeriodInvalid,
    TrialBalanceNeeded,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect, transaction, utcnow
from fin.diligence import ledgers, payroll, registers, tally
from fin.diligence.engagement import require_open
from fin.diligence.mask import Masker
from fin.diligence.stub import UNAUDITED
from fin.extract.ledger import Table, read_table
from fin.extract.periods import fiscal_year_of
from fin.extract.registers import read_registers
from fin.findings import record, set_status
from fin.ingest import sha256_of
from fin.paths import archive_dir, company_db_path

CHUNK = 500  # rows per INSERT


@dataclass(frozen=True)
class Loaded:
    file_id: int
    period: str
    lines: list[str]  # what to show, first line the summary


def insert_rows(
    con: duckdb.DuckDBPyConnection, table: str, columns: str, rows: list[list[Any]]
) -> None:
    """Insert rows with multi-row INSERTs of CHUNK rows."""
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        marks = ", ".join("(" + ", ".join("?" for _ in chunk[0]) + ")" for _ in chunk)
        con.execute(
            f"INSERT INTO {table} ({columns}) VALUES {marks}", [v for row in chunk for v in row]
        )


def period_of_file(
    table: Table, engagement: dict[str, Any], start_month: int, name: str
) -> tuple[str, str]:
    """(the engagement period the export covers, its basis): an annual period in scope ('audited')
    or the stub ('unaudited'). E808 unless it is one full fiscal year in scope or exactly the
    stub."""
    periods = engagement["periods"].split(",")
    start, end = table.start, table.end
    stub = engagement.get("stub")
    if stub and (start, end) == (engagement["stub_start"], engagement["stub_end"]):
        return stub, "unaudited"
    full = (
        start.day == 1
        and start.month == start_month
        and (end + timedelta(days=1)) == date(start.year + 1, start.month, 1)
    )
    period = fiscal_year_of(end, start_month) if full else None
    if period in periods:
        return period, "audited"
    what = f"the fiscal year {period}" if period else "not a full fiscal year"
    scope = ", ".join(periods)
    if stub:
        scope += (
            f"; stub {stub}, {engagement['stub_start'].isoformat()} to "
            f"{engagement['stub_end'].isoformat()}"
        )
    raise SourcePeriodInvalid(
        f"{name}: its header gives {start.isoformat()} to {end.isoformat()} ({what}), which is "
        f"not a period of the open engagement ({scope}). Nothing loaded"
    )


def archive_copy(run: Run, company: str, path: Path, sha256: str) -> str:
    target = archive_dir(run.root, company) / f"{sha256}{path.suffix.lower()}"
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {path} to {target}: {exc}") from exc
    return target.relative_to(run.root).as_posix()


def refuse_duplicate(con: duckdb.DuckDBPyConnection, name: str, sha256: str) -> None:
    """E105 when a file with this sha256 is already a source file (even a superseded one)."""
    earlier = con.execute(
        "SELECT file_id FROM source_files WHERE sha256 = ? ORDER BY file_id DESC", [sha256]
    ).fetchone()
    if earlier is not None:
        raise DuplicateFile(f"{name}: already loaded as source file_id {earlier[0]} (same sha256)")


def add_file(con: duckdb.DuckDBPyConnection, file: dict[str, Any]) -> int:
    """Insert the `source_files` row (the columns given by `file`); its file_id."""
    row = con.execute(
        f"INSERT INTO source_files ({', '.join(file)}) VALUES ({', '.join('?' for _ in file)}) "
        "RETURNING file_id",
        list(file.values()),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _tb_ledgers(con: duckdb.DuckDBPyConnection, engagement_id: int, period: str) -> list[str]:
    """The ledgers (as stored) of the loaded trial balance of the period, in row order."""
    return [
        ledger
        for (ledger,) in con.execute(
            "SELECT t.ledger FROM trial_balance t JOIN source_files s ON s.file_id = t.file_id "
            "WHERE s.status = 'loaded' AND s.kind = 'trial_balance' AND s.engagement_id = ? "
            "AND s.period = ? AND t.row_type = 'ledger' ORDER BY t.sheet, t.row",
            [engagement_id, period],
        ).fetchall()
    ]


def _load_tb(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    table: Table,
    file: dict[str, Any],
    masker: Masker,
) -> list[str]:
    """Insert the trial balance rows and its group findings; the lines to show."""
    file_id, name, period = file["file_id"], file["file_name"], file["period"]
    rows = tally.trial_balance_rows(table, name)
    names = tally.stored_names(rows, table.profile, masker)

    def stored(index: int | None) -> str | None:
        return names[index] if index is not None else None

    out = []
    for i, r in enumerate(rows):
        if r.row_type == "group":
            group, parent, ledger = names[i], stored(r.group), None
        else:
            up = rows[r.group].group if r.group is not None else None
            group, parent, ledger = stored(r.group), stored(up), names[i]
        out.append(
            [file_id, r.sheet, r.row, period, r.row_type, group, parent, ledger]
            + [r.debit, r.credit, run.run_id]
        )
    insert_rows(
        con,
        "trial_balance",
        'file_id, sheet, "row", period, row_type, group_name, parent_group, ledger, '
        "closing_debit, closing_credit, run_id",
        out,
    )
    findings = tally.group_sum_findings(rows, names, name, period)
    where = "file_id = ? AND check_name = 'tb_group_sum'"
    record(run, company, con, findings, where, [file_id], file_id)
    ledgers = sum(1 for r in rows if r.row_type == "ledger")
    lines = [
        f"trial balance {period}: {ledgers} ledgers in {len(rows) - ledgers} groups, "
        f"{masker.added} names newly masked, {len(findings)} findings"
    ]
    if findings:
        lines.append(
            f"{len(findings)} groups are not the sum of the ledgers under them (rows kept as "
            f"filed; findings, not failures): `fin findings list {company}`"
        )
    return lines


def _load_lv(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    table: Table,
    file: dict[str, Any],
    tb: list[str],
    masker: Masker,
) -> list[str]:
    """Insert the Ledger Vouchers lines; the lines to show, with the trial balance's ledgers
    (`tb`, as stored) missing from it and its ledgers not in the trial balance."""
    file_id, name, period = file["file_id"], file["file_name"], file["period"]
    entries, blocks = tally.ledger_entries(table, name)
    in_tb = set(tb)
    stored_as: dict[str, str] = {}
    outside: list[str] = []
    for entry in [*blocks, *entries]:
        if entry.ledger in stored_as:
            continue
        # A known name is its token; another ledger is stored as the trial balance stores it
        # (known names inside it replaced). One not in the trial balance cannot be classified,
        # so it is masked.
        token = masker.known(entry.ledger)
        shown = masker.text(entry.ledger) or entry.ledger
        if token is None and shown not in in_tb:
            token = masker.token("ledger", entry.ledger)
            outside.append(token)
        stored_as[entry.ledger] = token or shown
    masked = set(table.profile.mask_fields)

    def text(field: str, value: str) -> str | None:
        return (masker.text(value) if field in masked else value) or None

    insert_rows(
        con,
        "ledger_entries",
        'file_id, sheet, "row", ledger, entry_date, particulars, voucher_type, voucher_no, '
        "debit, credit, run_id",
        [
            [file_id, e.sheet, e.row, stored_as[e.ledger], e.date]
            + [text("particulars", e.particulars), text("voucher_type", e.voucher_type)]
            + [text("voucher_no", e.voucher_no), e.debit, e.credit, run.run_id]
            for e in entries
        ],
    )
    insert_rows(
        con,
        "ledger_blocks",
        'file_id, sheet, "row", ledger',
        [[file_id, b.sheet, b.row, stored_as[b.ledger]] for b in blocks],
    )
    present = {stored_as[b.ledger] for b in blocks}
    missing = [ledger for ledger in tb if ledger not in present]
    lines = [
        f"ledger vouchers {period}: {len(entries)} lines in {len(present)} ledgers, "
        f"{masker.added} names newly masked"
    ]
    if outside:
        lines.append(
            f"{len(outside)} ledgers not in the {period} trial balance, masked because fin cannot "
            f"tell whether they are parties: {', '.join(outside)}"
        )
    if missing:
        lines.append(
            f"{len(missing)} trial balance ledgers are missing from it (ask for the Ledger "
            f"Vouchers export with all ledgers): {', '.join(missing)}"
        )
    else:
        lines.append(f"every {period} trial balance ledger is in it")
    return lines


def _load_payroll(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    table: Table,
    file: dict[str, Any],
    masker: Masker,
) -> list[str]:
    """Insert the payroll snapshot rows (names and codes as tokens); the lines to show."""
    insert_rows(
        con, "payroll_lines", payroll.COLUMNS, payroll.rows(table, file, run.run_id, masker)
    )
    return payroll.summary(con, company, file, masker)


def _supersede(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, file: dict[str, Any]
) -> list[int]:
    """Mark the earlier loaded file of the same kind and period superseded, and its findings."""
    earlier = [
        file_id
        for (file_id,) in con.execute(
            "UPDATE source_files SET status = 'superseded', superseded_by = ? WHERE status = "
            "'loaded' AND kind = ? AND (period = ? OR kind = 'registers') AND engagement_id = ? "
            "AND file_id <> ? RETURNING file_id",
            [file["file_id"], file["kind"], file["period"], file["engagement_id"], file["file_id"]],
        ).fetchall()
    ]
    if earlier:
        ids = con.execute(
            "SELECT finding_id FROM findings WHERE list_contains(?::INTEGER[], file_id) "
            "AND status IN ('open', 'accepted')",
            [earlier],
        ).fetchall()
        set_status(run, company, con, [i for (i,) in ids], "superseded")
    return earlier


def ingest_source(
    run: Run, config: Config, company: str, path: Path, kind: str, profile: str | None
) -> Loaded:
    """Load one source file (see the module docstring). Raises E101-E105, E204, E803, E807-E811."""
    name = path.name
    sha256 = sha256_of(path)
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        engagement = require_open(con, company)
        refuse_duplicate(con, name, sha256)
        masker = Masker(con, run.run_id)
        month = config.fiscal_year_start_month

        def hide(text: str) -> str:
            return masker.text(text) or text

        if kind == "registers":
            reg_table = read_registers(path, profile, hide)
            parsed = registers.parse(reg_table, name)
            period, basis, start, end = registers.file_period(parsed, engagement, month)
            profile_name = reg_table.profile.name
        else:
            table = read_table(path, kind, profile, hide=hide)
            if kind == "payroll":
                period, basis = payroll.snapshot_period(table, engagement, month, name)
            else:
                period, basis = period_of_file(table, engagement, month, name)
            start, end, profile_name = table.start, table.end, table.profile.name
        tb = _tb_ledgers(con, engagement["engagement_id"], period)
        if kind == "ledger_vouchers" and not tb:
            raise TrialBalanceNeeded(
                f"{name}: load the {period} trial balance first (`fin ingest {company} <file> "
                "--kind trial_balance`): it says which ledgers are parties, whose names are "
                "masked. Nothing loaded"
            )
        archive = archive_copy(run, company, path, sha256)
        file = {
            "engagement_id": engagement["engagement_id"],
            "kind": kind,
            "file_name": name,
            "sha256": sha256,
            "archive_path": archive,
            "period": period,
            "period_start": start,
            "period_end": end,
            "basis": basis,
            "profile": profile_name,
            "status": "loaded",
            "run_id": run.run_id,
            "ingested_at": utcnow(),
        }
        file["file_id"] = masker.file_id = add_file(con, file)
        if kind == "trial_balance":
            lines = _load_tb(run, company, con, table, file, masker)
            added = ledgers.sync(con, run.run_id)
            if added:
                lines.append(
                    f"{added} new ledgers waiting for an account (`fin map ledgers {company}`)"
                )
        elif kind == "payroll":
            lines = _load_payroll(run, company, con, table, file, masker)
        elif kind == "registers":
            lines = registers.load(run, company, con, parsed, file, masker, engagement, month)
        else:
            lines = _load_lv(run, con, table, file, tb, masker)
        replaced = _supersede(run, company, con, file)
    head = f"loaded as source file_id {file['file_id']} ({kind} {period}, profile {profile_name})"
    if basis == "unaudited":
        head += f"; {period} is the stub: {UNAUDITED}"
    if replaced:
        head += f"; supersedes file_id {', '.join(map(str, replaced))}"
    run.loaded += 1
    run.event(
        "ingest",
        "; ".join([head, *lines]),
        file=f"{company}/{name}",
        context={"file_id": file["file_id"], "kind": kind, "period": period, "archive": archive},
    )
    return Loaded(file["file_id"], period, [head, *lines])
