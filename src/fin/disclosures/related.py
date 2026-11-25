"""Related party links (`fin disclosures related`; `fin review ... --related`).

fin matches each related party locally: a counterparty whose name a loaded file already had gets
that file's token on load, so a party that is the same token as a sales customer, a purchase vendor
or a ledger is matched (view `related_party_matches`, status 'matched'); real names are never
printed. For the rest Claude proposes links by token (`propose`: {party, kind, target,
confidence, reason}); nothing is applied. The user confirms or rejects them in review; only
confirmed links count.

Every proposal and decision is appended to the audit table `mapping_decisions` (scope
'related_party').
"""

from typing import Any

import duckdb

from fin.audit.errors import RelatedPartyLinkInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open
from fin.map.review import _current_actor

LINK_KINDS = ("customer", "vendor", "ledger")
TARGETS = {
    "customer": "SELECT r.customer FROM register_lines r JOIN source_files f USING (file_id) "
    "WHERE f.engagement_id = ? AND f.status = 'loaded' AND r.kind = 'sales'",
    "vendor": "SELECT r.vendor FROM register_lines r JOIN source_files f USING (file_id) "
    "WHERE f.engagement_id = ? AND f.status = 'loaded' AND r.kind = 'purchase'",
    "ledger": "SELECT t.ledger FROM trial_balance t JOIN source_files f USING (file_id) "
    "WHERE f.engagement_id = ? AND f.status = 'loaded' AND t.row_type = 'ledger' "
    "UNION SELECT b.ledger FROM ledger_blocks b JOIN source_files f USING (file_id) "
    "WHERE f.engagement_id = ? AND f.status = 'loaded'",
}


def _targets(con: duckdb.DuckDBPyConnection, engagement_id: int, kind: str) -> set[str]:
    params = [engagement_id] * TARGETS[kind].count("?")
    return {value for (value,) in con.execute(TARGETS[kind], params).fetchall() if value}


def _parties(con: duckdb.DuckDBPyConnection, engagement_id: int) -> set[str]:
    rows = con.execute(
        "SELECT party FROM related_parties WHERE engagement_id = ?", [engagement_id]
    ).fetchall()
    return {party for (party,) in rows}


def _check(number: int, proposal: Any, parties: set[str], targets: dict[str, set[str]]) -> None:
    what = f"proposal {number}"
    if not isinstance(proposal, dict):
        raise RelatedPartyLinkInvalid(
            f"{what}: expected an object with party, kind, target, confidence, reason"
        )
    for field in ("party", "kind", "target", "confidence", "reason"):
        if field not in proposal:
            raise RelatedPartyLinkInvalid(f"{what}: missing {field!r}")
    if not isinstance(proposal["reason"], str) or not proposal["reason"].strip():
        raise RelatedPartyLinkInvalid(f"{what}: 'reason' must be text")
    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise RelatedPartyLinkInvalid(f"{what}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise RelatedPartyLinkInvalid(f"{what}: confidence must be from 0 to 1")
    if proposal["party"] not in parties:
        raise RelatedPartyLinkInvalid(
            f"{what}: {proposal['party']!r} is not a related party of the open engagement "
            "(`fin disclosures related <company>` lists them by token)"
        )
    kind = proposal["kind"]
    if kind not in LINK_KINDS:
        raise RelatedPartyLinkInvalid(f"{what}: kind must be one of {', '.join(LINK_KINDS)}")
    if proposal["target"] not in targets[kind]:
        raise RelatedPartyLinkInvalid(
            f"{what}: the target is not a {kind} of the engagement's loaded files (customers and "
            "vendors are tokens; ledgers as `fin map ledgers` lists them)"
        )
    if proposal["target"] == proposal["party"]:
        raise RelatedPartyLinkInvalid(f"{what}: fin already matched {proposal['party']} by token")


def _record(run: Run, company: str, row: dict[str, Any], decision: str) -> None:
    actor, on_request = ("claude", False) if decision == "proposed" else _current_actor()
    run.record(
        "mapping_decisions",
        {
            "company": company,
            "statement": None,
            "section": f"related party {row['link_kind']}",
            "raw_label": row["party"],
            "account": row["target"],
            "scope": "related_party",
            "decision": decision,
            "actor": actor,
            "on_request": on_request,
            "confidence": row["confidence"],
            "reason": row["reason"],
        },
    )


def propose(run: Run, company: str, proposals: list[Any]) -> int:
    """Store Claude's link proposals; nothing is confirmed. All or nothing (E941). A link the user
    rejected can be proposed again; a confirmed one is left to the user."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        parties = _parties(con, engagement_id)
        targets = {kind: _targets(con, engagement_id, kind) for kind in LINK_KINDS}
        for number, proposal in enumerate(proposals, start=1):
            _check(number, proposal, parties, targets)
        for proposal in proposals:
            row = {
                "party": proposal["party"],
                "link_kind": proposal["kind"],
                "target": proposal["target"],
                "confidence": float(proposal["confidence"]),
                "reason": proposal["reason"].strip(),
            }
            con.execute(
                "INSERT INTO related_party_links (engagement_id, party, link_kind, target, status, "
                "confidence, reason, run_id, changed_at) "
                "VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?) "
                "ON CONFLICT (engagement_id, party, link_kind, target) DO UPDATE SET "
                "status = 'proposed', confidence = excluded.confidence, reason = excluded.reason, "
                "run_id = excluded.run_id, changed_at = excluded.changed_at "
                "WHERE related_party_links.status <> 'confirmed'",
                [engagement_id, *row.values(), run.run_id, utcnow()],
            )
            _record(run, company, row, "proposed")
    return len(proposals)


def _rows(con: duckdb.DuckDBPyConnection, engagement_id: int, ids: list[int]) -> list[Any]:
    """The open engagement's links with these ids, in the order given (E941 for any other id)."""
    found = {
        row["link_id"]: row
        for row in fetch_dicts(
            con,
            "SELECT * FROM related_party_links WHERE engagement_id = ? "
            "AND list_contains(?::INTEGER[], link_id)",
            [engagement_id, ids],
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise RelatedPartyLinkInvalid(
            f"no related party link with id {', '.join(missing)} in the open engagement "
            "(`fin review list <co> --related --all`)"
        )
    return [found[i] for i in ids]


def decide(run: Run, company: str, ids: list[int], status: str) -> list[dict[str, Any]]:
    """Confirm or reject links of the open engagement (status), all or nothing. E803 without an
    open engagement; E941 for an id not in it. Only the user."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        for row in _rows(con, engagement_id, ids):
            con.execute(
                "UPDATE related_party_links SET status = ?, run_id = ?, changed_at = ? "
                "WHERE link_id = ?",
                [status, run.run_id, utcnow(), row["link_id"]],
            )
            _record(run, company, row, status)
        return _rows(con, engagement_id, ids)


def read_links(con: duckdb.DuckDBPyConnection, engagement_id: int, waiting: bool) -> list[Any]:
    where = "AND status = 'proposed'" if waiting else ""
    return fetch_dicts(
        con,
        f"SELECT * FROM related_party_links WHERE engagement_id = ? {where} ORDER BY link_id",
        [engagement_id],
    )


def list_links(run: Run, company: str, waiting: bool = True) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return read_links(con, require_open(con, company)["engagement_id"], waiting)


def parties(run: Run, company: str) -> list[dict[str, Any]]:
    """Each related party with what fin matched and the user confirmed (`links`)."""
    with company_database(run, company) as con:
        engagement_id = require_open(con, company)["engagement_id"]
        return fetch_dicts(
            con,
            "SELECT p.party, list(m.link_kind || ' ' || m.target || ' (' || m.status || ')' "
            "ORDER BY m.link_kind, m.target) FILTER (WHERE m.target IS NOT NULL) AS links "
            "FROM related_parties p LEFT JOIN related_party_matches m "
            "ON m.engagement_id = p.engagement_id AND m.party = p.party "
            "WHERE p.engagement_id = ? GROUP BY p.party ORDER BY p.party",
            [engagement_id],
        )


def format_link(row: dict[str, Any]) -> str:
    return (
        f"link #{row['link_id']} [{row['status']}] {row['party']} -> {row['link_kind']} "
        f"{row['target']} (confidence {row['confidence']}): {row['reason']}"
    )


def format_party(row: dict[str, Any]) -> str:
    links = ", ".join(row["links"] or []) or "not matched: Claude proposes a link"
    return f"{row['party']}: {links}"
