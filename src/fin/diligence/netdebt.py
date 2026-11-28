"""FDD F4 (docs/fdd.md): net debt and debt-like items at each period end.

Each trial balance ledger has a net debt class: cash, debt, debt_like or none. fin defaults it from
the ledger's confirmed account (`netdebt_default_accounts` in db/schema/fdd_netdebt.sql: borrowings
and lease liabilities are debt, cash cash, a profit and loss account none) and leaves the rest
unclassified: other bank balances (deposits, margin money, lien-marked balances) wait for the
user. Claude proposes a class for a ledger with a reason (`propose`); the
user confirms or rejects it (`decide`, `fin review confirm|reject <co> <ids> --netdebt`); fin never
confirms one on its own. A confirmed class wins over the default. Every change is a run event.

Debt-like items not in a ledger are `fin.diligence.netdebt_items`. `collect` reads what
`fin netdebt show` prints and what the FDD report (F8) calls: the views `net_debt` and
`net_debt_lines`. Nothing changes company data.
"""

from typing import Any

import duckdb

from fin.audit.errors import NetDebtClassInvalid, NetDebtClassUnknown, NetDebtUnavailable
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts, fetch_one
from fin.diligence.engagement import company_database, refuse_if_closed, rupees

CLASSES = ("cash", "debt", "debt_like", "none")


def _check(number: int, proposal: Any, ledgers: set[str], confirmed: set[str]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict) or set(proposal) != {"ledger", "class", "reason"}:
        raise NetDebtClassInvalid(f"{what}: expected an object with ledger, class and reason")
    if proposal["class"] not in CLASSES:
        raise NetDebtClassInvalid(f"{what}: class must be one of {', '.join(CLASSES)}")
    if not isinstance(proposal["reason"], str) or not proposal["reason"].strip():
        raise NetDebtClassInvalid(f"{what}: give the reason as text")
    if proposal["ledger"] not in ledgers:
        raise NetDebtClassInvalid(
            f"{what}: {proposal['ledger']!r} is not a ledger of a loaded trial balance of the "
            "latest engagement (`fin netdebt propose <company>` lists the unclassified ones)"
        )
    if proposal["ledger"] in confirmed:
        raise NetDebtClassInvalid(
            f"{what}: {proposal['ledger']}'s class is confirmed; only the user changes it "
            "(`fin review reject <company> <id> --netdebt`)"
        )


def propose(run: Run, company: str, proposals: Any) -> list[int]:
    """Store Claude's proposals ({ledger, class, reason}), all or nothing (E930). A ledger proposed
    or rejected before is proposed again with the new class and reason. Returns their ids."""
    if not isinstance(proposals, list) or not proposals:
        raise NetDebtClassInvalid("expected a JSON list of {ledger, class, reason}; none stored")
    with company_database(run, company, write=True) as con, transaction(con):
        ledgers = {
            ledger
            for (ledger,) in con.execute(
                "SELECT DISTINCT t.ledger FROM trial_balance t JOIN source_files s USING (file_id) "
                "WHERE s.status = 'loaded' AND t.row_type = 'ledger' "
                "AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)"
            ).fetchall()
        }
        confirmed = {
            ledger
            for (ledger,) in con.execute(
                "SELECT ledger FROM netdebt_classes WHERE status = 'confirmed'"
            ).fetchall()
        }
        for number, proposal in enumerate(proposals, start=1):
            _check(number, proposal, ledgers, confirmed)
        named = [p["ledger"] for p in proposals]
        if len(set(named)) != len(named):
            raise NetDebtClassInvalid("a ledger is proposed twice in the file; none stored")
        ids = []
        for p in proposals:
            reason = p["reason"].strip()
            values = [p["ledger"], p["class"], reason, run.run_id, utcnow()]
            (class_id,) = fetch_one(
                con,
                "INSERT INTO netdebt_classes (ledger, debt_class, status, reason, run_id, "
                "changed_at) VALUES (?, ?, 'proposed', ?, ?, ?) ON CONFLICT (ledger) DO UPDATE "
                "SET debt_class = excluded.debt_class, status = 'proposed', "
                "reason = excluded.reason, run_id = excluded.run_id, "
                "changed_at = excluded.changed_at RETURNING class_id",
                values,
            )
            ids.append(class_id)
            _log(run, company, class_id, f"proposed {p['ledger']!r} as {p['class']}")
    return ids


def decide(run: Run, company: str, ids: list[int], status: str) -> list[dict[str, Any]]:
    """The user's decision on class proposals, all or nothing: 'confirmed' (a proposal) or
    'rejected' (a proposal or a confirmed class). E931 for an unknown id or one not waiting for
    it."""
    ids = list(dict.fromkeys(ids))
    allowed = ("proposed",) if status == "confirmed" else ("proposed", "confirmed")
    with company_database(run, company, write=True) as con, transaction(con):
        refuse_if_closed(con, company)
        rows = {r["class_id"]: r for r in _classes(con)}
        for class_id in ids:
            row = rows.get(class_id)
            if row is None or row["status"] not in allowed:
                state = "no such id" if row is None else row["status"]
                raise NetDebtClassUnknown(
                    f"net debt class #{class_id} cannot be {status} ({state}); nothing changed "
                    f"(`fin review list {company} --netdebt --all`)"
                )
        for class_id in ids:
            con.execute(
                "UPDATE netdebt_classes SET status = ?, run_id = ?, changed_at = ? "
                "WHERE class_id = ?",
                [status, run.run_id, utcnow(), class_id],
            )
            row = rows[class_id]
            _log(run, company, class_id, f"{status} {row['ledger']!r} as {row['debt_class']}")
        return [r for r in _classes(con) if r["class_id"] in ids]


def _log(run: Run, company: str, class_id: int, what: str) -> None:
    run.event(
        "netdebt",
        f"net debt class #{class_id}: {what}",
        context={"company": company, "class_id": class_id},
    )


def _classes(con: duckdb.DuckDBPyConnection, open_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE n.status = 'proposed'" if open_only else ""
    return fetch_dicts(
        con,
        "SELECT n.*, m.account, d.debt_class AS default_class FROM netdebt_classes n "
        "LEFT JOIN ledger_accounts m ON m.ledger = n.ledger AND m.status = 'confirmed' "
        f"LEFT JOIN netdebt_default_accounts d ON d.account = m.account {where} "
        "ORDER BY n.class_id",
    )


def list_classes(run: Run, company: str, open_only: bool = False) -> list[dict[str, Any]]:
    """The class proposals (only those waiting for the user when `open_only`), by id."""
    with company_database(run, company) as con:
        return _classes(con, open_only)


def unclassified(run: Run, company: str) -> list[dict[str, Any]]:
    """The latest engagement's trial balance ledgers with no class, for Claude to propose."""
    with company_database(run, company) as con:
        return fetch_dicts(
            con,
            "SELECT ledger, group_name, account, proposal_id, proposed_class "
            "FROM netdebt_ledger_classes WHERE debt_class IS NULL ORDER BY ledger",
        )


def collect(con: duckdb.DuckDBPyConnection, period: str | None = None) -> dict[str, Any]:
    """F4's figures for a report or `fin netdebt show` (every period end, or one): the net_debt
    rows (annual periods, then the stub) and the net_debt_lines. The FDD report (F8) calls this."""
    where, args = ("WHERE period = ?", [period]) if period else ("", [])
    return {
        "net_debt": fetch_dicts(
            con, f"SELECT * EXCLUDE (n) FROM net_debt {where} ORDER BY n, period", args
        ),
        "lines": fetch_dicts(
            con,
            f"SELECT * FROM net_debt_lines {where} ORDER BY period, line_kind, debt_class, "
            "ledger, item_id",
            args,
        ),
    }


def show(run: Run, company: str, period: str | None) -> dict[str, Any]:
    """F4's figures for the latest engagement. E934 without one, or for a period not in it."""
    with company_database(run, company) as con:
        row = con.execute(
            "SELECT max(engagement_id), arg_max(periods, engagement_id), "
            "arg_max(stub, engagement_id) FROM engagement"
        ).fetchone()
        if row is None or row[0] is None:
            raise NetDebtUnavailable(
                f"{company} has no engagement; open one and load its trial balances (`/diligence`)"
            )
        known = row[1].split(",") + ([row[2]] if row[2] else [])
        if period is not None and period not in known:
            raise NetDebtUnavailable(
                f"{period} is not a period of {company}'s latest engagement ({', '.join(known)})"
            )
        return collect(con, period)


def format_class(row: dict[str, Any]) -> str:
    line = f"{row['company']} " if row.get("company") else ""
    line += f"net debt class #{row['class_id']} [{row['status']}] {row['ledger']!r} -> "
    line += f"{row['debt_class']}: {row['reason']}"
    if row.get("account"):
        default = row["default_class"] or "unclassified"
        line += f" (account {row['account']}, default {default})"
    return line


def format_show(data: dict[str, Any]) -> str:
    out = ["Net debt at each period end (trial balance ledgers by class, and debt-like items)"]
    for r in data["net_debt"]:
        out.extend(_period_lines(r))
    for line in data["lines"]:
        what = line["ledger"] if line["line_kind"] == "ledger" else f"item #{line['item_id']}"
        note = line["class_source"] or line["status"]
        desc = f" {line['description']}" if line["description"] else ""
        out.append(
            f"    {line['period']} {line['debt_class']:>9} {rupees(line['amount'])} {what} "
            f"[{note}]{desc} ({line['source']})"
        )
    return "\n".join(out)


def _period_lines(r: dict[str, Any]) -> list[str]:
    """The lines for one period end of the net debt summary."""
    label = r["period"] + ("" if r["basis"] == "audited" else " (unaudited management accounts)")
    if r["trial_balance_file"] is None:
        return [f"  {label}: no trial balance loaded; net debt not computed"]
    out = [
        f"  {label} at {r['period_end']}: cash {rupees(r['cash'])}, debt {rupees(r['debt'])}, "
        f"debt-like {rupees(r['debt_like'])} (ledgers {rupees(r['debt_like_ledgers'])}, "
        f"accepted items {rupees(r['debt_like_items'])}), net debt {rupees(r['net_debt'])}"
    ]
    if r["proposed_item_count"]:
        out.append(
            f"    proposed items, not applied: {r['proposed_item_count']} "
            f"({rupees(r['proposed_items'])})"
        )
    if r["unclassified_ledgers"]:
        out.append(
            f"    {r['unclassified_ledgers']} ledgers unclassified: net debt may be incomplete"
        )
    if r["proposed_classes"]:
        out.append(
            f"    {r['proposed_classes']} class proposals wait for the user (not applied; "
            "`fin review list <company> --netdebt`)"
        )
    return out
