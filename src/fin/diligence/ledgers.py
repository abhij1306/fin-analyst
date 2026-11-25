"""Tally ledgers mapped to Schedule III accounts (`fin map ledgers`; `fin review ... --ledgers`).

A trial balance load adds its new ledgers to `ledger_accounts` (db/schema/sources.sql) as
pending (`sync`). Claude proposes an account for each (`propose`, like `fin map propose`);
nothing is applied. The user confirms (`confirm`) or rejects (`reject`) them in review; fin never
confirms one on its own. Only confirmed ledgers roll up into accounts (`fin.diligence.recon`, the
`stub_figures` view); the others are flagged as unmapped, never guessed.

An account must be a balance sheet or profit and loss account in rupees. Its side says how its
balance reads from the trial balance: 1 for a debit-natured account (assets, expenses), -1 for a
credit-natured one (equity, liabilities, income), from the dictionary tree (`side`).

Every proposal and decision is appended to the audit table `mapping_decisions` (scope 'ledger',
section the ledger's group).
"""

from typing import Any

import duckdb

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database
from fin.map.dictionary import Account, load_accounts
from fin.map.review import LOW_CONFIDENCE, _current_actor

STATEMENTS = ("balance_sheet", "profit_and_loss")
# Top lines whose accounts carry debit balances; every other top line's carry credit balances.
DEBIT_ROOTS = ("total_assets", "total_financial_assets", "total_non_financial_assets")
OPEN = ("pending", "proposed")


def lineage(accounts: dict[str, Account], name: str) -> list[str]:
    """The account and the subtotals it adds into, nearest first."""
    out = [name]
    while (parent := accounts[out[-1]].parent) is not None and parent not in out:
        out.append(parent)
    return out


def side(accounts: dict[str, Account], name: str) -> int:
    """1 when the account's balance is a debit (assets, expenses), -1 when it is a credit."""
    chain = lineage(accounts, name)
    sign = 1
    for account in chain[:-1]:
        sign *= accounts[account].sign or 1
    return sign * (1 if chain[-1] in DEBIT_ROOTS else -1)


def check_account(accounts: dict[str, Account], account: Any, what: str) -> Account:
    """The dictionary account a ledger may map to. E304 otherwise."""
    if not isinstance(account, str) or account not in accounts:
        raise InvalidMapping(f"{what}: unknown account {account!r} (`fin map accounts`)")
    found = accounts[account]
    if found.statement not in STATEMENTS or found.unit != "rupees":
        raise InvalidMapping(
            f"{what}: {account} is a {found.statement} account in {found.unit}; a ledger maps to "
            "a balance sheet or profit and loss account in rupees"
        )
    return found


def sync(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    """Add the ledgers of the loaded trial balances that are not in `ledger_accounts` yet, as
    pending, in the order they first appear. Returns how many were added."""
    added = con.execute(
        "INSERT INTO ledger_accounts (ledger, group_name, parent_group, status, run_id, "
        "changed_at) SELECT t.ledger, arg_max(t.group_name, t.file_id), "
        "arg_max(t.parent_group, t.file_id), "
        "'pending', ?, ? FROM trial_balance t JOIN source_files s ON s.file_id = t.file_id "
        "WHERE s.status = 'loaded' AND s.kind = 'trial_balance' AND t.row_type = 'ledger' "
        "AND t.ledger NOT IN (SELECT ledger FROM ledger_accounts) "
        'GROUP BY t.ledger ORDER BY min(t.file_id), min(t."row") RETURNING ledger_id',
        [run_id, utcnow()],
    ).fetchall()
    return len(added)


def read(con: duckdb.DuckDBPyConnection, open_only: bool = False) -> list[dict[str, Any]]:
    """The ledgers (only those waiting for a decision when `open_only`), by id, with whether a
    loaded trial balance (in_tb) and loaded Ledger Vouchers (in_lv) have them."""
    where = "WHERE m.status IN ('pending', 'proposed')" if open_only else ""
    return fetch_dicts(
        con,
        "SELECT m.*, EXISTS (SELECT 1 FROM trial_balance t JOIN source_files s USING (file_id) "
        "WHERE s.status = 'loaded' AND t.ledger = m.ledger AND t.row_type = 'ledger') AS in_tb, "
        "EXISTS (SELECT 1 FROM ledger_blocks e JOIN source_files s USING (file_id) "
        "WHERE s.status = 'loaded' AND e.ledger = m.ledger) AS in_lv "
        f"FROM ledger_accounts m {where} ORDER BY m.ledger_id",
    )


def list_ledgers(run: Run, company: str, open_only: bool = False) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return read(con, open_only)


def _decide(run: Run, company: str, row: dict[str, Any], decision: str) -> None:
    actor, on_request = ("claude", False) if decision == "proposed" else _current_actor()
    run.record(
        "mapping_decisions",
        {
            "company": company,
            "statement": row["statement"],
            "section": row["group_name"] or "",
            "raw_label": row["ledger"],
            "account": row["account"],
            "scope": "ledger",
            "decision": decision,
            "actor": actor,
            "on_request": on_request,
            "confidence": row["confidence"],
            "reason": row["reason"],
        },
    )


def _by_ledger(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    return {row["ledger"]: row for row in fetch_dicts(con, "SELECT * FROM ledger_accounts")}


def _check_proposal(number: int, proposal: Any, known: dict[str, dict[str, Any]]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict):
        raise InvalidMapping(f"{what}: expected an object with ledger, account, confidence, reason")
    for field in ("ledger", "account", "confidence", "reason"):
        if field not in proposal:
            raise InvalidMapping(f"{what}: missing {field!r}")
    if not isinstance(proposal["reason"], str):
        raise InvalidMapping(f"{what}: 'reason' must be text")
    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise InvalidMapping(f"{what}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise InvalidMapping(f"{what}: confidence must be from 0 to 1")
    row = known.get(proposal["ledger"]) if isinstance(proposal["ledger"], str) else None
    if row is None:
        raise InvalidMapping(
            f"{what}: {proposal['ledger']!r} is not a ledger of a loaded trial balance "
            "(`fin map ledgers <company>`; party ledgers go by their token)"
        )
    if row["status"] == "confirmed":
        raise InvalidMapping(
            f"{what}: {row['ledger']} is confirmed as {row['account']}; only the user changes it "
            "(`fin review confirm <company> <id> --ledgers --account <account>`)"
        )


def propose(run: Run, company: str, proposals: list[Any]) -> int:
    """Store Claude's proposals ({ledger, account, confidence, reason}) on the ledgers; nothing is
    confirmed. All or nothing: a bad proposal (E304) stores none. Returns how many were stored."""
    accounts = load_accounts(run.root)
    with company_database(run, company, write=True) as con, transaction(con):
        known = _by_ledger(con)
        for number, proposal in enumerate(proposals, start=1):
            _check_proposal(number, proposal, known)
            check_account(accounts, proposal["account"], f"proposal {number}")
        for proposal in proposals:
            row = known[proposal["ledger"]]
            row.update(
                status="proposed",
                account=proposal["account"],
                statement=accounts[proposal["account"]].statement,
                confidence=float(proposal["confidence"]),
                reason=proposal["reason"],
            )
            con.execute(
                "UPDATE ledger_accounts SET status = 'proposed', account = ?, statement = NULL, "
                "side = NULL, confidence = ?, reason = ?, run_id = ?, changed_at = ? "
                "WHERE ledger_id = ?",
                [row["account"], row["confidence"], row["reason"], run.run_id, utcnow()]
                + [row["ledger_id"]],
            )
            _decide(run, company, row, "proposed")
            if row["confidence"] < LOW_CONFIDENCE:
                run.event(
                    "map",
                    f"ledger {row['ledger']!r} proposed as {row['account']} with confidence "
                    f"{row['confidence']}: low, review it",
                    level="warning",
                    code="E302",
                    context={"company": company, "ledger_id": row["ledger_id"]},
                )
    return len(proposals)


def _rows(con: duckdb.DuckDBPyConnection, ids: list[int]) -> list[dict[str, Any]]:
    """The ledgers with these ids, in the order given. E304 for an unknown id."""
    found = {
        row["ledger_id"]: row
        for row in fetch_dicts(
            con, "SELECT * FROM ledger_accounts WHERE list_contains(?::INTEGER[], ledger_id)", [ids]
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise InvalidMapping(f"no ledger with id {', '.join(missing)} (`fin map ledgers`)")
    return [found[i] for i in ids]


def confirm(
    run: Run, company: str, ids: list[int], account: str | None = None
) -> list[dict[str, Any]]:
    """Confirm the ledgers' proposed accounts (or `account` instead), all or nothing. E304 for an
    unknown id, a ledger with no proposal and no `account`, or an account a ledger cannot map
    to."""
    ids = list(dict.fromkeys(ids))
    accounts = load_accounts(run.root)
    with company_database(run, company, write=True) as con, transaction(con):
        rows = _rows(con, ids)
        for row in rows:
            chosen = account or row["account"]
            if chosen is None:
                raise InvalidMapping(
                    f"ledger #{row['ledger_id']} has no proposed account; give one with --account"
                )
            check_account(accounts, chosen, f"ledger #{row['ledger_id']}")
        for row in rows:
            edited = account is not None and account != row["account"]
            chosen = account or row["account"]
            row.update(account=chosen, statement=accounts[chosen].statement)
            con.execute(
                "UPDATE ledger_accounts SET status = 'confirmed', account = ?, statement = ?, "
                "side = ?, run_id = ?, changed_at = ? WHERE ledger_id = ?",
                [chosen, row["statement"], side(accounts, chosen), run.run_id, utcnow()]
                + [row["ledger_id"]],
            )
            _decide(run, company, row, "edited" if edited else "confirmed")
        return _rows(con, ids)


def reject(run: Run, company: str, ids: list[int]) -> list[dict[str, Any]]:
    """Reject the ledgers' proposals, all or nothing (E304 for an unknown id). A rejected ledger
    is unmapped until a new proposal is confirmed."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        rows = _rows(con, ids)
        for row in rows:
            con.execute(
                "UPDATE ledger_accounts SET status = 'rejected', statement = NULL, side = NULL, "
                "run_id = ?, changed_at = ? WHERE ledger_id = ?",
                [run.run_id, utcnow(), row["ledger_id"]],
            )
            _decide(run, company, row, "rejected")
        return _rows(con, ids)


def format_ledger(row: dict[str, Any]) -> str:
    where = ", ".join(
        name
        for name, present in (("trial balance", row["in_tb"]), ("vouchers", row["in_lv"]))
        if present
    )
    line = f"ledger #{row['ledger_id']} [{row['status']}] {row['ledger']!r}"
    line += f" in {row['group_name'] or '-'} ({where or 'in no loaded file'})"
    if row["account"]:
        line += f" -> {row['account']}"
        if row["status"] != "confirmed" and row["confidence"] is not None:
            line += f" (confidence {row['confidence']}): {row['reason']}"
    return line
