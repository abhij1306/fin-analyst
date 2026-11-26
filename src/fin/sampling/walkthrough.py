"""`fin walkthrough record <company> <sample-id> <file.json>` and `fin walkthrough summary`.

Claude walks each line of a sample through the checks below and writes a JSON list, one object per
result:

    [{"line_no": 1, "check": "contract_covers", "result": "exception",
      "evidence": "the contract rate is 900, the invoice 1000", "file": "contract.pdf", "page": 3}]

- `check`: in_register (the line is in the register), contract_covers (a contract covers it and
  its rate, dates and value agree), service_in_contract (the service period sits inside the
  contract), bank_matched (a bank receipt or payment is matched, D5), ledger_posting (the ledger
  posting is found, D2);
- `result`: pass, exception or not_available; `evidence` (required): what was seen, with known
  names replaced by tokens on load; `file` and `page`: where, when it is a document.

Results are stored in `walkthroughs` (read_by claude); a result for a line and check already
recorded replaces it. fin does not check the reading: trusting it is the user's decision. E902 for a
file that cannot be used (nothing recorded). After recording, the period's `walkthrough`
reconciliation runs again: an exception at or above the engagement threshold is a line for a query.
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import WalkthroughInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.diligence.mask import Masker
from fin.reconcile import store
from fin.reconcile import walkthrough as recon_module
from fin.reconcile.format import format_result
from fin.sampling.draw import load_sample

CHECKS = ("in_register", "contract_covers", "service_in_contract", "bank_matched", "ledger_posting")
RESULTS = ("pass", "exception", "not_available")


def _item(number: int, item: Any, lines: int) -> dict[str, Any]:
    """One validated result; ValueError says what is wrong."""
    if not isinstance(item, dict):
        raise ValueError("not an object")
    line = item.get("line_no")
    if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= lines:
        raise ValueError(f"line_no {line!r} is not a line of the sample (1 to {lines})")
    if item.get("check") not in CHECKS:
        raise ValueError(f"check {item.get('check')!r} is not one of {', '.join(CHECKS)}")
    if item.get("result") not in RESULTS:
        raise ValueError(f"result {item.get('result')!r} is not one of {', '.join(RESULTS)}")
    evidence = item.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence is empty")
    page, file = item.get("page"), item.get("file")
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
        raise ValueError("page starts at 1")
    if file is not None and not isinstance(file, str):
        raise ValueError("file is not text")
    return {
        "line_no": line,
        "check": item["check"],
        "result": item["result"],
        "evidence": " ".join(evidence.split()),
        "file": file or None,
        "page": page,
    }


def read_results(path: Path, lines: int) -> list[dict[str, Any]]:
    """The file's results, validated. E902 naming every result at fault."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalkthroughInvalid(f"cannot read {path.name}: {exc}; nothing recorded") from exc
    if not isinstance(data, list) or not data:
        raise WalkthroughInvalid(f"{path.name} is not a non-empty JSON list; nothing recorded")
    results, problems, seen = [], [], set()
    for number, item in enumerate(data, start=1):
        try:
            result = _item(number, item, lines)
        except ValueError as exc:
            problems.append(f"result {number}: {exc}")
            continue
        if (result["line_no"], result["check"]) in seen:
            problems.append(f"result {number}: repeats line {result['line_no']} {result['check']}")
        seen.add((result["line_no"], result["check"]))
        results.append(result)
    if problems:
        raise WalkthroughInvalid(f"{path.name}: {'; '.join(problems)}; nothing recorded")
    return results


def record(run: Run, company: str, sample_id: int, path: Path) -> dict[str, Any]:
    """Store the results for the sample, then run the period's walkthrough reconciliation again.
    Returns {"summary": the sample's summary row, "recon": the reconciliation result}. E903, E902.
    """
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        sample = load_sample(con, engagement_id, sample_id)
        results = read_results(path, len(sample["lines"]))
        masker = Masker(con, run.run_id)
        now = utcnow()
        for result in results:
            con.execute(
                "DELETE FROM walkthroughs WHERE sample_id = ? AND line_no = ? AND check_name = ?",
                [sample_id, result["line_no"], result["check"]],
            )
            con.execute(
                "INSERT INTO walkthroughs (sample_id, line_no, check_name, result, evidence, "
                "evidence_file, evidence_page, read_by, run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'claude', ?, ?)",
                [
                    sample_id, result["line_no"], result["check"], result["result"],
                    masker.text(result["evidence"]), result["file"], result["page"],
                    run.run_id, now,
                ],
            )  # fmt: skip
        engagement = require_open(con, company)
        period = sample["period"]
        recon = store.save(
            run, con, engagement, recon_module.NAME, period,
            recon_module.compute(con, company, engagement, period),
        )  # fmt: skip
        summary = summaries(con, engagement_id, sample_id)[0]
    run.event(
        "reconcile",
        format_result(recon).splitlines()[0],
        context={"company": company, "recon_id": recon["recon_id"]},
    )
    return {"summary": summary, "recon": recon}


def summaries(
    con: duckdb.DuckDBPyConnection, engagement_id: int, sample_id: int | None = None
) -> list[dict[str, Any]]:
    """The view `walkthrough_summary` rows of the engagement (the data function the FDD report
    calls), or one sample's."""
    return fetch_dicts(
        con,
        "SELECT * FROM walkthrough_summary WHERE engagement_id = ? "
        "AND (? IS NULL OR sample_id = ?) ORDER BY sample_id",
        [engagement_id, sample_id, sample_id],
    )


def exception_lines(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[dict[str, Any]]:
    """The checks Claude recorded as an exception, with the sample line they are on (the FDD
    report lists them)."""
    return fetch_dicts(
        con,
        "SELECT w.sample_id, w.line_no, l.party, l.invoice_no, l.invoice_date, l.amount_inr, "
        "w.check_name, w.evidence, w.evidence_file, w.evidence_page "
        "FROM walkthroughs w JOIN samples s USING (sample_id) "
        "JOIN sample_lines l USING (sample_id, line_no) "
        "WHERE s.engagement_id = ? AND w.result = 'exception' ORDER BY w.sample_id, w.line_no, "
        "w.check_name",
        [engagement_id],
    )


def list_summaries(run: Run, company: str) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return summaries(con, require_open(con, company)["engagement_id"])


def format_summary(row: dict[str, Any]) -> str:
    return (
        f"sample #{row['sample_id']} ({row['kind']} {row['period']}): {row['lines']} lines, "
        f"{row['lines_walked']} walked, {row['checks']} checks: {row['checks_passed']} passed, "
        f"{row['exceptions']} exceptions on {row['exception_lines']} lines "
        f"({rupees(row['exception_amount'])}), {row['not_available']} not available "
        "(read by Claude, not verified)"
    )
