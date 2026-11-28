"""Customer sectors (`fin map customers`; `fin review ... --customers`), for the sector mix.

A registers load adds its sales customers (tokens) to `customers`
(db/schema/diligence_analytics.sql) as pending (`sync`). Claude proposes a sector for each
(`propose`); nothing is applied. The user confirms (`confirm`, `--sector` to choose another) or
rejects (`reject`) them in review; fin never confirms one on its own. Only confirmed sectors count
in `sector_mix`: the other customers are 'unmapped', never guessed. Claude sees tokens only: it
proposes from what the customer bought (products, order types), never a name.

Every proposal and decision is appended to the audit table `mapping_decisions` (scope 'customer').
"""

from typing import Any

import duckdb

from fin.actor import CLAUDE, recorded
from fin.audit.errors import CustomerMappingInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database
from fin.map.review import LOW_CONFIDENCE

UNMAPPED = "unmapped"  # the label sector_mix gives a customer without a confirmed sector


def sync(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    """Add the customers of the loaded sales registers that are not in `customers` yet, as pending,
    in the order they first appear. Returns how many were added."""
    added = con.execute(
        "INSERT INTO customers (customer, status, run_id, changed_at) SELECT r.customer, "
        "'pending', ?, ? FROM register_lines r JOIN source_files s ON s.file_id = r.file_id "
        "WHERE s.status = 'loaded' AND s.kind = 'registers' AND r.kind = 'sales' "
        "AND r.customer IS NOT NULL AND r.customer NOT IN (SELECT customer FROM customers) "
        'GROUP BY r.customer ORDER BY min(r.file_id), min(r."row") RETURNING customer_id',
        [run_id, utcnow()],
    ).fetchall()
    return len(added)


def read(con: duckdb.DuckDBPyConnection, open_only: bool = False) -> list[dict[str, Any]]:
    """The customers (only those waiting for a decision when `open_only`), by id, with what they
    bought: revenue in the loaded sales registers and up to three products by revenue."""
    where = "WHERE c.status IN ('pending', 'proposed')" if open_only else ""
    return fetch_dicts(
        con,
        "SELECT c.*, coalesce(sum(r.amount_inr), 0) AS revenue, count(r.customer) AS lines, "
        "list(DISTINCT r.product) FILTER (WHERE r.product IS NOT NULL) AS products "
        "FROM customers c LEFT JOIN (SELECT l.* FROM register_lines l JOIN source_files s "
        "ON s.file_id = l.file_id AND s.status = 'loaded' WHERE l.kind = 'sales') r "
        f"ON r.customer = c.customer {where} GROUP BY ALL ORDER BY c.customer_id",
    )


def list_customers(run: Run, company: str, open_only: bool = False) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return read(con, open_only)


def _decide(run: Run, company: str, row: dict[str, Any], decision: str, actor: str) -> None:
    actor, on_request = recorded(actor, decision)
    run.record(
        "mapping_decisions",
        {
            "company": company,
            "statement": None,
            "section": "customer sector",
            "raw_label": row["customer"],
            "account": row["sector"],
            "scope": "customer",
            "decision": decision,
            "actor": actor,
            "on_request": on_request,
            "confidence": row["confidence"],
            "reason": row["reason"],
        },
    )


def check_sector(sector: Any, what: str) -> str:
    """The sector as stored (trimmed). E868 when it is not text, empty, or the label for none."""
    if not isinstance(sector, str) or not sector.strip():
        raise CustomerMappingInvalid(f"{what}: sector must be text, such as 'Banking'")
    if sector.strip().lower() == UNMAPPED:
        raise CustomerMappingInvalid(
            f"{what}: {UNMAPPED!r} is what fin calls a customer with no sector"
        )
    return sector.strip()


def _check_proposal(number: int, proposal: Any, known: dict[str, dict[str, Any]]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict):
        raise CustomerMappingInvalid(
            f"{what}: expected an object with customer, sector, confidence, reason"
        )
    for field in ("customer", "sector", "confidence", "reason"):
        if field not in proposal:
            raise CustomerMappingInvalid(f"{what}: missing {field!r}")
    if not isinstance(proposal["reason"], str):
        raise CustomerMappingInvalid(f"{what}: 'reason' must be text")
    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise CustomerMappingInvalid(f"{what}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise CustomerMappingInvalid(f"{what}: confidence must be from 0 to 1")
    check_sector(proposal["sector"], what)
    row = known.get(proposal["customer"]) if isinstance(proposal["customer"], str) else None
    if row is None:
        raise CustomerMappingInvalid(
            f"{what}: {proposal['customer']!r} is not a customer of a loaded sales register "
            "(`fin map customers <company>`; customers are tokens such as CUST-0001)"
        )
    if row["status"] == "confirmed":
        raise CustomerMappingInvalid(
            f"{what}: {row['customer']} is confirmed as {row['sector']}; only the user changes it "
            "(`fin review confirm <company> <id> --customers --sector <sector>`)"
        )


def _by_customer(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    return {row["customer"]: row for row in fetch_dicts(con, "SELECT * FROM customers")}


def propose(run: Run, company: str, proposals: list[Any]) -> int:
    """Store Claude's proposals ({customer, sector, confidence, reason}); nothing is confirmed. All
    or nothing: a bad proposal (E868) stores none. Returns how many were stored."""
    with company_database(run, company, write=True) as con, transaction(con):
        known = _by_customer(con)
        for number, proposal in enumerate(proposals, start=1):
            _check_proposal(number, proposal, known)
        for proposal in proposals:
            row = known[proposal["customer"]]
            row.update(
                status="proposed",
                sector=proposal["sector"].strip(),
                confidence=float(proposal["confidence"]),
                reason=proposal["reason"],
            )
            con.execute(
                "UPDATE customers SET status = 'proposed', sector = ?, confidence = ?, reason = ?, "
                "run_id = ?, changed_at = ? WHERE customer_id = ?",
                [row["sector"], row["confidence"], row["reason"], run.run_id, utcnow()]
                + [row["customer_id"]],
            )
            _decide(run, company, row, "proposed", CLAUDE)
            if row["confidence"] < LOW_CONFIDENCE:
                run.event(
                    "map",
                    f"customer {row['customer']} proposed as {row['sector']} with confidence "
                    f"{row['confidence']}: low, review it",
                    level="warning",
                    code="E302",
                    context={"company": company, "customer_id": row["customer_id"]},
                )
    return len(proposals)


def _rows(con: duckdb.DuckDBPyConnection, ids: list[int]) -> list[dict[str, Any]]:
    """The customers with these ids, in the order given. E868 for an unknown id."""
    found = {
        row["customer_id"]: row
        for row in fetch_dicts(
            con, "SELECT * FROM customers WHERE list_contains(?::INTEGER[], customer_id)", [ids]
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise CustomerMappingInvalid(
            f"no customer with id {', '.join(missing)} (`fin map customers`)"
        )
    return [found[i] for i in ids]


def confirm(
    run: Run, company: str, ids: list[int], sector: str | None = None, *, actor: str
) -> list[dict[str, Any]]:
    """Confirm the customers' proposed sectors (or `sector` instead), all or nothing. E868 for an
    unknown id, or a customer with no proposal and no `sector`."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        rows = _rows(con, ids)
        chosen_for = {}
        for row in rows:
            what = f"customer #{row['customer_id']}"
            chosen = sector if sector is not None else row["sector"]
            if chosen is None:
                raise CustomerMappingInvalid(
                    f"{what} has no proposed sector; give one with --sector"
                )
            chosen_for[row["customer_id"]] = check_sector(chosen, what)
        for row in rows:
            chosen = chosen_for[row["customer_id"]]
            edited = chosen != row["sector"]
            row["sector"] = chosen
            con.execute(
                "UPDATE customers SET status = 'confirmed', sector = ?, run_id = ?, changed_at = ? "
                "WHERE customer_id = ?",
                [chosen, run.run_id, utcnow(), row["customer_id"]],
            )
            _decide(run, company, row, "edited" if edited else "confirmed", actor)
        return _rows(con, ids)


def reject(run: Run, company: str, ids: list[int], *, actor: str) -> list[dict[str, Any]]:
    """Reject the customers' proposals, all or nothing (E868 for an unknown id). A rejected customer
    is unmapped until a new proposal is confirmed."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        rows = _rows(con, ids)
        for row in rows:
            con.execute(
                "UPDATE customers SET status = 'rejected', sector = NULL, run_id = ?, "
                "changed_at = ? WHERE customer_id = ?",
                [run.run_id, utcnow(), row["customer_id"]],
            )
            _decide(run, company, row, "rejected", actor)
        return _rows(con, ids)


def format_customer(row: dict[str, Any]) -> str:
    products = ", ".join(row["products"] or []) or "no product"
    line = f"customer #{row['customer_id']} [{row['status']}] {row['customer']}"
    line += f": {row['lines']} sales lines ({products})"
    if row["sector"]:
        line += f" -> {row['sector']}"
        if row["status"] != "confirmed" and row["confidence"] is not None:
            line += f" (confidence {row['confidence']}): {row['reason']}"
    return line
