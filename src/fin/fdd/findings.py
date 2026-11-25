"""FDD findings (docs/fdd.md F8, table `fdd_findings` in db/schema/fdd_findings.sql).

Claude proposes a finding with evidence (`fin fdd finding propose`) and edits or drops it while it
is a draft; only the user marks it final (`fin fdd finding final`, a hook asks first) or returns a
final one to draft (`--undo`):

    draft -> final -> draft (undo), or draft -> dropped.

The amount is never typed: it comes from the linked evidence (an adjustment, a net debt item, a
reconciliation run, else a query; view `fdd_finding_rows`). The linked query's answer is the
management response. Text is masked and has no typed number and no valuation, investment-rating or
buy/don't-buy language (E970). `collect` is what the FDD report reads.
"""

from typing import Any

import duckdb

from fin.audit.errors import FddFindingInvalid, FddFindingUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.diligence.mask import Masker
from fin.report.lint import JUDGING, typed_number_lines

AREAS = ("qoe", "working_capital", "net_debt", "revenue", "walkthroughs", "provisions", "tax",
         "contingencies", "related_parties", "inquiries", "other")  # fmt: skip
IMPACTS = ("ebitda", "net_debt", "working_capital", "revenue", "disclosure", "none")
SEVERITIES = ("high", "medium", "low")
DEALS = ("price", "spa_protection", "condition_precedent", "further_work", "none")
TEXTS = ("title", "observation", "implication")
# link column -> (table, id column) checked against the open engagement
LINKS = {
    "adj_id": ("adjustments", "adj_id"),
    "netdebt_item_id": ("netdebt_items", "item_id"),
    "recon_id": ("recon_runs", "recon_id"),
    "query_id": ("accountant_queries", "query_id"),
}
AMOUNT_LINKS = ("adj_id", "netdebt_item_id", "recon_id")
CHOICES = {"area": AREAS, "impact_type": IMPACTS, "severity": SEVERITIES,
           "deal_consideration": DEALS}  # fmt: skip
ORDER = "list_position(['high', 'medium', 'low'], severity), finding_id"


def _check(con: duckdb.DuckDBPyConnection, engagement_id: int, fields: dict[str, Any]) -> None:
    """E970 for any field at fault, every problem listed."""
    problems = []
    for name, allowed in CHOICES.items():
        if name in fields and fields[name] not in allowed:
            problems.append(f"{name} {fields[name]!r} is not one of {', '.join(allowed)}")
    for name in ("title", "observation", "evidence"):
        if name in fields and not (fields[name] or "").strip():
            problems.append(f"give the {name}")
    for name in TEXTS:
        text = fields.get(name) or ""
        if typed_number_lines(text):
            problems.append(f"the {name} has a typed number (amounts come from the evidence)")
        if JUDGING.search(text):
            problems.append(f"the {name} has valuation, rating or buy/don't-buy language")
    if sum(fields.get(name) is not None for name in AMOUNT_LINKS) > 1:
        problems.append("link at most one of --adjustment, --netdebt-item, --recon")
    for name, (table, column) in LINKS.items():
        value = fields.get(name)
        if value is None:
            continue
        found = con.execute(
            f"SELECT count(*) FROM {table} WHERE {column} = ? AND engagement_id = ?",
            [value, engagement_id],
        ).fetchone()
        if not found or not found[0]:
            problems.append(f"{table} has no #{value} in the open engagement")
    if problems:
        raise FddFindingInvalid("; ".join(problems) + "; nothing stored")


def _masked(masker: Masker, fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    for name in (*TEXTS, "evidence"):
        if out.get(name):
            out[name] = masker.text(" ".join(out[name].split()))
    return out


def propose(run: Run, company: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Store a draft finding in the open engagement. E803 without one; E970 for a field at fault."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        _check(con, engagement_id, {name: None for name in LINKS} | fields)
        values = _masked(Masker(con, run.run_id), fields)
        columns = [*CHOICES, *TEXTS, "evidence", *LINKS]
        now = utcnow()
        (finding_id,) = con.execute(
            f"INSERT INTO fdd_findings (engagement_id, {', '.join(columns)}, status, "
            "created_run_id, created_at, status_run_id, status_at) VALUES "
            f"(?, {', '.join('?' for _ in columns)}, 'draft', ?, ?, ?, ?) RETURNING finding_id",
            [engagement_id, *(values.get(c) for c in columns), run.run_id, now, run.run_id, now],
        ).fetchone()
        _log(run, company, finding_id, f"proposed ({fields['severity']}, {fields['area']})")
        return _rows(con, engagement_id, [finding_id])[0]


def update(run: Run, company: str, finding_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    """Change a draft's fields; a link given replaces the amount link. E971 unless it is a draft."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        row = _one(con, engagement_id, finding_id, "draft", "updated", company)
        if any(fields.get(name) is not None for name in AMOUNT_LINKS):
            fields = {name: None for name in AMOUNT_LINKS} | fields
        _check(con, engagement_id, {name: row[name] for name in LINKS} | fields)
        values = _masked(Masker(con, run.run_id), fields)
        if values:
            sets = ", ".join(f"{name} = ?" for name in values)
            con.execute(
                f"UPDATE fdd_findings SET {sets}, status_run_id = ?, status_at = ? "
                "WHERE finding_id = ?",
                [*values.values(), run.run_id, utcnow(), finding_id],
            )
        _log(run, company, finding_id, "updated " + ", ".join(sorted(values)))
        return _rows(con, engagement_id, [finding_id])[0]


def set_status(run: Run, company: str, ids: list[int], status: str) -> list[dict[str, Any]]:
    """Move findings, all or nothing: draft -> dropped (Claude), draft -> final or final -> draft
    (the user's word only). E971 for an id not in the open engagement or in the wrong state."""
    before = {"dropped": "draft", "final": "draft", "draft": "final"}[status]
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        for finding_id in ids:
            _one(con, engagement_id, finding_id, before, f"made {status}", company)
        for finding_id in ids:
            con.execute(
                "UPDATE fdd_findings SET status = ?, status_run_id = ?, status_at = ? "
                "WHERE finding_id = ?",
                [status, run.run_id, utcnow(), finding_id],
            )
            _log(run, company, finding_id, f"{before} -> {status}")
        return _rows(con, engagement_id, ids)


def _one(
    con: duckdb.DuckDBPyConnection,
    engagement_id: int,
    finding_id: int,
    state: str,
    what: str,
    company: str,
) -> dict[str, Any]:
    rows = _rows(con, engagement_id, [finding_id])
    if not rows or rows[0]["status"] != state:
        found = rows[0]["status"] if rows else "not in the open engagement"
        raise FddFindingUnknown(
            f"FDD finding #{finding_id} cannot be {what} ({found}; it must be {state}); nothing "
            f"changed (`fin fdd finding list {company} --all`)"
        )
    return rows[0]


def _log(run: Run, company: str, finding_id: int, what: str) -> None:
    run.event(
        "fdd",
        f"FDD finding #{finding_id}: {what}",
        context={"company": company, "finding_id": finding_id},
    )


def _rows(
    con: duckdb.DuckDBPyConnection, engagement_id: int, ids: list[int] | None = None
) -> list[dict[str, Any]]:
    where = " AND list_contains(?::INTEGER[], finding_id)" if ids is not None else ""
    return fetch_dicts(
        con,
        f"SELECT * FROM fdd_finding_rows WHERE engagement_id = ?{where} ORDER BY {ORDER}",
        [engagement_id] + ([ids] if ids is not None else []),
    )


def collect(
    con: duckdb.DuckDBPyConnection, engagement_id: int, drafts: bool = False
) -> list[dict[str, Any]]:
    """The FDD report's findings: final ones (and drafts with `drafts`), by severity then id,
    with amount, amount_source and management_response. Dropped ones never."""
    wanted = ("final", "draft") if drafts else ("final",)
    return [r for r in _rows(con, engagement_id) if r["status"] in wanted]


def list_findings(run: Run, company: str, every: bool = False) -> list[dict[str, Any]]:
    """The open engagement's findings; dropped ones only with `every`. E803 without one."""
    with company_database(run, company) as con:
        rows = _rows(con, require_open(con, company)["engagement_id"])
    return [r for r in rows if every or r["status"] != "dropped"]


def format_finding(row: dict[str, Any]) -> str:
    amount = (
        f"{rupees(row['amount'])} from {row['amount_source']}"
        if row["amount"] is not None
        else "no amount"
    )
    lines = [
        f"FDD finding #{row['finding_id']} [{row['status']}] {row['severity']} "
        f"{row['area']}: {row['title']}",
        f"  {row['observation']}",
        f"  impact {row['impact_type']}, {amount}; deal consideration {row['deal_consideration']}",
        f"  evidence: {row['evidence']}",
    ]
    if row["implication"]:
        lines.insert(2, f"  implication: {row['implication']}")
    if row["query_id"] is not None:
        answer = row["management_response"] or f"no answer yet ({row['query_status']})"
        lines.append(f"  management response (query #{row['query_id']}): {answer}")
    return "\n".join(lines)
