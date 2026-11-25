"""Gate G1 Ingestion's trial balance checks (DILIGENCE_PLAN.md §5), used by `fin.diligence.gates`.

They apply once the engagement has a stub or a loaded diligence source file; an engagement on
statements only says so in one passing line. Then, for every annual period and the stub:

- a loaded trial balance and Ledger Vouchers, and every trial balance ledger in the Ledger Vouchers;
- every trial balance ledger confirmed to an account (`fin map ledgers`, then the user confirms);
- each annual period's trial balance reconciled to its statements on the current data
  (`tb_vs_statements`, `fin.diligence.recon`), with every line at or above the threshold in a
  query;
- the stub is marked unaudited: it has no audited statement, so it is checked against its Ledger
  Vouchers only.

The inputs (source files, ledger mappings, the latest reconciliation per period) go into G1's hash,
so a new file, a mapping change or a new reconciliation reopens it.
"""

from typing import Any

import duckdb

from fin.diligence import recon
from fin.diligence.stub import UNAUDITED

Line = tuple[bool, str]


def _files(con: duckdb.DuckDBPyConnection, engagement_id: int) -> dict[tuple[str, str], int]:
    return {
        (kind, period): file_id
        for file_id, kind, period in con.execute(
            "SELECT file_id, kind, period FROM source_files WHERE engagement_id = ? "
            "AND status = 'loaded'",
            [engagement_id],
        ).fetchall()
    }


def _missing_from_vouchers(con: duckdb.DuckDBPyConnection, tb: int, lv: int) -> list[str]:
    return [
        ledger
        for (ledger,) in con.execute(
            "SELECT ledger FROM trial_balance WHERE file_id = ? AND row_type = 'ledger' "
            "AND ledger NOT IN (SELECT ledger FROM ledger_entries WHERE file_id = ?) "
            'ORDER BY sheet, "row"',
            [tb, lv],
        ).fetchall()
    ]


def _names(items: list[str]) -> str:
    return ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")


def _latest_run(con: duckdb.DuckDBPyConnection, engagement_id: int, period: str) -> Any:
    return con.execute(
        "SELECT recon_id, input_hash FROM recon_runs WHERE engagement_id = ? AND name = ? "
        "AND period = ? ORDER BY recon_id DESC LIMIT 1",
        [engagement_id, recon.NAME, period],
    ).fetchone()


def _period_lines(
    con: duckdb.DuckDBPyConnection, company: str, period: str, files: dict[tuple[str, str], int]
) -> list[Line]:
    tb, lv = files.get(("trial_balance", period)), files.get(("ledger_vouchers", period))
    absent = [
        name
        for name, file_id in (("trial balance", tb), ("Ledger Vouchers", lv))
        if file_id is None
    ]
    if absent:
        return [
            (
                False,
                f"{period}: no loaded {' or '.join(absent)} (`fin ingest {company} <file> "
                "--kind trial_balance|ledger_vouchers`)",
            )
        ]
    assert tb is not None and lv is not None
    missing = _missing_from_vouchers(con, tb, lv)
    if missing:
        return [
            (
                False,
                f"{period}: {len(missing)} trial balance ledgers are not in its Ledger Vouchers "
                f"(ask for the export with all ledgers): {_names(missing)}",
            )
        ]
    return [(True, f"{period}: trial balance and Ledger Vouchers loaded, every ledger in both")]


def _recon_line(ctx: Any, period: str) -> Line:
    con, company = ctx.con, ctx.company
    latest = _latest_run(con, ctx.engagement["engagement_id"], period)
    if latest is None or latest[1] != recon.input_hash(con, ctx.engagement, period):
        state = "has not run" if latest is None else "ran on data that has changed since"
        return (
            False,
            f"{period}: {recon.NAME} {state} (`fin reconcile {company} --period {period}`)",
        )
    open_lines = con.execute(
        "SELECT count(*) FROM recon_items WHERE recon_id = ? AND kind NOT IN ('matched', "
        "'unmapped') AND abs(difference) >= ? AND query_id IS NULL",
        [latest[0], ctx.engagement["query_threshold"]],
    ).fetchone()
    assert open_lines is not None
    if open_lines[0]:
        return (
            False,
            f"{period}: {open_lines[0]} trial balance differences at or above the threshold are "
            f"not in a query (map every ledger, then `fin reconcile {company} --period {period}`)",
        )
    return (
        True,
        f"{period}: trial balance reconciled to the statements; every difference at or above "
        "the threshold is in a query",
    )


def checklist(ctx: Any) -> list[Line]:
    """The trial balance lines of G1's checklist (`ctx`: a `fin.diligence.gates.Context`)."""
    con, engagement = ctx.con, ctx.engagement
    files = _files(con, engagement["engagement_id"])
    stub = engagement.get("stub")
    if not files and not stub:
        return [
            (
                True,
                "no trial balance loaded and no stub: the trial balance checks apply once one is",
            )
        ]
    annual = engagement["periods"].split(",")
    lines: list[Line] = []
    for period in [*annual, *([stub] if stub else [])]:
        lines += _period_lines(con, ctx.company, period, files)
    unmapped = [
        ledger
        for (ledger,) in con.execute(
            "SELECT DISTINCT t.ledger FROM trial_balance t JOIN source_files s USING (file_id) "
            "WHERE s.status = 'loaded' AND s.engagement_id = ? AND t.row_type = 'ledger' "
            "AND t.ledger NOT IN (SELECT ledger FROM ledger_accounts WHERE status = 'confirmed') "
            "ORDER BY t.ledger",
            [engagement["engagement_id"]],
        ).fetchall()
    ]
    if unmapped:
        lines.append(
            (
                False,
                f"{len(unmapped)} trial balance ledgers not mapped to an account (`fin map ledgers "
                f"{ctx.company}`, then the user confirms them): {_names(unmapped)}",
            )
        )
    elif files:
        lines.append((True, "every trial balance ledger is mapped to an account"))
    for period in annual:
        if ("trial_balance", period) in files:
            lines.append(_recon_line(ctx, period))
    if stub:
        lines.append(
            (
                True,
                f"{stub}: {UNAUDITED}; checked against its Ledger Vouchers only (no audited "
                "statement)",
            )
        )
    return lines


def inputs(ctx: Any) -> dict[str, Any]:
    """G1's trial balance inputs: the source files, the ledger mappings and the latest
    reconciliation of each annual period."""
    con, engagement_id = ctx.con, ctx.engagement["engagement_id"]
    runs = []
    for period in ctx.engagement["periods"].split(","):
        latest = _latest_run(con, engagement_id, period)
        if latest is not None:
            items = con.execute(
                "SELECT item_no, kind, account, ledger, difference, query_id FROM recon_items "
                "WHERE recon_id = ? ORDER BY item_no",
                [latest[0]],
            ).fetchall()
            runs.append([period, latest[0], latest[1], [list(item) for item in items]])
    return {
        "source files": [
            list(row)
            for row in con.execute(
                "SELECT file_id, kind, period, sha256, status FROM source_files "
                "WHERE engagement_id = ? ORDER BY file_id",
                [engagement_id],
            ).fetchall()
        ],
        "ledger mappings": [
            list(row)
            for row in con.execute(
                "SELECT ledger, account, status FROM ledger_accounts ORDER BY ledger"
            ).fetchall()
        ],
        "reconciliations": runs,
    }
