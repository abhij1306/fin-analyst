"""`fin ingest <co> <file> --kind trial_balance|ledger_vouchers`: load a diligence source file.

For one file, `ingest_source`:

1. Hashes it (a file already loaded is refused, E105) and reads it with a column profile
   (`fin.extract.ledger`; E807 when no profile matches).
2. Checks the period its header lines give is an annual period of the open engagement (E803 with no
   open engagement, E808 otherwise). Ledger Vouchers need their period's trial balance loaded first
   (E811): it says which ledgers are parties.
3. Copies the original to `archive/<company>/<sha256>.<ext>`.
4. In ONE transaction: adds the `source_files` row, the rows (`fin.diligence.tally`) with sheet and
   row, names masked (`fin.diligence.mask`), the trial balance's group findings, and marks an
   earlier file of the same kind and period superseded (and its findings). Any error rolls it back.

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
from fin.diligence import tally
from fin.diligence.engagement import require_open
from fin.diligence.mask import Masker
from fin.extract.ledger import Table, read_table
from fin.extract.periods import fiscal_year_of
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


def period_of_file(table: Table, periods: list[str], start_month: int, name: str) -> str:
    """The engagement period the export covers. E808 unless it is one full fiscal year in scope."""
    start, end = table.start, table.end
    full = (
        start.day == 1
        and start.month == start_month
        and (end + timedelta(days=1)) == date(start.year + 1, start.month, 1)
    )
    period = fiscal_year_of(end, start_month) if full else None
    if period in periods:
        return period
    what = f"the fiscal year {period}" if period else "not a full fiscal year"
    raise SourcePeriodInvalid(
        f"{name}: its header gives {start.isoformat()} to {end.isoformat()} ({what}), which is "
        f"not a period of the open engagement ({', '.join(periods)}). Nothing loaded"
    )


def _archive(run: Run, company: str, path: Path, sha256: str) -> str:
    target = archive_dir(run.root, company) / f"{sha256}{path.suffix.lower()}"
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {path} to {target}: {exc}") from exc
    return target.relative_to(run.root).as_posix()


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


def _name(rows: list[tally.TbRow], index: int | None) -> str | None:
    return rows[index].name if index is not None else None


def _load_tb(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, table: Table, file: dict[str, Any]
) -> list[str]:
    """Insert the trial balance rows and its group findings; the lines to show."""
    file_id, name, period = file["file_id"], file["file_name"], file["period"]
    rows = tally.trial_balance_rows(table, name)
    masker = Masker(con, run.run_id, file_id)
    stored = []
    for i, r in enumerate(rows):
        up = rows[r.group].group if r.group is not None else None
        if r.row_type == "group":
            names = [r.name, _name(rows, r.group), None]
        else:
            names = [
                _name(rows, r.group),
                _name(rows, up),
                tally.stored_ledger(rows, i, table, masker),
            ]
        stored.append(
            [file_id, r.sheet, r.row, period, r.row_type, *names, r.debit, r.credit, run.run_id]
        )
    insert_rows(
        con,
        "trial_balance",
        'file_id, sheet, "row", period, row_type, group_name, parent_group, ledger, '
        "closing_debit, closing_credit, run_id",
        stored,
    )
    findings = tally.group_sum_findings(rows, name, period)
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
    run: Run, con: duckdb.DuckDBPyConnection, table: Table, file: dict[str, Any], tb: list[str]
) -> list[str]:
    """Insert the Ledger Vouchers lines; the lines to show, with the trial balance's ledgers
    (`tb`, as stored) missing from it and its ledgers not in the trial balance."""
    file_id, name, period = file["file_id"], file["file_name"], file["period"]
    entries = tally.ledger_entries(table, name)
    masker = Masker(con, run.run_id, file_id)
    in_tb = set(tb)
    stored_as: dict[str, str] = {}
    outside: list[str] = []
    for entry in entries:
        if entry.ledger in stored_as:
            continue
        known = masker.known(entry.ledger)
        if known is None and entry.ledger not in in_tb:
            known = masker.token("ledger", entry.ledger)
            outside.append(known)
        stored_as[entry.ledger] = known or entry.ledger
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
    present = set(stored_as.values())
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


def _supersede(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, file: dict[str, Any]
) -> list[int]:
    """Mark the earlier loaded file of the same kind and period superseded, and its findings."""
    earlier = [
        file_id
        for (file_id,) in con.execute(
            "UPDATE source_files SET status = 'superseded', superseded_by = ? WHERE status = "
            "'loaded' AND kind = ? AND period = ? AND engagement_id = ? AND file_id <> ? "
            "RETURNING file_id",
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
    table = read_table(path, kind, profile)
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        engagement = require_open(con, company)
        earlier = con.execute(
            "SELECT file_id FROM source_files WHERE sha256 = ? AND status = 'loaded'", [sha256]
        ).fetchone()
        if earlier is not None:
            raise DuplicateFile(
                f"{name}: already loaded as source file_id {earlier[0]} (same sha256)"
            )
        periods = engagement["periods"].split(",")
        period = period_of_file(table, periods, config.fiscal_year_start_month, name)
        tb = _tb_ledgers(con, engagement["engagement_id"], period)
        if kind == "ledger_vouchers" and not tb:
            raise TrialBalanceNeeded(
                f"{name}: load the {period} trial balance first (`fin ingest {company} <file> "
                "--kind trial_balance`): it says which ledgers are parties, whose names are "
                "masked. Nothing loaded"
            )
        archive = _archive(run, company, path, sha256)
        file = {
            "engagement_id": engagement["engagement_id"],
            "kind": kind,
            "file_name": name,
            "sha256": sha256,
            "archive_path": archive,
            "period": period,
            "period_start": table.start,
            "period_end": table.end,
            "basis": "audited",
            "profile": table.profile.name,
            "status": "loaded",
            "run_id": run.run_id,
            "ingested_at": utcnow(),
        }
        row = con.execute(
            f"INSERT INTO source_files ({', '.join(file)}) VALUES ({', '.join('?' for _ in file)}) "
            "RETURNING file_id",
            list(file.values()),
        ).fetchone()
        assert row is not None
        file["file_id"] = row[0]
        if kind == "trial_balance":
            lines = _load_tb(run, company, con, table, file)
        else:
            lines = _load_lv(run, con, table, file, tb)
        replaced = _supersede(run, company, con, file)
    head = (
        f"loaded as source file_id {file['file_id']} ({kind} {period}, profile "
        f"{table.profile.name})"
    )
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
