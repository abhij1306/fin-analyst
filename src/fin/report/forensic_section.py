"""The FDD report's "Forensic tests" section (SPEC-21, docs/fdd.md F8): what the forensic tests of
SPEC-18 to 20 found, as the user's decisions carried it.

- every test and period: ran (date, population, flags, flags at or above the threshold), stale,
  too few entries, cannot run (and what it needs) or not run, as `fin forensic status` says it;
  no score, rating or "risk" word;
- per test, its flags at or above the threshold, largest amount first (a voucher gap, with none,
  last), at most `TOP`, with the rest counted (all of them in `fin forensic flags`), each with
  its source; a party is its token, a ledger as filed;
- the queries raised on flags with their status, and the forensic findings (final ones; drafts
  only with --drafts, as the key findings);
- with no forensic run, the section says so; it is never left out.

Read on the report's connection for its latest engagement; every count and amount was computed in
SQL (`fin.forensic.status`).
"""

from pathlib import Path
from typing import Any

import duckdb

from fin.config import Display
from fin.db.rows import fetch_dicts
from fin.fdd.finding_flags import AREA, flag_text
from fin.forensic.status import flag_rows, state_text, status_rows
from fin.report.dd_tables import _label, _m
from fin.report.tables import table

TOP = 20  # flags shown per test; the rest are counted

QUERIES = """
SELECT q.query_id, q.period, q.status, q.amount, q.question, count(*) AS flags
FROM accountant_queries q JOIN forensic_flag_queries l USING (query_id)
WHERE q.engagement_id = ?
GROUP BY q.query_id, q.period, q.status, q.amount, q.question
ORDER BY q.query_id
"""


def collect(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    company: str,
    engagement: dict[str, Any],
    fdd_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """The section's data. `fdd_findings`: the report's findings (final, or with drafts)."""
    rows = status_rows(con, root, company, engagement)
    flags: dict[str, list[dict[str, Any]]] = {}
    for flag in flag_rows(con, root, company, engagement):
        if flag["over_threshold"]:
            flags.setdefault(flag["test"], []).append(flag)
    return {
        "company": company,
        "ran": any(r["outcome"] is not None for r in rows),
        "tests": [{**r, "text": state_text(r)} for r in rows],
        "flags": {
            test: {"shown": f[:TOP], "more": max(len(f) - TOP, 0)} for test, f in flags.items()
        },
        "queries": fetch_dicts(con, QUERIES, [engagement["engagement_id"]]),
        "findings": [r for r in fdd_findings if r["area"] == AREA],
    }


def section(data: dict[str, Any], unit: Display) -> str:
    """The section's Markdown under its heading."""
    forensic = data["forensic"]
    if not forensic["ran"]:
        return (
            "No forensic test has run for this engagement (`fin forensic run`). Nothing was "
            "flagged because nothing was tested."
        )
    parts = [
        "Each test per period, as `fin forensic status` gives it. A flag is an entry for the "
        "company to explain, never a conclusion; a stale run's data or settings changed since it "
        "ran.",
        _status_table(forensic, data["stub"]),
    ]
    titles = {r["test"]: r["title"] for r in forensic["tests"]}
    for test, flags in forensic["flags"].items():
        parts.append(f"**{titles.get(test, test)}** (`{test}`): flags at or above the threshold")
        parts.append(_flag_table(flags["shown"], data["stub"], unit))
        if flags["more"]:
            parts.append(
                f"And {flags['more']} more at or above the threshold, all in `fin forensic flags "
                f"{forensic['company']} --test {test} --over-threshold`."
            )
    parts += ["**Queries raised on flags**", _queries_table(forensic, unit)]
    parts += ["**Forensic findings**", _findings_table(forensic, data["drafts"], unit)]
    return "\n\n".join(parts)


def _status_table(forensic: dict[str, Any], stub: str | None) -> str:
    rows = [
        [
            f"{r['title']} ({r['test']})",
            _label(r["period"], stub),
            r["text"],
            str(r["ran_at"])[:10] if r.get("ran_at") else "",
            "" if r.get("population") is None else str(r["population"]),
        ]
        for r in forensic["tests"]
    ]
    return table(["Test", "Period", "State", "Ran on", "Population"], rows, "No forensic test.")


def _flag_table(flags: list[dict[str, Any]], stub: str | None, unit: Display) -> str:
    rows = [
        [
            f"#{f['flag_id']}",
            _label(f["period"], stub) + (" (stale run)" if f["stale"] else ""),
            f["voucher_no"] or "",
            f["ledger"] or f["account"] or "",
            f["party"] or "",
            _m(f["amount"], unit) if f["amount"] is not None else "no amount",
            f["source"],
            f["reason"],
            f"#{f['query_id']} ({f['query_status']})" if f["query_id"] else "",
        ]
        for f in flags
    ]
    return table(
        [
            "Flag",
            "Period",
            "Voucher",
            "Ledger",
            "Party",
            "Amount",
            "Source",
            "Why flagged",
            "Query",
        ],
        rows,
        "",
    )


def _queries_table(forensic: dict[str, Any], unit: Display) -> str:
    rows = [
        [f"#{q['query_id']}", q["period"] or "", q["status"], str(q["flags"]),
         _m(q["amount"], unit), q["question"]]
        for q in forensic["queries"]
    ]  # fmt: skip
    return table(
        ["Query", "Period", "Status", "Flags", "Amount at issue", "Question"],
        rows,
        "No query is raised on forensic flags.",
    )


def _findings_table(forensic: dict[str, Any], drafts: bool, unit: Display) -> str:
    rows = [
        [f"#{r['finding_id']}", r["status"], r["severity"], r["title"], flag_text(r),
         _m(r["amount"], unit) if r["amount"] is not None else "none"]
        for r in forensic["findings"]
    ]  # fmt: skip
    empty = "No forensic findings yet." if drafts else "No forensic finding marked final yet."
    return table(["Finding", "Status", "Severity", "Title", "Flags", "Amount"], rows, empty)
