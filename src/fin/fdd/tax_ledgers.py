"""Tax ledger classes (`fin map tax`; `fin review ... --tax`), docs/fdd.md F6.

Claude proposes which trial balance ledgers are GST output, GST input, RCM, TDS, income tax or MAT
credit ledgers (`propose`: {ledger, class, confidence, reason}); nothing is applied. The user
confirms (`confirm`, `--tax-class` to choose another) or rejects (`reject`) them in review; fin
never confirms one on its own. Only confirmed classes are read: by the GST reconciliations
(`fin.reconcile.gst`) and the `tax_ledger_balances` and `tax_charge` views (db/schema/fdd_gst.sql).
A ledger is one of the loaded trial balances' ledgers, as filed.

Every proposal and decision is appended to the audit table `mapping_decisions` (scope 'tax').
"""

from typing import Any

import duckdb

from fin.audit.errors import TaxLedgerInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database
from fin.map.review import LOW_CONFIDENCE, _current_actor

CLASSES = ("gst_output", "gst_input", "rcm", "tds", "income_tax", "mat_credit")
FIELDS = ("ledger", "class", "confidence", "reason")
_SELECT = (
    "SELECT x.*, a.group_name FROM tax_ledgers x LEFT JOIN ledger_accounts a ON a.ledger = x.ledger"
)


def check_class(value: Any, what: str) -> str:
    if value not in CLASSES:
        raise TaxLedgerInvalid(f"{what}: class {value!r} is not one of {', '.join(CLASSES)}")
    return str(value)


def read(con: duckdb.DuckDBPyConnection, open_only: bool = False) -> list[dict[str, Any]]:
    """The tax ledger rows by id (only the proposed ones when `open_only`), with their group."""
    where = " WHERE x.status = 'proposed'" if open_only else ""
    return fetch_dicts(con, f"{_SELECT}{where} ORDER BY x.tax_ledger_id")


def list_rows(run: Run, company: str, open_only: bool = False) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return read(con, open_only)


def unclassified(run: Run, company: str) -> list[dict[str, Any]]:
    """The trial balance ledgers with no tax class proposed or confirmed, with their group: what
    Claude reads to propose."""
    with company_database(run, company) as con:
        return fetch_dicts(
            con,
            "SELECT ledger, group_name FROM ledger_accounts WHERE ledger NOT IN (SELECT ledger "
            "FROM tax_ledgers WHERE status <> 'rejected') ORDER BY ledger_id",
        )


def _decide(run: Run, company: str, row: dict[str, Any], decision: str) -> None:
    actor, on_request = ("claude", False) if decision == "proposed" else _current_actor()
    run.record(
        "mapping_decisions",
        {
            "company": company,
            "statement": None,
            "section": "tax ledger class",
            "raw_label": row["ledger"],
            "account": row["tax_class"],
            "scope": "tax",
            "decision": decision,
            "actor": actor,
            "on_request": on_request,
            "confidence": row["confidence"],
            "reason": row["reason"],
        },
    )


def _check(number: int, proposal: Any, ledgers: set[str], rows: dict[str, Any]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict) or any(f not in proposal for f in FIELDS):
        raise TaxLedgerInvalid(f"{what}: expected an object with {', '.join(FIELDS)}")
    if not isinstance(proposal["reason"], str):
        raise TaxLedgerInvalid(f"{what}: 'reason' must be text")
    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise TaxLedgerInvalid(f"{what}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise TaxLedgerInvalid(f"{what}: confidence must be from 0 to 1")
    check_class(proposal["class"], what)
    ledger = proposal["ledger"]
    if not isinstance(ledger, str) or ledger not in ledgers:
        raise TaxLedgerInvalid(
            f"{what}: {ledger!r} is not a trial balance ledger fin has loaded (`fin map tax "
            "<company>` lists them)"
        )
    row = rows.get(ledger)
    if row is not None and row["status"] == "confirmed":
        raise TaxLedgerInvalid(
            f"{what}: {ledger!r} is confirmed as {row['tax_class']}; only the user changes it "
            "(`fin review confirm <company> <id> --tax --tax-class <class>`)"
        )


def propose(run: Run, company: str, proposals: list[Any]) -> int:
    """Store Claude's proposals; nothing is confirmed. All or nothing: a bad proposal (E951)
    stores none. Returns how many were stored."""
    with company_database(run, company, write=True) as con, transaction(con):
        ledgers = {r for (r,) in con.execute("SELECT ledger FROM ledger_accounts").fetchall()}
        rows = {r["ledger"]: r for r in read(con)}
        for number, proposal in enumerate(proposals, start=1):
            _check(number, proposal, ledgers, rows)
        for p in proposals:
            values = [p["class"], float(p["confidence"]), p["reason"], run.run_id, utcnow()]
            con.execute(
                "INSERT INTO tax_ledgers (ledger, status, tax_class, confidence, reason, run_id, "
                "changed_at) VALUES (?, 'proposed', ?, ?, ?, ?, ?) ON CONFLICT (ledger) DO UPDATE "
                "SET status = 'proposed', tax_class = excluded.tax_class, confidence = "
                "excluded.confidence, reason = excluded.reason, run_id = excluded.run_id, "
                "changed_at = excluded.changed_at",
                [p["ledger"], *values],
            )
            row = {"ledger": p["ledger"], "tax_class": p["class"]}
            row |= {"confidence": float(p["confidence"]), "reason": p["reason"]}
            _decide(run, company, row, "proposed")
            if row["confidence"] < LOW_CONFIDENCE:
                run.event(
                    "map",
                    f"tax ledger {p['ledger']!r} proposed as {p['class']} with confidence "
                    f"{row['confidence']}: low, review it",
                    level="warning",
                    code="E302",
                    context={"company": company},
                )
    return len(proposals)


def _by_ids(con: duckdb.DuckDBPyConnection, ids: list[int]) -> list[dict[str, Any]]:
    found = {
        r["tax_ledger_id"]: r
        for r in fetch_dicts(
            con, f"{_SELECT} WHERE list_contains(?::INTEGER[], tax_ledger_id)", [ids]
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise TaxLedgerInvalid(
            f"no tax ledger with id {', '.join(missing)} (`fin review list <co> --tax --all`)"
        )
    return [found[i] for i in ids]


def confirm(
    run: Run, company: str, ids: list[int], tax_class: str | None = None
) -> list[dict[str, Any]]:
    """Confirm the proposed classes (or `tax_class` instead), all or nothing. E951 for an unknown
    id or class, or a rejected row with no `tax_class`."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        rows = _by_ids(con, ids)
        for row in rows:
            chosen = tax_class if tax_class is not None else row["tax_class"]
            if chosen is None:
                raise TaxLedgerInvalid(
                    f"tax ledger #{row['tax_ledger_id']} has no proposed class; give one with "
                    "--tax-class"
                )
            row["chosen"] = check_class(chosen, f"tax ledger #{row['tax_ledger_id']}")
        for row in rows:
            edited = row["chosen"] != row["tax_class"]
            row["tax_class"] = row["chosen"]
            con.execute(
                "UPDATE tax_ledgers SET status = 'confirmed', tax_class = ?, run_id = ?, "
                "changed_at = ? WHERE tax_ledger_id = ?",
                [row["chosen"], run.run_id, utcnow(), row["tax_ledger_id"]],
            )
            _decide(run, company, row, "edited" if edited else "confirmed")
        return _by_ids(con, ids)


def reject(run: Run, company: str, ids: list[int]) -> list[dict[str, Any]]:
    """Reject the proposals, all or nothing (E951 for an unknown id): the ledger has no class."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        for row in _by_ids(con, ids):
            con.execute(
                "UPDATE tax_ledgers SET status = 'rejected', tax_class = NULL, run_id = ?, "
                "changed_at = ? WHERE tax_ledger_id = ?",
                [run.run_id, utcnow(), row["tax_ledger_id"]],
            )
            _decide(run, company, row, "rejected")
        return _by_ids(con, ids)


def format_row(row: dict[str, Any]) -> str:
    line = f"tax ledger #{row['tax_ledger_id']} [{row['status']}] {row['ledger']!r}"
    line += f" ({row['group_name'] or 'no group'}) -> {row['tax_class'] or 'no class'}"
    if row["status"] == "proposed" and row["confidence"] is not None:
        line += f" (confidence {row['confidence']}): {row['reason']}"
    return line
