"""`fin forensic status` and `fin forensic flags`: each test's state per period, and the flags of
the latest runs, with the query each is in.

A state is 'ran' (flag count, how many at or above the threshold), 'too_few' (the count and the
minimum), 'cannot_run' (what it needs), 'stale' (the data, a mapping, the threshold or a setting it
reads changed since: the last outcome is kept beside it) or 'not_run'.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open
from fin.forensic import settings as forensic_settings
from fin.forensic.books import without_lines
from fin.forensic.registry import tests
from fin.forensic.run import test_hash
from fin.reconcile import periods_of

LATEST = """
SELECT r.* FROM forensic_runs r
WHERE r.engagement_id = ? AND r.forensic_run_id = (
    SELECT max(forensic_run_id) FROM forensic_runs x
    WHERE x.engagement_id = r.engagement_id AND x.test = r.test AND x.period = r.period)
"""


def current_hashes(
    con: duckdb.DuckDBPyConnection, root: Path, company: str, engagement: dict[str, Any]
) -> dict[str, str]:
    """test -> the input hash a run made now would have."""
    values = forensic_settings.resolve(root, company)
    books = without_lines(con, engagement)
    threshold = Decimal(engagement["query_threshold"])
    return {name: test_hash(t, values, threshold, books.hash_for(t)) for name, t in tests().items()}


def latest_runs(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any]) -> dict[tuple, dict]:
    rows = fetch_dicts(con, LATEST, [engagement["engagement_id"]])
    return {(r["test"], r["period"]): r for r in rows}


def status(run: Run, root: Path, company: str) -> list[dict[str, Any]]:
    """One row per registered test and engagement period. E803 without an open engagement."""
    with company_database(run, company) as con:
        return status_rows(con, root, company, require_open(con, company))


def status_rows(
    con: duckdb.DuckDBPyConnection, root: Path, company: str, engagement: dict[str, Any]
) -> list[dict[str, Any]]:
    """`status` on an open connection, for any engagement (the FDD report reads the latest)."""
    hashes = current_hashes(con, root, company, engagement)
    latest = latest_runs(con, engagement)
    out = []
    for name, test in tests().items():
        for period in periods_of(engagement):
            last = latest.get((name, period))
            row = {"test": name, "family": test.family, "title": test.title, "period": period}
            if last is None:
                out.append({**row, "state": "not_run", "outcome": None})
                continue
            stale = last["input_hash"] != hashes[name]
            out.append(
                {
                    **row,
                    "state": "stale" if stale else last["outcome"],
                    "outcome": last["outcome"],
                    "flagged": last["flagged"],
                    "over_threshold": last["over_threshold"],
                    "population": last["population"],
                    "minimum": last["minimum"],
                    "needs": last["needs"],
                    "not_whole": last["not_whole"],
                    "near_misses": last["near_misses"],
                    "run_id": last["run_id"],
                    "ran_at": last["created_at"],
                }
            )
    return out


def state_text(row: dict[str, Any]) -> str:
    """'ran: 3 flagged, 2 at or above the threshold', 'too few entries: 120 of 500 needed', ..."""
    outcome = row["outcome"]
    if outcome is None:
        return "not run"
    if outcome == "cannot_run":
        text = f"cannot run: needs {row['needs']}"
    elif outcome == "too_few":
        text = f"too few entries: {row['population']} of {row['minimum']} needed"
    else:
        text = f"ran: {row['flagged']} flagged, {row['over_threshold']} at or above the threshold"
        if row.get("not_whole"):
            text += f"; {row['not_whole']} vouchers not whole, left out"
        if row.get("needs"):
            text += f"; cannot run for {row['needs']}"
    return (
        f"stale (data or settings changed since; last {text})" if row["state"] == "stale" else text
    )


TUNING = """
SELECT test, period, outcome, settings, population, minimum, not_whole, flagged, over_threshold,
    near_misses
FROM forensic_runs r
WHERE engagement_id = (SELECT max(engagement_id) FROM engagement)
  AND forensic_run_id = (SELECT max(forensic_run_id) FROM forensic_runs x
      WHERE x.engagement_id = r.engagement_id AND x.test = r.test AND x.period = r.period)
ORDER BY test, period
"""


def tuning(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    """test -> period -> the latest run's counts and thresholds (`fin eval bundle`): what tuning
    the defaults needs after a client run, with no row, ledger or party (owner, 2026-11-28)."""
    out: dict[str, dict[str, Any]] = {}
    for row in fetch_dicts(con, TUNING):
        test, period = row.pop("test"), row.pop("period")
        row["thresholds"] = json.loads(row.pop("settings"))
        out.setdefault(test, {})[period] = row
    return out


# The links of a flag and of the same flag in every earlier run of its test and period (same
# source, voucher and ledger): a query stays on a flag when the test is run again.
LINKS = """
SELECT l.query_id FROM forensic_flag_queries l JOIN forensic_flags g USING (flag_id)
JOIN forensic_runs gr ON gr.forensic_run_id = g.forensic_run_id
WHERE gr.engagement_id = r.engagement_id AND g.test = f.test AND g.period = f.period
  AND g.source = f.source AND g.voucher_no IS NOT DISTINCT FROM f.voucher_no
  AND g.ledger IS NOT DISTINCT FROM f.ledger
"""
FLAGS = """
WITH shown AS (
    SELECT f.*, r.input_hash, r.run_id, r.created_at AS ran_at,
        (SELECT max(query_id) FROM ({links})) AS query_id
    FROM forensic_flags f JOIN forensic_runs r USING (forensic_run_id)
    WHERE r.forensic_run_id IN ({latest}))
SELECT s.*, q.status AS query_status FROM shown s LEFT JOIN accountant_queries q USING (query_id)
ORDER BY abs(s.amount) DESC NULLS LAST, s.flag_id
"""


def flags(
    run: Run,
    root: Path,
    company: str,
    *,
    test: str | None = None,
    period: str | None = None,
    over_threshold: bool = False,
) -> list[dict[str, Any]]:
    """The flags of the latest run of each test and period, ordered by amount (largest first; a gap,
    with none, last), each with its query (carried from the same flag of an earlier run) and
    whether its run is stale."""
    with company_database(run, company) as con:
        rows = flag_rows(con, root, company, require_open(con, company))
    return [
        row
        for row in rows
        if (not test or row["test"] == test)
        and (not period or row["period"] == period)
        and (not over_threshold or row["over_threshold"])
    ]


def flag_rows(
    con: duckdb.DuckDBPyConnection, root: Path, company: str, engagement: dict[str, Any]
) -> list[dict[str, Any]]:
    """`flags` (every one) on an open connection, for any engagement (the FDD report)."""
    hashes = current_hashes(con, root, company, engagement)
    latest = LATEST.replace("r.*", "r.forensic_run_id")
    rows = fetch_dicts(con, FLAGS.format(links=LINKS, latest=latest), [engagement["engagement_id"]])
    for row in rows:
        row["stale"] = row.pop("input_hash") != hashes.get(row["test"])
    return rows
