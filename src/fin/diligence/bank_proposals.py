"""Matches Claude proposes for what the fixed passes left unmatched (D5).

`fin reconcile <co> match --propose <file.json>` stores each proposal as `proposed`; the user
confirms or rejects it (`fin review confirm|reject <co> <ids> --matches`), as with mappings. Only a
confirmed match is applied (as pass `confirmed`) the next time `bank_vs_invoices` runs. A proposal
is a bank credit line or lines and the sales invoice line or lines they pay:

    [{"bank": [[file_id, "sheet", row]], "invoices": [[file_id, "sheet", row]],
      "confidence": 0.8, "reason": "..."}]
"""

import json
from typing import Any

import duckdb

from fin.audit.errors import MatchChangeRefused, MatchProposalInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open

_BANK = (
    "SELECT b.credit, b.currency, b.txn_date, b.remarks FROM bank_lines b JOIN source_files s "
    "USING (file_id) WHERE s.status = 'loaded' AND s.engagement_id = ? AND b.file_id = ? "
    'AND b.sheet = ? AND b."row" = ? AND b.credit > 0'
)
_INVOICE = (
    "SELECT r.amount_inr, coalesce(r.currency, 'INR'), r.invoice_date, r.customer, r.invoice_no "
    "FROM register_lines r JOIN source_files s USING (file_id) WHERE s.status = 'loaded' "
    "AND s.engagement_id = ? AND r.kind = 'sales' AND r.file_id = ? AND r.sheet = ? "
    'AND r."row" = ?'
)


def _refs(number: int, proposal: dict[str, Any], field: str) -> list[list[Any]]:
    refs = proposal.get(field)
    if not (isinstance(refs, list) and refs and all(_ref(r) for r in refs)):
        raise MatchProposalInvalid(
            f"proposal {number}: {field!r} must be a non-empty list of [file_id, sheet, row]"
        )
    return refs


def _ref(r: object) -> bool:
    """[file_id, sheet, row]."""
    return (
        isinstance(r, list)
        and len(r) == 3
        and isinstance(r[0], int)
        and isinstance(r[1], str)
        and isinstance(r[2], int)
    )


def _check_lines(
    con: duckdb.DuckDBPyConnection,
    eid: int,
    number: int,
    bank: list[list[Any]],
    invoices: list[list[Any]],
) -> None:
    """E854 when the lines are in different currencies or one is not a loaded line of its kind."""
    wanted = [(ref, _BANK, "a loaded bank credit line") for ref in bank]
    wanted += [(ref, _INVOICE, "a loaded sales invoice line") for ref in invoices]
    hits = [(ref, con.execute(sql, [eid, *ref]).fetchone(), what) for ref, sql, what in wanted]
    currencies = {found[1] for _, found, _ in hits if found}
    if len(currencies) > 1:
        raise MatchProposalInvalid(
            f"proposal {number}: the lines are in different currencies "
            f"({', '.join(sorted(currencies))}); propose one currency at a time"
        )
    for ref, found, what in hits:
        if found is None:
            raise MatchProposalInvalid(f"proposal {number}: {ref} is not {what}")


def _confidence(number: int, proposal: dict[str, Any]) -> Any:
    confidence = proposal.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, int | float)
        or not 0 <= confidence <= 1
    ):  # fmt: skip
        raise MatchProposalInvalid(f"proposal {number}: confidence must be from 0 to 1")
    return confidence


def _check_proposal(
    con: duckdb.DuckDBPyConnection, eid: int, number: int, proposal: Any
) -> tuple[list[list[Any]], list[list[Any]], Any, str]:
    """(bank refs, invoice refs, confidence, reason) of a valid proposal; E854 otherwise."""
    reason = proposal.get("reason") if isinstance(proposal, dict) else None
    if not isinstance(reason, str) or not reason:
        raise MatchProposalInvalid(f"proposal {number}: expected an object with a reason")
    bank, invoices = _refs(number, proposal, "bank"), _refs(number, proposal, "invoices")
    if any(len({tuple(r) for r in refs}) != len(refs) for refs in (bank, invoices)):
        raise MatchProposalInvalid(f"proposal {number}: a line is named twice")
    _check_lines(con, eid, number, bank, invoices)
    return bank, invoices, _confidence(number, proposal), reason


def propose(run: Run, company: str, proposals: Any) -> list[int]:
    """Store the proposals (all or nothing); returns their ids. E854 for a bad one."""
    if not isinstance(proposals, list) or not proposals:
        raise MatchProposalInvalid("expected a non-empty JSON list of match proposals")
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        eid = engagement["engagement_id"]
        rows = [_check_proposal(con, eid, n, p) for n, p in enumerate(proposals, start=1)]
        ids = []
        for bank, invoices, confidence, reason in rows:
            new = con.execute(
                "INSERT INTO match_proposals (engagement_id, bank_lines, invoice_lines, "
                "confidence, "
                "reason, status, run_id, changed_at) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?) "
                "RETURNING proposal_id",
                [
                    eid,
                    json.dumps(bank),
                    json.dumps(invoices),
                    confidence,
                    reason,
                    run.run_id,
                    utcnow(),
                ],
            ).fetchone()
            assert new is not None
            ids.append(new[0])
    return ids


def _describe(con: duckdb.DuckDBPyConnection, eid: int, row: dict[str, Any]) -> dict[str, Any]:
    bank = [con.execute(_BANK, [eid, *r]).fetchone() for r in json.loads(row["bank_lines"])]
    inv = [con.execute(_INVOICE, [eid, *r]).fetchone() for r in json.loads(row["invoice_lines"])]
    return row | {
        "bank": [
            dict(zip(("amount", "currency", "date", "remarks"), b, strict=True))
            for b in bank
            if b
        ],
        "invoices": [
            dict(zip(("amount_inr", "currency", "date", "customer", "invoice_no"), i, strict=True))
            for i in inv if i
        ],
    }  # fmt: skip


def list_proposals(run: Run, company: str, waiting: bool = True) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        where = "AND status = 'proposed'" if waiting else ""
        rows = fetch_dicts(
            con,
            f"SELECT * FROM match_proposals WHERE engagement_id = ? {where} ORDER BY proposal_id",
            [engagement["engagement_id"]],
        )
        return [_describe(con, engagement["engagement_id"], r) for r in rows]


def decide(run: Run, company: str, ids: list[int], status: str) -> list[int]:
    """Confirm or reject proposals, all or nothing. E855: unknown id, or already decided."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        found = dict(
            con.execute(
                "SELECT proposal_id, status FROM match_proposals WHERE engagement_id = ? "
                "AND list_contains(?::INTEGER[], proposal_id)",
                [engagement["engagement_id"], ids],
            ).fetchall()
        )
        for i in ids:
            if found.get(i) != "proposed":
                what = "no such match proposal" if i not in found else f"already {found[i]}"
                raise MatchChangeRefused(
                    f"match proposal {i} of {company}: {what}; nothing changed"
                )
        con.execute(
            "UPDATE match_proposals SET status = ?, run_id = ?, changed_at = ? "
            "WHERE list_contains(?::INTEGER[], proposal_id)",
            [status, run.run_id, utcnow(), ids],
        )
    return ids


def format_proposal(row: dict[str, Any]) -> str:
    bank = "; ".join(f"{b['currency']} {b['amount']} on {b['date']}" for b in row["bank"])
    inv = "; ".join(
        f"{i['invoice_no']} {i['customer']} {i['currency']} {i['amount_inr']} on {i['date']}"
        for i in row["invoices"]
    )
    conf = f" (confidence {row['confidence']})" if row["confidence"] is not None else ""
    return (
        f"match #{row['proposal_id']} [{row['status']}]{conf}: receipts {bank} <-> invoices {inv}: "
        f"{row['reason']}"
    )
