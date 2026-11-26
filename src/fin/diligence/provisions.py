"""FDD F3 (docs/fdd.md): provisions against what the books and payroll support.

`provision_ledgers` (db/schema/fdd_bsheet.sql) names the trial balance ledgers that hold the
provision for doubtful debts and the gratuity provision: the dictionary has no account for them, so
Claude proposes ledgers (`propose`) and the user confirms or rejects them (`decide`); fin never
confirms one on its own. Every change is logged as a run event.

`collect` reads what `fin provisions show` prints and what the FDD report (F8) calls:
`receivable_provision` (receivables ageing at each year end against the doubtful debts provision)
and `gratuity_indicative` (indicative, undiscounted gratuity from the payroll snapshot on the
`config.toml [fdd]` assumptions, against the gratuity provision). Nothing is changed or fixed: a
shortfall is shown for the user and Claude to raise as a query or a net debt item.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ProvisionLedgerInvalid, ProvisionLedgerUnknown, ProvisionsUnavailable
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database

KINDS = ("doubtful_debts", "gratuity")
LABEL = "indicative, undiscounted, not an actuarial valuation"


def _check(number: int, proposal: Any, ledgers: set[str], confirmed: set[str]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict) or set(proposal) != {"ledger", "kind", "reason"}:
        raise ProvisionLedgerInvalid(f"{what}: expected an object with ledger, kind and reason")
    if proposal["kind"] not in KINDS:
        raise ProvisionLedgerInvalid(f"{what}: kind must be one of {', '.join(KINDS)}")
    if not isinstance(proposal["reason"], str) or not proposal["reason"].strip():
        raise ProvisionLedgerInvalid(f"{what}: give the reason as text")
    if proposal["ledger"] not in ledgers:
        raise ProvisionLedgerInvalid(
            f"{what}: {proposal['ledger']!r} is not a ledger of a loaded trial balance of the "
            "latest engagement (`fin map ledgers <company>`)"
        )
    if proposal["ledger"] in confirmed:
        raise ProvisionLedgerInvalid(
            f"{what}: {proposal['ledger']} is confirmed; only the user changes it "
            "(`fin provisions reject`)"
        )


def propose(run: Run, company: str, proposals: Any) -> list[int]:
    """Store Claude's proposals ({ledger, kind, reason}), all or nothing (E920). A ledger proposed
    or rejected before is proposed again with the new kind and reason. Returns their ids."""
    if not isinstance(proposals, list) or not proposals:
        raise ProvisionLedgerInvalid("expected a JSON list of {ledger, kind, reason}; none stored")
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
                "SELECT ledger FROM provision_ledgers WHERE status = 'confirmed'"
            ).fetchall()
        }
        for number, proposal in enumerate(proposals, start=1):
            _check(number, proposal, ledgers, confirmed)
        named = [p["ledger"] for p in proposals]
        if len(set(named)) != len(named):
            raise ProvisionLedgerInvalid("a ledger is proposed twice in the file; none stored")
        ids = []
        for p in proposals:
            values = [p["kind"], p["reason"].strip(), run.run_id, utcnow()]
            (provision_id,) = con.execute(
                "INSERT INTO provision_ledgers (ledger, kind, status, reason, run_id, changed_at) "
                "VALUES (?, ?, 'proposed', ?, ?, ?) ON CONFLICT (ledger) DO UPDATE SET "
                "kind = excluded.kind, status = 'proposed', reason = excluded.reason, "
                "run_id = excluded.run_id, changed_at = excluded.changed_at "
                "RETURNING provision_id",
                [p["ledger"], *values],
            ).fetchone()
            ids.append(provision_id)
            _log(run, company, provision_id, f"proposed {p['ledger']!r} as {p['kind']}")
    return ids


def decide(run: Run, company: str, ids: list[int], status: str) -> list[dict[str, Any]]:
    """The user's decision on provision ledgers, all or nothing: 'confirmed' (a proposal) or
    'rejected' (a proposal or a confirmed ledger). E921 for an unknown id or a ledger not waiting
    for it."""
    ids = list(dict.fromkeys(ids))
    allowed = ("proposed",) if status == "confirmed" else ("proposed", "confirmed")
    with company_database(run, company, write=True) as con, transaction(con):
        rows = {r["provision_id"]: r for r in _ledgers(con)}
        for provision_id in ids:
            row = rows.get(provision_id)
            if row is None or row["status"] not in allowed:
                state = "no such id" if row is None else row["status"]
                raise ProvisionLedgerUnknown(
                    f"provision ledger #{provision_id} cannot be {status} ({state}); nothing "
                    f"changed (`fin provisions ledgers {company}`)"
                )
        for provision_id in ids:
            con.execute(
                "UPDATE provision_ledgers SET status = ?, run_id = ?, changed_at = ? "
                "WHERE provision_id = ?",
                [status, run.run_id, utcnow(), provision_id],
            )
            row = rows[provision_id]
            _log(run, company, provision_id, f"{status} {row['ledger']!r} as {row['kind']}")
        return [r for r in _ledgers(con) if r["provision_id"] in ids]


def _log(run: Run, company: str, provision_id: int, what: str) -> None:
    run.event(
        "provisions",
        f"provision ledger #{provision_id}: {what}",
        context={"company": company, "provision_id": provision_id},
    )


def _ledgers(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return fetch_dicts(con, "SELECT * FROM provision_ledgers ORDER BY provision_id")


def list_ledgers(run: Run, company: str) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return _ledgers(con)


def assumptions(fdd: dict[str, Any]) -> dict[str, Any]:
    """The gratuity assumptions, as config.toml [fdd] states them."""
    return {
        "basic_share": fdd["basic_share"],
        "gratuity_min_years": fdd["gratuity_min_years"],
        "gratuity_cap": fdd["gratuity_cap"],
        "label": LABEL,
    }


def collect(
    con: duckdb.DuckDBPyConnection, fdd: dict[str, Any], period: str | None = None
) -> dict[str, Any]:
    """F3's figures for a report or `fin provisions show` (all periods, or one): the
    receivable_provision rows, the gratuity_indicative rows, the assumptions and the provision
    ledgers. The FDD report (F8) calls this with config.toml's [fdd]."""
    where, args = ("WHERE period = ?", [period]) if period else ("", [])
    params = [Decimal(fdd["basic_share"]), fdd["gratuity_min_years"], Decimal(fdd["gratuity_cap"])]
    gratuity = fetch_dicts(
        con, f"SELECT * FROM gratuity_indicative(?, ?, ?) {where}", [*params, *args]
    )
    return {
        "receivables": fetch_dicts(con, f"SELECT * FROM receivable_provision {where}", args),
        "gratuity": gratuity,
        "assumptions": assumptions(fdd),
        "ledgers": _ledgers(con),
    }


def show(run: Run, company: str, period: str | None) -> dict[str, Any]:
    """F3's figures for the latest engagement. E923 without one, or for a period not in it."""
    fdd = load_config(run.root).fdd
    with company_database(run, company) as con:
        row = con.execute(
            "SELECT max(engagement_id), arg_max(periods, engagement_id) FROM engagement"
        ).fetchone()
        if row is None or row[0] is None:
            raise ProvisionsUnavailable(
                f"{company} has no engagement; open one and load its files (`/diligence`)"
            )
        if period is not None and period not in row[1].split(","):
            raise ProvisionsUnavailable(
                f"{period} is not an annual period of {company}'s latest engagement ({row[1]})"
            )
        return collect(con, fdd, period)
