"""Figures the user states (`fin fact state|unstate|list`; docs/product.md, "Figures the user
states").

A stated figure is the user's statement, not a document's: a nil for a line the documents do not
have (no finance cost line, so EBITDA could not be computed), or a value from a source fin cannot
hold. It is stored in `stated_figures` with read_by 'user', the reason, the run and the time.
`current_facts` uses it only while no loaded document gives the same account, period and scope, so
the metrics compute from it and a document loaded later takes its place.

Stating a figure a loaded document already gives stores nothing and changes no figure: that is a
conflict, recorded as a finding (check_name 'stated_figure', no doc_id, so it never holds or
releases a document) with both values, for the user to decide. Withdrawing (`unstate`) keeps the
row, marked withdrawn. Every change copies the company database to `backups/<run_id>/` first and is
appended to the audit table `stated_figures`. Inside a diligence engagement a stated figure is
allowed (specs Q3); it changes `current_facts`, which is an input of gate G1, so G1 reopens.

A figure fin read wrongly is corrected with `fin fact correct` (`correct`): facts.value takes the
new value, the cell or page and the text as printed stay in raw_values, and `fact_corrections`
keeps the value before, the reason, the actor (`fin.actor`, passed in by the interface: 'user', or
'ai:claude' with on_request when Claude ran it on the user's word) and the run. A stated figure
records its actor the same way. It is for fin's own misread, never to make figures tie: inside a
diligence engagement a figure under a query or an adjustment that is not closed is refused (E409),
because a difference is the company's and is queried. A held document is checked again after it.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb

from fin.actor import is_ai, label
from fin.audit.errors import (
    DependencyMissing,
    FigureCannotBeCorrected,
    FigureCannotBeStated,
    StatedFigureUnknown,
)
from fin.audit.logger import Run
from fin.config import SCOPES, load_config
from fin.db.conn import connect, no_company_message, transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.findings import recheck_held, record
from fin.ingest_read import MAX_RUPEES  # as E890: a figure this large is a wrong unit
from fin.map.dictionary import load_accounts
from fin.metrics.export import refresh_analytics
from fin.paths import company_db_path
from fin.query.write import backup

# FY25, FY26-Q1 ... FY26-Q4, FY26-H1, FY26-H2, FY26-9M (fin.extract.periods).
PERIOD = re.compile(r"^FY(\d{2})(?:-(Q[1-4]|H[12]|9M))?$")
LENGTH = {None: 12, "Q": 3, "H": 6, "9": 9}
# Commas only as thousands separators: 1,234,567 or the Indian 12,34,567 (never "1,2,3").
GROUPED = (
    re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?$"),
    re.compile(r"^[+-]?\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d*)?$"),
)

# The loaded document's figure for an account, period and scope, where it was printed, and the
# stated value minus it.
_DOCUMENT_SQL = """
SELECT c.fact_id, c.value, d.file_name, source_ref(r.sheet, r.cell, r.page) AS source,
       CAST(? AS DECIMAL(20, 2)) - c.value AS difference
FROM current_facts c
JOIN facts f ON f.fact_id = c.fact_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = c.doc_id
WHERE c.doc_id IS NOT NULL AND c.account = ? AND c.period = ? AND c.scope = ?
"""

_LIST_SQL = """
SELECT stated_id, account, period, scope, value, reason, read_by, in_use, run_id, stated_at
FROM stated_now ORDER BY scope, account, period_end, period_months DESC, stated_id
"""


@dataclass(frozen=True)
class StatedFigure:
    """A figure as the user states it, checked: `period_months`, `period_end` and
    `fiscal_year` follow from `period`."""

    account: str
    statement: str  # the account's statement (balance_sheet, profit_and_loss, cash_flow)
    period: str
    period_months: int
    period_end: date
    fiscal_year: str
    scope: str
    value: Decimal
    reason: str


def period_dates(period: str, start_month: int) -> tuple[str, int, date]:
    """(fiscal year, months, period end) of a period name, for a fiscal year starting in
    `start_month`: 'FY26-H1' with April -> ('FY26', 6, 2025-09-30). E407 for any other name."""
    match = PERIOD.match(period)
    if match is None:
        raise FigureCannotBeStated(
            f"period {period!r}: use FY25, FY26-Q1 to FY26-Q4, FY26-H1, FY26-H2 or FY26-9M. "
            "Nothing stated"
        )
    part = match.group(2)
    months = LENGTH[part[0] if part else None]
    count = 1 if part is None or part == "9M" else int(part[1])  # which quarter or half
    elapsed = 9 if part == "9M" else months * count
    end_year = 2000 + int(match.group(1))
    first_year = end_year - 1 if start_month > 1 else end_year
    month_index = start_month - 1 + elapsed  # months from January of first_year to the day after
    after = date(first_year + month_index // 12, month_index % 12 + 1, 1)
    return f"FY{match.group(1)}", months, after - timedelta(days=1)


def parse_value(text: str) -> Decimal:
    """The value in rupees as given: a number with at most two decimals, below 10^16. E407."""
    plain = text.strip()
    if "," in plain and not any(form.match(plain) for form in GROUPED):
        raise FigureCannotBeStated(
            f"value {text!r}: commas only between digit groups (1,23,456 or 123,456). Nothing "
            "stated"
        )
    try:
        value = Decimal(plain.replace(",", ""))
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        raise FigureCannotBeStated(f"value {text!r} is not a number. Nothing stated")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise FigureCannotBeStated(
            f"value {text!r} has more than two decimals: give it in rupees and paise. Nothing "
            "stated"
        )
    if abs(value) >= MAX_RUPEES:
        raise FigureCannotBeStated(
            f"value {text!r} is 10^16 rupees or more (a wrong unit?): give it in rupees. Nothing "
            "stated"
        )
    return value


def check(
    root: Path, account: str, period: str, scope: str, value: str, reason: str
) -> StatedFigure:
    """Check what the user states against the chart of accounts and config.toml. E407."""
    accounts = load_accounts(root)
    if account not in accounts:
        raise FigureCannotBeStated(
            f"account {account!r} is not in the chart of accounts (`fin map accounts` lists them). "
            "Nothing stated"
        )
    if scope not in SCOPES:
        raise FigureCannotBeStated(f"scope {scope!r}: use {' or '.join(SCOPES)}. Nothing stated")
    if not reason.strip():
        raise FigureCannotBeStated("give the reason: why the figure is stated. Nothing stated")
    fiscal_year, months, period_end = period_dates(
        period, load_config(root).fiscal_year_start_month
    )
    return StatedFigure(
        account,
        accounts[account].statement,
        period,
        months,
        period_end,
        fiscal_year,
        scope,
        parse_value(value),
        reason.strip(),
    )


def _database(root: Path, company: str) -> Path:
    path = company_db_path(root, company)
    if not path.is_file():
        raise DependencyMissing(no_company_message(company))
    ensure_database(path, "company")
    return path


def _refuse_twice(con: duckdb.DuckDBPyConnection, s: StatedFigure) -> None:
    row = con.execute(
        "SELECT stated_id, value FROM stated_figures WHERE status = 'stated' AND account = ? "
        "AND period = ? AND scope = ?",
        [s.account, s.period, s.scope],
    ).fetchone()
    if row is not None:
        raise FigureCannotBeStated(
            f"{s.account} {s.period} {s.scope} is already stated (#{row[0]}, {row[1]}): withdraw "
            f"it first (`fin fact unstate <company> {row[0]}`). Nothing stated"
        ).with_context(stated_id=row[0], value=str(row[1]))


def _finding_scope(s: StatedFigure) -> tuple[str, list[Any]]:
    """The findings of a statement's conflict check (`where` and its parameters)."""
    where = "check_name = 'stated_figure' AND scope = ? AND period = ? AND label = ?"
    return where, [s.scope, s.period, s.account]


def _finding(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, s: StatedFigure, doc: dict[str, Any]
) -> int:
    """Record the conflict with the loaded document's figure as a finding; its id."""
    where, params = _finding_scope(s)
    row = {
        "file_name": doc["file_name"],
        "check_name": "stated_figure",
        "finding": "document_gives",
        "statement": s.statement,
        "scope": s.scope,
        "period": s.period,
        "label": s.account,
        "fin_value": doc["value"],
        "other_value": s.value,
        "difference": doc["difference"],
        "fin_source": doc["source"],
        "other_source": f"stated by the user: {s.reason}",
        "fact_id": doc["fact_id"],
        "read_by": "user",
        "message": (
            f"{s.account} {s.period} {s.scope} was stated by the user, but {doc['file_name']} "
            "gives it: a stated figure never replaces a document's, so nothing was stated"
        ),
    }
    record(run, company, con, [row], where, params)
    found = con.execute(
        f"SELECT max(finding_id) FROM findings WHERE {where} AND status IN ('open', 'accepted')",
        params,
    ).fetchone()
    assert found is not None
    return int(found[0])


def _log(run: Run, company: str, action: str, s: dict[str, Any], saved: Path) -> None:
    run.record(
        "stated_figures",
        {
            "company": company,
            "action": action,
            "stated_id": s.get("stated_id"),
            "finding_id": s.get("finding_id"),
            "account": s["account"],
            "period": s["period"],
            "scope": s["scope"],
            "value": s["value"],
            "reason": s.get("reason"),
            "actor": s["actor"],
            "backup_path": saved.relative_to(run.root).as_posix(),
        },
    )
    run.event("facts", f"{company}: {action} {s['account']} {s['period']} {s['scope']}")


def state(
    run: Run,
    company: str,
    account: str,
    period: str,
    scope: str,
    value: str,
    reason: str,
    *,
    actor: str,
) -> dict[str, Any]:
    """Store a figure the user states, or, when a loaded document gives it, record a finding and
    store nothing. Returns what happened: outcome 'stated' (with stated_id) or 'finding' (with
    finding_id and the document's figure). E407 when it cannot be stated; E001 with no company
    database."""
    s = check(run.root, account, period, scope, value, reason)
    path = _database(run.root, company)
    saved = backup(run.root, path, run)
    out: dict[str, Any] = {
        "company": company,
        "account": s.account,
        "period": s.period,
        "scope": s.scope,
        "value": s.value,
        "reason": s.reason,
        "actor": actor,
    }
    with connect(path, write=True) as con, transaction(con):
        _refuse_twice(con, s)
        docs = fetch_dicts(con, _DOCUMENT_SQL, [s.value, s.account, s.period, s.scope])
        if docs:
            doc = docs[0]
            out |= {
                "outcome": "finding",
                "finding_id": _finding(run, company, con, s, doc),
                "document": {k: doc[k] for k in ("file_name", "source", "value", "fact_id")},
            }
        else:
            [(stated_id,)] = con.execute(
                "INSERT INTO stated_figures (account, fiscal_year, period, period_months, "
                "period_end, scope, value, reason, actor, status, run_id, stated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'stated', ?, ?) RETURNING stated_id",
                [
                    s.account,
                    s.fiscal_year,
                    s.period,
                    s.period_months,
                    s.period_end,
                    s.scope,
                    s.value,
                    s.reason,
                    actor,
                    run.run_id,
                    utcnow(),
                ],
            ).fetchall()
            out |= {"outcome": "stated", "stated_id": stated_id}
            # no loaded document gives it any more: an earlier conflict finding is resolved
            record(run, company, con, [], *_finding_scope(s))
    _log(run, company, out["outcome"], out, saved)
    refresh = refresh_analytics(run)  # metrics are views; this rebuilds the dashboards' copy
    return out | {"backup_path": saved.relative_to(run.root).as_posix(), "refresh": refresh}


def unstate(run: Run, company: str, stated_id: int, *, actor: str) -> dict[str, Any]:
    """Withdraw a stated figure: the row stays, marked withdrawn, and current_facts no longer has
    it. E408 for an id that is not a stated figure of the company."""
    path = _database(run.root, company)
    saved = backup(run.root, path, run)
    with connect(path, write=True) as con, transaction(con):
        rows = fetch_dicts(
            con,
            "UPDATE stated_figures SET status = 'withdrawn', withdrawn_run_id = ?, "
            "withdrawn_at = ? WHERE stated_id = ? AND status = 'stated' "
            "RETURNING stated_id, account, period, scope, value, reason",
            [run.run_id, utcnow(), stated_id],
        )
        if not rows:
            raise StatedFigureUnknown(
                f"{company} has no stated figure #{stated_id} (unknown, or already withdrawn); "
                f"nothing changed. Stated figures: `fin fact list {company}`"
            ).with_context(stated_id=stated_id)
    _log(run, company, "withdrawn", rows[0] | {"actor": actor}, saved)
    refresh = refresh_analytics(run)
    return {"company": company, "outcome": "withdrawn", **rows[0], "refresh": refresh}


def list_stated(root: Path, company: str) -> list[dict[str, Any]]:
    """The company's stated figures not withdrawn, with their reasons and whether they are in use
    (false while a loaded document gives the same figure)."""
    path = _database(root, company)
    with connect(path) as con:
        return fetch_dicts(con, _LIST_SQL, [])


def format_stated(row: dict[str, Any]) -> str:
    use = "in use" if row["in_use"] else "not in use: a loaded document gives it"
    return (
        f"#{row['stated_id']} {row['account']} {row['period']} {row['scope']} = "
        f"₹{row['value']:,} stated by the user ({use}): {row['reason']}"
    )


# The fact to correct, with its document's status and where it was read.
_FACT_SQL = """
SELECT f.fact_id, f.doc_id, f.account, f.period, f.scope, f.value, d.status, d.file_name,
       source_ref(r.sheet, r.cell, r.page) AS source, r.raw_value_text, r.raw_unit
FROM facts f
JOIN documents d ON d.doc_id = f.doc_id
JOIN raw_values r ON r.raw_id = f.raw_id
WHERE f.fact_id = ?
"""

# Inside an open engagement, the queries (not closed or withdrawn) and adjustments (not rejected)
# about the fact: through a finding on it, or for an adjustment its account, period and scope.
_UNDER_QUERY_SQL = """
SELECT 'query #' || q.query_id FROM accountant_queries q
JOIN engagement e ON e.engagement_id = q.engagement_id AND e.status = 'open'
JOIN findings x ON x.finding_id = q.finding_id
WHERE q.status NOT IN ('closed', 'withdrawn') AND x.fact_id = ?
UNION ALL
SELECT 'adjustment #' || a.adj_id FROM adjustments a
JOIN engagement e ON e.engagement_id = a.engagement_id AND e.status = 'open'
LEFT JOIN findings x ON x.finding_id = a.finding_id
WHERE a.status <> 'rejected'
    AND (x.fact_id = ? OR (a.account = ? AND a.period = ? AND a.scope = ?))
"""

_CORRECTED_SQL = """
SELECT correction_id, fact_id, account, period, scope, original_value, value, reason, actor,
       on_request, run_id, corrected_at, file_name, source
FROM corrected_now ORDER BY correction_id
"""


def _fact_to_correct(con: duckdb.DuckDBPyConnection, company: str, fact_id: int) -> dict[str, Any]:
    found = fetch_dicts(con, _FACT_SQL, [fact_id])
    if not found or found[0]["status"] == "superseded":
        why = "has no fact" if not found else "has only a superseded document's fact"
        raise FigureCannotBeCorrected(
            f"{company} {why} #{fact_id}; nothing corrected. Find the fact with `fin audit` or "
            "`fin sql` (fact_id)"
        ).with_context(fact_id=fact_id)
    fact = found[0]
    under = [name for (name,) in con.execute(_UNDER_QUERY_SQL, [fact_id, fact_id, fact["account"],
             fact["period"], fact["scope"]]).fetchall()]  # fmt: skip
    if under:
        raise FigureCannotBeCorrected(
            f"fact #{fact_id} ({fact['account']} {fact['period']} {fact['scope']}) is under "
            f"{', '.join(under)} of the open engagement: a difference is queried, never fixed. "
            "Nothing corrected"
        ).with_context(fact_id=fact_id, under=under)
    return fact


def correct(
    run: Run, company: str, fact_id: int, value: str, reason: str, *, actor: str
) -> dict[str, Any]:
    """Correct a figure fin read wrongly, on the user's word: facts.value becomes `value` (rupees,
    rupees per share for EPS), raw_values is untouched, and fact_corrections keeps the value
    before, the reason and the actor. Backs up and logs first, then checks held documents again
    (and the fact's document) and refreshes the analytics. E409 when it cannot be corrected."""
    if not reason.strip():
        raise FigureCannotBeCorrected("give the reason: what fin misread. Nothing corrected")
    try:
        new = parse_value(value)
    except FigureCannotBeStated as error:
        raise FigureCannotBeCorrected(
            error.message.replace("Nothing stated", "Nothing corrected")
        ) from error
    path = _database(run.root, company)
    with connect(path) as con:
        fact = _fact_to_correct(con, company, fact_id)
    if fact["value"] == new:
        raise FigureCannotBeCorrected(
            f"fact #{fact_id} already has the value {new}; nothing corrected"
        ).with_context(fact_id=fact_id)
    on_request = is_ai(actor)  # an AI corrects only because the user asked
    saved = backup(run.root, path, run)
    with connect(path, write=True) as con, transaction(con):
        fact = _fact_to_correct(con, company, fact_id)  # again, inside the transaction
        con.execute("UPDATE facts SET value = ? WHERE fact_id = ?", [new, fact_id])
        [(correction_id,)] = con.execute(
            "INSERT INTO fact_corrections (fact_id, doc_id, original_value, value, reason, actor, "
            "on_request, run_id, corrected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING correction_id",
            [
                fact_id,
                fact["doc_id"],
                fact["value"],
                new,
                reason.strip(),
                actor,
                on_request,
                run.run_id,
                utcnow(),
            ],
        ).fetchall()
    out = {
        "company": company,
        "outcome": "corrected",
        "correction_id": correction_id,
        "fact_id": fact_id,
        "account": fact["account"],
        "period": fact["period"],
        "scope": fact["scope"],
        "original_value": fact["value"],
        "value": new,
        "reason": reason.strip(),
        "actor": actor,
        "on_request": on_request,
        "source": f"{fact['file_name']} {fact['source']}",
        "printed": f"{fact['raw_value_text']} {fact['raw_unit']}",
        "backup_path": saved.relative_to(run.root).as_posix(),
    }
    run.record(
        "fact_corrections",
        {k: out[k] for k in ("company", "correction_id", "fact_id", "account", "period", "scope")}
        | {k: out[k] for k in ("original_value", "value", "reason", "actor", "on_request")}
        | {"backup_path": out["backup_path"]},
    )
    run.event("facts", f"{company}: corrected fact {fact_id} ({fact['account']} {fact['period']})")
    out["held_documents_rechecked"] = recheck_held(run, company, [fact["doc_id"]])
    out["refresh"] = refresh_analytics(run)
    return out


def list_corrected(root: Path, company: str) -> list[dict[str, Any]]:
    """The corrections in force (the latest per fact) in loaded and held documents."""
    with connect(_database(root, company)) as con:
        return fetch_dicts(con, _CORRECTED_SQL, [])


def format_corrected(row: dict[str, Any]) -> str:
    return (
        f"correction #{row['correction_id']}: fact {row['fact_id']} {row['account']} "
        f"{row['period']} {row['scope']} ({row['file_name']} {row['source']}) "
        f"{row['original_value']} -> {row['value']}, corrected by {label(row['actor'])}: "
        f"{row['reason']}"
    )
