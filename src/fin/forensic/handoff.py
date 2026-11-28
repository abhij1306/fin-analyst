"""`fin query raise <co> --flags 12,13 --question ...`: one draft query for forensic flags.

The flags must be in the latest run of their test and period, on data and settings that have not
changed since (E992), at or above the engagement's query threshold (a voucher gap has no amount and
can be raised) and in no query that is not withdrawn (its own, or one on the same flag in an
earlier run), and of one period (E993). The query's amount
is the flags' amounts added up without their signs; its sources the flags' sources;
forensic_flag_queries links the flags, so each flag shows its query. Claude drafts the wording; the
user approves it like any query.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import ForensicFlagNotQueryable, ForensicFlagUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open
from fin.diligence.queries import draft_in
from fin.forensic.status import LINKS, current_hashes

SHOWN = 10  # flags whose sources a query names; the rest are counted

_FLAGS = """
SELECT f.*, r.engagement_id, r.input_hash,
    r.forensic_run_id = (SELECT max(forensic_run_id) FROM forensic_runs x
        WHERE x.engagement_id = r.engagement_id AND x.test = r.test AND x.period = r.period)
        AS is_latest,
    EXISTS (SELECT 1 FROM accountant_queries q WHERE q.status <> 'withdrawn'
        AND q.query_id IN ({links})) AS in_query
FROM forensic_flags f JOIN forensic_runs r USING (forensic_run_id)
WHERE f.flag_id IN ({marks})
"""


def _ids(ids: list[int]) -> str:
    return ", ".join(f"#{i}" for i in ids)


def raise_from_flags(
    run: Run, root: Path, company: str, ids: list[int], question: str
) -> dict[str, Any]:
    """A draft query (origin 'forensic') for the flags. E992 for an unknown, superseded or stale
    flag; E993 for one below the threshold, already in a query, or flags of several periods."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        marks = ", ".join("?" for _ in ids)
        found = {
            r["flag_id"]: r for r in fetch_dicts(con, _FLAGS.format(links=LINKS, marks=marks), ids)
        }
        unknown = [
            i
            for i in ids
            if i not in found or found[i]["engagement_id"] != engagement["engagement_id"]
        ]
        if unknown:
            raise ForensicFlagUnknown(
                f"flags {_ids(unknown)} are not flags of {company}'s open engagement; nothing "
                f"raised. Take the ids from `fin forensic flags {company}`"
            ).with_context(flags=unknown)
        hashes = current_hashes(con, root, company, engagement)
        old = [i for i in ids if not found[i]["is_latest"]]
        stale = [
            i
            for i in ids
            if i not in old and found[i]["input_hash"] != hashes.get(found[i]["test"])
        ]
        if old or stale:
            raise ForensicFlagUnknown(
                f"flags {_ids(old + stale)} are not in a current run of their test (it was run "
                f"again, or a file, mapping or setting changed since); nothing raised. Run `fin "
                f"forensic run {company}` and take the ids from `fin forensic flags {company}`"
            ).with_context(flags=old + stale)
        flags = [found[i] for i in ids]
        below = [f["flag_id"] for f in flags if not f["over_threshold"]]
        if below:
            raise ForensicFlagNotQueryable(
                f"flags {_ids(below)} are below the query threshold "
                f"({engagement['query_threshold']}); nothing raised"
            ).with_context(flags=below)
        taken = [f["flag_id"] for f in flags if f["in_query"]]
        if taken:
            raise ForensicFlagNotQueryable(
                f"flags {_ids(taken)} are already in a query that is not withdrawn; nothing raised"
            ).with_context(flags=taken)
        periods = sorted({f["period"] for f in flags})
        if len(periods) > 1:
            raise ForensicFlagNotQueryable(
                f"the flags are of several periods ({', '.join(periods)}); raise one query per "
                "period. Nothing raised"
            ).with_context(periods=periods)
        amounts = [abs(f["amount"]) for f in flags if f["amount"] is not None]
        more = f"; and {len(flags) - SHOWN} more flags" if len(flags) > SHOWN else ""
        sources = "; ".join(dict.fromkeys(f["source"] for f in flags[:SHOWN])) + more
        query = draft_in(
            run,
            company,
            con,
            engagement,
            {
                "origin": "forensic",
                "finding_id": None,
                "period": periods[0],
                "scope": "standalone",
                "question": question,
                "amount": sum(amounts, Decimal(0)) if amounts else None,
                "sources": sources,
            },
        )
        for f in flags:
            con.execute(
                "INSERT INTO forensic_flag_queries (query_id, flag_id) VALUES (?, ?)",
                [query["query_id"], f["flag_id"]],
            )
        return query
