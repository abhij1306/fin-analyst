"""Adjustments (`fin adjust ...`): proposed effects on EBITDA, revenue and costs, kept beside the
reported figures.

Reported facts are never changed. An adjustment has a period of the engagement, a scope, a category
(config.toml [diligence] adjustment_categories), optionally the dictionary account it relates to,
signed effects in rupees (revenue effect, cost effect, and the EBITDA effect, which is always
revenue effect minus cost effect), a description, and evidence: a query, a finding or a source
reference (at least one). A reclassification between two cost accounts names both (`account`,
`from_account`) and the amount moved; its effects are 0. Claude proposes; the user accepts or
rejects (table `adjustments`, db/schema/diligence.sql):

    proposed -> accepted, or proposed -> rejected (with the reason).

Accepting or rejecting needs gate G2 approved (fin.diligence.gates): E801 otherwise.

Every adjustment proposed or decided is appended to the audit table `diligence_events`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import AdjustmentInvalid, DiligenceChangeRefused
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence import gates
from fin.diligence.engagement import company_database, record_event, require_open, rupees
from fin.map.dictionary import load_accounts


def _get(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], company: str, adj_id: int
) -> dict[str, Any]:
    rows = fetch_dicts(
        con,
        "SELECT * FROM adjustments WHERE adj_id = ? AND engagement_id = ?",
        [adj_id, engagement["engagement_id"]],
    )
    if not rows:
        raise DiligenceChangeRefused(
            f"adjustment {adj_id} is not an adjustment of {company}'s open engagement "
            f"#{engagement['engagement_id']}; nothing changed. Adjustments: "
            f"`fin adjust list {company}`"
        )
    return rows[0]


def _check_links(
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    company: str,
    query_id: int | None,
    finding_id: int | None,
) -> None:
    """E805 when the query is not one of the engagement's, or the finding does not exist."""
    if query_id is not None:
        found = con.execute(
            "SELECT count(*) FROM accountant_queries WHERE query_id = ? AND engagement_id = ?",
            [query_id, engagement["engagement_id"]],
        ).fetchone()
        if found is None or found[0] == 0:
            raise DiligenceChangeRefused(
                f"query {query_id} is not a query of {company}'s open engagement; nothing "
                f"proposed. Queries: `fin query list {company}`"
            )
    if finding_id is not None:
        found = con.execute(
            "SELECT count(*) FROM findings WHERE finding_id = ?", [finding_id]
        ).fetchone()
        if found is None or found[0] == 0:
            raise DiligenceChangeRefused(
                f"finding {finding_id} of {company} does not exist; nothing proposed. Findings: "
                f"`fin findings list {company}`"
            )


ZERO = Decimal("0.00")
# Expense accounts EBITDA adds back: a cost moved across them would change EBITDA.
BELOW_EBITDA = ("finance_cost", "depreciation", "interest_expended", "provisions_and_contingencies")


def resolve_effects(
    amount: Decimal | None, revenue_effect: Decimal | None, cost_effect: Decimal | None
) -> tuple[Decimal, Decimal, Decimal]:
    """(EBITDA effect, revenue effect, cost effect). Given an amount alone, it is a cost effect of
    minus the amount (an add-back lowers costs). Given a revenue or cost effect, the EBITDA effect
    is revenue minus cost, and an amount given too must equal it. E804 otherwise."""
    if revenue_effect is None and cost_effect is None:
        if amount is None:
            raise AdjustmentInvalid(
                "give --amount (the effect on EBITDA), or --revenue-effect and --cost-effect; "
                "nothing proposed"
            )
        return amount, ZERO, -amount
    revenue, cost = revenue_effect or ZERO, cost_effect or ZERO
    if amount is not None and amount != revenue - cost:
        raise AdjustmentInvalid(
            f"the EBITDA effect {amount} is not the revenue effect {revenue} minus the cost "
            f"effect {cost} ({revenue - cost}); nothing proposed"
        )
    return revenue - cost, revenue, cost


def _check_reclassification(
    run: Run,
    category: str,
    account: str | None,
    from_account: str | None,
    moved_amount: Decimal | None,
    effects: tuple[Decimal, Decimal, Decimal],
) -> None:
    """E804 unless a cost-to-cost reclassification names two different cost accounts of the
    dictionary and a positive amount moved, and has no net effect."""
    if (from_account is None) != (moved_amount is None):
        raise AdjustmentInvalid("--from-account and --moved-amount go together; nothing proposed")
    if from_account is None:
        return
    if category != "reclassification":
        raise AdjustmentInvalid(
            f"--from-account is for the 'reclassification' category, not {category!r}; nothing "
            "proposed"
        )
    accounts = load_accounts(run.root)
    for name, value in (("--account", account), ("--from-account", from_account)):
        found = accounts.get(value) if value else None
        if (
            found is None
            or (found.statement, found.parent) != ("profit_and_loss", "total_expenses")
            or value in BELOW_EBITDA
        ):
            raise AdjustmentInvalid(
                f"{name} {value!r} must be a cost account within EBITDA (an expense of the profit "
                "and loss other than finance cost and depreciation, see `fin map accounts`); "
                "nothing proposed"
            )
    if account == from_account:
        raise AdjustmentInvalid("a reclassification needs two different accounts; nothing proposed")
    if moved_amount is not None and moved_amount <= 0:
        raise AdjustmentInvalid("--moved-amount must be positive; nothing proposed")
    if any(effects):
        raise AdjustmentInvalid(
            "a reclassification between cost accounts has no revenue, cost or EBITDA effect "
            "(it only moves cost); nothing proposed"
        )


@dataclass(frozen=True)
class Evidence:
    """What backs an adjustment: a query, a finding and/or a source reference."""

    query_id: int | None = None
    finding_id: int | None = None
    source_ref: str | None = None


NO_EVIDENCE = Evidence()


def propose(
    run: Run,
    company: str,
    *,
    period: str,
    scope: str,
    category: str,
    description: str,
    amount: Decimal | None = None,
    revenue_effect: Decimal | None = None,
    cost_effect: Decimal | None = None,
    account: str | None = None,
    from_account: str | None = None,
    moved_amount: Decimal | None = None,
    evidence: Evidence = NO_EVIDENCE,
) -> dict[str, Any]:
    """Propose an adjustment. E804 without evidence, for a category not in config.toml, a period
    not in the engagement, an account not in the dictionary, effects that do not add up
    (`resolve_effects`) or a reclassification that is not valid (`_check_reclassification`);
    E805 for an unknown query or finding."""
    effects = resolve_effects(amount, revenue_effect, cost_effect)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        return propose_in(
            run,
            con,
            company,
            engagement,
            effects,
            period=period,
            scope=scope,
            category=category,
            description=description,
            account=account,
            from_account=from_account,
            moved_amount=moved_amount,
            evidence=evidence,
        )


def check_in(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    company: str,
    engagement: dict[str, Any],
    effects: tuple[Decimal, Decimal, Decimal],
    *,
    period: str,
    category: str,
    account: str | None = None,
    from_account: str | None = None,
    moved_amount: Decimal | None = None,
    evidence: Evidence = NO_EVIDENCE,
) -> None:
    """The checks of `propose_in`, writing nothing (`fin adjust import` checks every row before
    it adds any). E804, E805 as `propose`."""
    if evidence.query_id is None and evidence.finding_id is None and not evidence.source_ref:
        raise AdjustmentInvalid(
            "an adjustment needs evidence: --query, --finding or --source; nothing proposed"
        )
    categories = load_config(run.root).adjustment_categories
    if category not in categories:
        raise AdjustmentInvalid(
            f"category {category!r} is not in config.toml [diligence] adjustment_categories "
            f"({', '.join(categories)}); nothing proposed"
        )
    periods = engagement["periods"].split(",")
    if period not in periods:
        raise AdjustmentInvalid(
            f"period {period} is not in the engagement ({engagement['periods']}); nothing proposed"
        )
    if account is not None and account not in load_accounts(run.root):
        raise AdjustmentInvalid(
            f"account {account!r} is not in the dictionary (`fin map accounts`); nothing proposed"
        )
    _check_reclassification(run, category, account, from_account, moved_amount, effects)
    _check_links(con, engagement, company, evidence.query_id, evidence.finding_id)


def propose_in(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    company: str,
    engagement: dict[str, Any],
    effects: tuple[Decimal, Decimal, Decimal],
    *,
    period: str,
    scope: str,
    category: str,
    description: str,
    account: str | None = None,
    from_account: str | None = None,
    moved_amount: Decimal | None = None,
    evidence: Evidence = NO_EVIDENCE,
) -> dict[str, Any]:
    """`propose` in the caller's transaction: `check_in`, then the insert (`effects` as
    `resolve_effects` gives them)."""
    check_in(
        run,
        con,
        company,
        engagement,
        effects,
        period=period,
        category=category,
        account=account,
        from_account=from_account,
        moved_amount=moved_amount,
        evidence=evidence,
    )
    query_id, finding_id, source_ref = evidence.query_id, evidence.finding_id, evidence.source_ref
    now = utcnow()
    row = {
        "engagement_id": engagement["engagement_id"],
        "period": period,
        "scope": scope,
        "category": category,
        "account": account,
        "amount": effects[0],
        "revenue_effect": effects[1],
        "cost_effect": effects[2],
        "from_account": from_account,
        "moved_amount": moved_amount,
        "description": description,
        "query_id": query_id,
        "finding_id": finding_id,
        "source_ref": source_ref or None,
        "status": "proposed",
        "created_run_id": run.run_id,
        "created_at": now,
        "status_run_id": run.run_id,
        "status_at": now,
    }
    new = con.execute(
        f"INSERT INTO adjustments ({', '.join(row)}) "
        f"VALUES ({', '.join('?' for _ in row)}) RETURNING adj_id",
        list(row.values()),
    ).fetchone()
    assert new is not None
    record_event(run, company, con, "adjustment", new[0])
    return _get(con, engagement, company, new[0])


def decide(
    run: Run, company: str, ids: list[int], status: str, reason: str | None = None
) -> list[dict[str, Any]]:
    """Accept or reject proposed adjustments, all or none. E801 unless gate G2 (and G1 before it)
    is approved; E805 for an unknown id or one that is not proposed."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        command = f"fin adjust {'accept' if status == 'accepted' else 'reject'}"
        gates.require(gates.context(con, run.root, company), "G2", command)
        for row in [_get(con, engagement, company, adj_id) for adj_id in ids]:
            if row["status"] != "proposed":
                raise DiligenceChangeRefused(
                    f"adjustment {row['adj_id']} of {company} is {row['status']}; only a proposed "
                    f"adjustment can be {status}. Nothing changed"
                )
        for adj_id in ids:
            con.execute(
                "UPDATE adjustments SET status = ?, reason = ?, status_run_id = ?, status_at = ? "
                "WHERE adj_id = ?",
                [status, reason, run.run_id, utcnow(), adj_id],
            )
            record_event(run, company, con, "adjustment", adj_id)
        return [_get(con, engagement, company, adj_id) for adj_id in ids]


def list_adjustments(run: Run, company: str, status: str | None = None) -> list[dict[str, Any]]:
    """The open engagement's adjustments (only those in `status` when given), by id."""
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        only = "" if status is None else " AND status = ?"
        return fetch_dicts(
            con,
            f"SELECT * FROM adjustments WHERE engagement_id = ?{only} ORDER BY adj_id",
            [engagement["engagement_id"], *([] if status is None else [status])],
        )


def evidence_text(row: dict[str, Any]) -> str:
    """An adjustment's evidence: 'query 3; finding 7; source walk.xlsx Adjustments!G5'."""
    return "; ".join(
        f"{name} {value}"
        for name, value in (
            ("query", row["query_id"]),
            ("finding", row["finding_id"]),
            ("source", row["source_ref"]),
        )
        if value is not None
    )


def format_adjustment(row: dict[str, Any]) -> str:
    account = f" {row['account']}" if row["account"] else ""
    if row["from_account"]:
        account = f" {row['from_account']} -> {row['account']} {rupees(row['moved_amount'])} moved,"
    evidence = evidence_text(row)
    lines = [
        f"#{row['adj_id']} {row['status']} {row['period']} {row['scope']} {row['category']}"
        f"{account} {rupees(row['amount'])}: {row['description']}",
        f"    evidence: {evidence}",
    ]
    if row["revenue_effect"] != 0 or (row["cost_effect"] + row["amount"]) != 0:
        lines.append(
            f"    effects: revenue {rupees(row['revenue_effect'])}, "
            f"costs {rupees(row['cost_effect'])}, EBITDA {rupees(row['amount'])}"
        )
    if row["reason"] is not None:
        lines.append(f"    reason: {row['reason']}")
    return "\n".join(lines)
