"""Reconciliations (`fin reconcile`): the built-in `tb_vs_statements` (docs/diligence.md D2).

For each audited (annual) period with a loaded trial balance, its ledger closing balances (debit -
credit) roll up through the confirmed ledger mappings (`fin.diligence.ledgers`) and are compared
with the audited standalone statement figures (`current_facts`), one line per account:

- the statement's detail lines are its figures with no figure below them in the dictionary tree;
  a ledger's balance goes to the nearest of its account and the subtotals above it that is one,
  else to its own account (a left_only line). A detail line no ledger reaches is right_only;
- Tally's trial balance lists the year's income and expense ledgers beside the opening "Profit &
  Loss A/c", so the year's profit from the profit and loss ledgers also rolls into other equity
  (`RETAINED`), as the balance sheet shows it;
- a ledger not confirmed to an account is an `unmapped` line: flagged, never guessed.

A line whose difference (trial balance - statements) is at or above the engagement threshold gets
a DRAFT accountant query (origin 'recon'): fin never approves or sends one. A line that had one in
an earlier run of the same period, with the same account and amounts (and the query not
withdrawn), keeps it. A draft of an earlier run whose line this run no longer has (a ledger mapping
was fixed) is listed as stale for the user to withdraw; fin withdraws nothing. A line below the
threshold is listed only. While any ledger is unmapped no query is drafted: the difference may be
fin's own mapping gap. Differences are queried, never fixed: nothing here changes company data.

Each run is a `recon_runs` row (with the hash of what it compared, `input_hash`, so gate G1 can
tell when it must run again) and its `recon_items` (db/schema/reconciliations.sql).
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconNotPossible
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.diligence.ledgers import STATEMENTS, lineage, side
from fin.diligence.queries import draft_in
from fin.map.dictionary import Account, load_accounts

NAME = "tb_vs_statements"
SCOPE = "standalone"
RETAINED = "other_equity"
SHOWN_ROWS = 5  # trial balance rows named in a line's source; the rest are counted


@dataclass(frozen=True)
class Ledger:
    sheet: str
    row: int
    name: str
    net: Decimal  # closing debit - closing credit
    account: str | None  # its confirmed account


def trial_balance(con: duckdb.DuckDBPyConnection, engagement_id: int, period: str) -> Any:
    """The loaded trial balance of the period: (file_id, file_name), or None."""
    return con.execute(
        "SELECT file_id, file_name FROM source_files WHERE engagement_id = ? AND period = ? "
        "AND kind = 'trial_balance' AND status = 'loaded'",
        [engagement_id, period],
    ).fetchone()


def _ledgers(con: duckdb.DuckDBPyConnection, file_id: int) -> list[Ledger]:
    rows = con.execute(
        'SELECT t.sheet, t."row", t.ledger, t.closing_debit - t.closing_credit, '
        "CASE WHEN m.status = 'confirmed' THEN m.account END FROM trial_balance t "
        "LEFT JOIN ledger_accounts m ON m.ledger = t.ledger "
        "WHERE t.file_id = ? AND t.row_type = 'ledger' ORDER BY t.sheet, t.\"row\"",
        [file_id],
    ).fetchall()
    return [Ledger(*row) for row in rows]


def statement_figures(
    con: duckdb.DuckDBPyConnection, period: str
) -> list[tuple[str, Decimal, str]]:
    """(account, value, source) of the period's standalone statement figures."""
    return con.execute(
        "SELECT c.account, c.value, string_agg(d.file_name || ' ' || coalesce(r.sheet || '!' || "
        "r.cell, 'page ' || CAST(r.page AS VARCHAR), ''), '; ' ORDER BY f.fact_id) "
        "FROM current_facts c JOIN facts f ON list_contains(c.fact_ids, f.fact_id) "
        "LEFT JOIN raw_values r ON r.raw_id = f.raw_id "
        "JOIN documents d ON d.doc_id = c.doc_id WHERE c.period = ? AND c.scope = ? "
        "GROUP BY c.account, c.value ORDER BY c.account",
        [period, SCOPE],
    ).fetchall()


def input_hash(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> Any:
    """sha256 of what tb_vs_statements compares for the period now (the trial balance, its
    ledgers' confirmed accounts, the statement figures, the threshold); None without a trial
    balance."""
    tb = trial_balance(con, engagement["engagement_id"], period)
    if tb is None:
        return None
    value = {
        "file": tb[0],
        "ledgers": [[x.name, x.net, x.account] for x in _ledgers(con, tb[0])],
        "figures": [[a, v] for a, v, _ in statement_figures(con, period)],
        "threshold": engagement["query_threshold"],
    }
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows_text(file_name: str, refs: list[tuple[str, int]], profit_ledgers: int) -> str:
    parts = []
    if refs:
        sheet = refs[0][0]
        shown = ", ".join(str(row) for _, row in refs[:SHOWN_ROWS])
        more = f" and {len(refs) - SHOWN_ROWS} more" if len(refs) > SHOWN_ROWS else ""
        parts.append(f"{file_name} {sheet}!{shown}{more}")
    if profit_ledgers:
        parts.append(f"the year's profit from {profit_ledgers} profit and loss ledgers")
    return "; ".join(parts)


def build_lines(
    accounts: dict[str, Account],
    file_name: str,
    ledgers: list[Ledger],
    figures: list[tuple[str, Decimal, str]],
) -> list[dict[str, Any]]:
    """The reconciliation lines (columns of `recon_items` but recon_id, item_no, query_id)."""
    shown = {
        a: (v, s)
        for a, v, s in figures
        if a in accounts and accounts[a].statement in STATEMENTS and accounts[a].unit == "rupees"
    }
    detail = {a for a in shown if not any(a in lineage(accounts, b)[1:] for b in shown)}
    totals, unmapped = _roll_up(accounts, detail, ledgers)
    order = {name: i for i, name in enumerate(accounts)}
    lines = [
        _line(file_name, account, totals.get(account), shown.get(account))
        for account in sorted(set(totals) | detail, key=lambda a: order.get(a, len(order)))
    ]
    for ledger in unmapped:
        lines.append(
            {
                "kind": "unmapped",
                "account": None,
                "ledger": ledger.name,
                "left_amount": ledger.net,
                "right_amount": None,
                "difference": ledger.net,
                "left_source": f"{file_name} {ledger.sheet}!{ledger.row}",
                "right_source": None,
            }
        )
    return lines


def _roll_up(
    accounts: dict[str, Account], detail: set[str], ledgers: list[Ledger]
) -> tuple[dict[str, list[Any]], list[Ledger]]:
    """({account: [amount, [(sheet, row)], profit ledgers]}, the unmapped ledgers)."""
    totals: dict[str, list[Any]] = {}

    def add(account: str, ledger: Ledger, direct: bool) -> None:
        target = next((a for a in lineage(accounts, account) if a in detail), account)
        line = totals.setdefault(target, [Decimal("0.00"), [], 0])
        line[0] += ledger.net * side(accounts, target)
        if direct:
            line[1].append((ledger.sheet, ledger.row))
        else:
            line[2] += 1

    unmapped = []
    for ledger in ledgers:
        if ledger.account is None or ledger.account not in accounts:
            unmapped.append(ledger)
            continue
        add(ledger.account, ledger, True)
        if accounts[ledger.account].statement == "profit_and_loss" and RETAINED in accounts:
            add(RETAINED, ledger, False)
    return totals, unmapped


def _line(
    file_name: str, account: str, total: list[Any] | None, shown: tuple[Decimal, str] | None
) -> dict[str, Any]:
    left = total[0] if total else None
    right = shown[0] if shown else None
    difference = (left or Decimal("0.00")) - (right or Decimal("0.00"))
    if difference == 0:
        kind = "matched"
    else:
        kind = {(True, True): "difference", (True, False): "left_only"}.get(
            (left is not None, right is not None), "right_only"
        )
    source = _rows_text(file_name, total[1], total[2]) if total else None
    return {
        "kind": kind,
        "account": account,
        "ledger": None,
        "left_amount": left,
        "right_amount": right,
        "difference": difference,
        "left_source": source or None,
        "right_source": shown[1] if shown else None,
    }


def _question(period: str, line: dict[str, Any]) -> str:
    left = (
        f"the trial balance ledgers mapped to {line['account']} total {rupees(line['left_amount'])}"
    )
    if line["kind"] == "right_only":
        left = f"no trial balance ledger maps to {line['account']}"
    right = f"the audited standalone statement shows {rupees(line['right_amount'])}"
    if line["kind"] == "left_only":
        right = "the audited standalone statements show no figure for it"
    return (
        f"{period}: {left}; {right} (difference {rupees(line['difference'])}). "
        "Please explain the difference."
    )


def _earlier_query(
    con: duckdb.DuckDBPyConnection, engagement_id: int, period: str, line: dict[str, Any]
) -> int | None:
    row = con.execute(
        "SELECT i.query_id FROM recon_items i JOIN recon_runs r USING (recon_id) "
        "JOIN accountant_queries q ON q.query_id = i.query_id AND q.status <> 'withdrawn' "
        "WHERE r.engagement_id = ? AND r.name = ? AND r.period = ? AND i.account = ? "
        "AND i.left_amount IS NOT DISTINCT FROM ? AND i.right_amount IS NOT DISTINCT FROM ? "
        "AND i.query_id IS NOT NULL ORDER BY r.recon_id DESC LIMIT 1",
        [engagement_id, NAME, period, line["account"], line["left_amount"], line["right_amount"]],
    ).fetchone()
    return row[0] if row else None


def _reconcile(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    accounts: dict[str, Account],
    period: str,
) -> dict[str, Any]:
    engagement_id, threshold = engagement["engagement_id"], engagement["query_threshold"]
    tb = trial_balance(con, engagement_id, period)
    if tb is None:
        raise ReconNotPossible(
            f"{company} {period}: no loaded trial balance (`fin ingest {company} <file> --kind "
            "trial_balance`); nothing recorded"
        )
    figures = statement_figures(con, period)
    if not figures:
        raise ReconNotPossible(
            f"{company} {period}: no audited standalone statement figures to compare with; "
            "nothing recorded"
        )
    lines = build_lines(accounts, tb[1], _ledgers(con, tb[0]), figures)
    unmapped = [line["ledger"] for line in lines if line["kind"] == "unmapped"]
    drafted, kept = [], []
    for line in lines:
        # a line `fin query raise --items` may group (none while a ledger is unmapped)
        line["needs_query"] = (
            not unmapped
            and line["kind"] not in ("matched", "unmapped")
            and abs(line["difference"]) >= threshold
        )
        line["query_id"] = None
        if unmapped or line["kind"] in ("matched", "unmapped"):
            continue
        if abs(line["difference"]) < threshold:
            continue
        line["query_id"] = _earlier_query(con, engagement_id, period, line)
        if line["query_id"] is not None:
            kept.append(line["query_id"])
            continue
        query = draft_in(
            run,
            company,
            con,
            engagement,
            {
                "origin": "recon",
                "finding_id": None,
                "period": period,
                "scope": SCOPE,
                "question": _question(period, line),
                "amount": line["difference"],
                "sources": "; ".join(s for s in (line["left_source"], line["right_source"]) if s),
            },
        )
        line["query_id"] = query["query_id"]
        drafted.append(query["query_id"])
    # while a ledger is unmapped no line is checked against earlier queries: nothing is stale
    stale = [] if unmapped else _stale_drafts(con, engagement_id, period, drafted + kept)
    new = con.execute(
        "INSERT INTO recon_runs (engagement_id, name, period, scope, left_file_id, threshold, "
        "input_hash, unmapped, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING recon_id",
        [engagement_id, NAME, period, SCOPE, tb[0], threshold]
        + [input_hash(con, engagement, period), len(unmapped), run.run_id, utcnow()],
    ).fetchone()
    assert new is not None
    columns = list(lines[0]) if lines else []
    for number, line in enumerate(lines, start=1):
        con.execute(
            f"INSERT INTO recon_items (recon_id, item_no, {', '.join(columns)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in columns)})",
            [new[0], number, *line.values()],
        )
    for item_no, item_id in con.execute(
        "SELECT item_no, item_id FROM recon_items WHERE recon_id = ?", [new[0]]
    ).fetchall():
        lines[item_no - 1]["item_id"] = item_id
    return {
        "recon_id": new[0],
        "name": NAME,
        "period": period,
        "file_name": tb[1],
        "threshold": threshold,
        "lines": lines,
        "unmapped": unmapped,
        "drafted": drafted,
        "kept": kept,
        "stale": stale,
    }


def _stale_drafts(
    con: duckdb.DuckDBPyConnection, engagement_id: int, period: str, current: list[int]
) -> list[int]:
    """Drafts fin made from an earlier run of the period whose line this run no longer has (a
    mapping was fixed, a figure corrected). fin never withdraws a query itself: they are listed for
    the user to withdraw."""
    rows = con.execute(
        "SELECT DISTINCT q.query_id FROM accountant_queries q "
        "JOIN recon_items i ON i.query_id = q.query_id JOIN recon_runs r USING (recon_id) "
        "WHERE q.engagement_id = ? AND q.origin = 'recon' AND q.status = 'draft' "
        "AND r.name = ? AND r.period = ? ORDER BY q.query_id",
        [engagement_id, NAME, period],
    ).fetchall()
    return [query_id for (query_id,) in rows if query_id not in current]


def tb_vs_statements(run: Run, company: str, period: str | None = None) -> list[dict[str, Any]]:
    """Run tb_vs_statements for the period (default: every annual period with a loaded trial
    balance) in one transaction. E803 without an open engagement; E812 when it cannot run."""
    accounts = load_accounts(run.root)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        periods = engagement["periods"].split(",")
        if period is not None and period not in periods:
            raise ReconNotPossible(
                f"{period} is not an annual period of {company}'s open engagement "
                f"({', '.join(periods)}); the stub has no audited statement (its figures: the "
                "stub_figures view). Nothing recorded"
            )
        chosen = (
            [period]
            if period
            else [p for p in periods if trial_balance(con, engagement["engagement_id"], p)]
        )
        if not chosen:
            raise ReconNotPossible(
                f"{company}: no annual period of the open engagement has a loaded trial balance; "
                "nothing recorded"
            )
        results = [_reconcile(run, company, con, engagement, accounts, p) for p in chosen]
    for result in results:
        run.event(
            "reconcile",
            format_result(result).splitlines()[0],
            context={"company": company, "recon_id": result["recon_id"]},
        )
    return results


def recon_lines(con: duckdb.DuckDBPyConnection, recon_id: int) -> list[dict[str, Any]]:
    return fetch_dicts(
        con, "SELECT * FROM recon_items WHERE recon_id = ? ORDER BY item_no", [recon_id]
    )


def format_result(result: dict[str, Any]) -> str:
    lines, threshold = result["lines"], result["threshold"]
    accounts = [line for line in lines if line["kind"] != "unmapped"]
    matched = sum(1 for line in accounts if line["kind"] == "matched")
    above = [
        line
        for line in accounts
        if line["kind"] != "matched" and abs(line["difference"]) >= threshold
    ]
    below = len(accounts) - matched - len(above)
    head = (
        f"{result['name']} {result['period']} (recon #{result['recon_id']}, {result['file_name']}"
        f", threshold {rupees(threshold)}): {len(accounts)} accounts, {matched} matched, "
        f"{len(above)} at or above the threshold, {below} below it (listed)"
    )
    out = [head]
    if result["drafted"] or result["kept"]:
        out.append(
            f"draft queries: {', '.join(f'#{i}' for i in result['drafted']) or 'none new'}"
            + (
                f"; already raised: {', '.join(f'#{i}' for i in result['kept'])}"
                if result["kept"]
                else ""
            )
        )
    for line in accounts:
        if line["kind"] == "matched":
            continue
        if line["query_id"]:
            mark = f"query #{line['query_id']}"
        elif abs(line["difference"]) < threshold:
            mark = "below the threshold"
        else:
            mark = "no query yet"
        out.append(
            f"  [item #{line['item_id']}] {line['kind']} {line['account']}: trial balance "
            f"{rupees(line['left_amount'])}, "
            f"statements {rupees(line['right_amount'])}, difference {rupees(line['difference'])}"
            f" ({mark})"
        )
    if result.get("stale"):
        ids = " ".join(str(i) for i in result["stale"])
        out.append(
            f"stale draft queries: {', '.join(f'#{i}' for i in result['stale'])}: their difference "
            "is not in this run (a mapping or figure changed); nothing was withdrawn: withdraw "
            f"them when the user says so (`fin query withdraw <company> {ids} --reason ...`)"
        )
    if result["unmapped"]:
        out.append(
            f"{len(result['unmapped'])} ledgers not mapped to an account (flagged, not guessed; "
            "no query is drafted until every ledger is confirmed, `fin map ledgers`): "
            + ", ".join(result["unmapped"])
        )
    return "\n".join(out)
