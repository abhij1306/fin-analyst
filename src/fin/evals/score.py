"""`fin eval run <company>`: score fin's current outputs against the company's golden set.

Each checked line is compared exactly, in rupees after unit conversion, with what fin gives now:
`pass` (equal), `fail` (differs), `missing` (fin has no value), or `unchecked` (the person has not
checked it: not scored). The score is passes over scored lines. With it, the counts that show where
fin's reading needs work (fin.evals.counts). The report goes to `evals/<company>/eval-<run_id>.md`
and `.json` in the data folder."""

import json
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.diligence.engagement import company_database
from fin.diligence.recon import NAME as RECON
from fin.evals.counts import counts, related_codes
from fin.evals.golden import Check, read_golden
from fin.map.dictionary import load_accounts
from fin.paths import evals_dir

OUTCOMES = ("pass", "fail", "missing", "unchecked")

_FIN_VALUE = {
    "statement": (
        "SELECT value FROM current_facts WHERE period = ? AND scope = ? AND account = ?",
        ("period", "scope", "account"),
    ),
    "tb_ledger": (
        "SELECT sum(t.closing_debit - t.closing_credit) FROM trial_balance t "
        "JOIN source_files s USING (file_id) WHERE s.status = 'loaded' AND t.row_type = 'ledger' "
        "AND t.period = ? AND t.ledger = ? HAVING count(*) > 0",
        ("period", "ledger"),
    ),
    "tb_vs_statements": (
        "SELECT difference FROM recon_items WHERE account = ? AND recon_id = "
        f"(SELECT max(recon_id) FROM recon_runs WHERE name = '{RECON}' AND period = ?)",
        ("account", "period"),
    ),
    "stub": (
        "SELECT value FROM stub_figures WHERE period = ? AND account = ? AND engagement_id = "
        "(SELECT max(engagement_id) FROM engagement WHERE stub IS NOT NULL)",
        ("period", "account"),
    ),
}


def fin_value(con: duckdb.DuckDBPyConnection, check: Check) -> Decimal | None:
    sql, fields = _FIN_VALUE[check.kind]
    row = con.execute(sql, [getattr(check, f) for f in fields]).fetchone()
    return None if row is None or row[0] is None else Decimal(row[0])


def relative(fin: Decimal | None, expected: Decimal | None) -> Decimal | None:
    """(fin - expected) / |expected|, to four places; None without both or for an expected nil."""
    if fin is None or expected is None or expected == 0:
        return None
    return ((fin - expected) / abs(expected)).quantize(Decimal("0.0001"))


def score_check(con: duckdb.DuckDBPyConnection, check: Check) -> dict[str, Any]:
    expected = check.rupees
    fin = fin_value(con, check)
    if not check.checked or expected is None:
        outcome = "unchecked"
    elif fin is None:
        outcome = "missing"
    else:
        outcome = "pass" if fin == expected else "fail"
    return {
        "line": check.line,
        "kind": check.kind,
        "period": check.period,
        "scope": check.scope,
        "account": check.account,
        "ledger": check.ledger,
        "source": check.source,
        "outcome": outcome,
        "expected": expected,
        "fin": fin,
        "relative_difference": relative(fin, expected) if outcome == "fail" else None,
        "codes": related_codes(con, check) if outcome in ("fail", "missing") else [],
    }


def summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Outcomes counted overall and per kind, and the score (passes over scored lines)."""
    by_kind: dict[str, dict[str, int]] = {}
    for check in checks:
        kinds = by_kind.setdefault(check["kind"], dict.fromkeys(OUTCOMES, 0))
        kinds[check["outcome"]] += 1
    total = {o: sum(k[o] for k in by_kind.values()) for o in OUTCOMES}
    scored = total["pass"] + total["fail"] + total["missing"]
    score = None if not scored else (Decimal(100 * total["pass"]) / scored).quantize(Decimal("0.1"))
    return {"score_percent": score, "scored": scored, **total, "by_kind": by_kind}


def evaluate(run: Run, company: str) -> dict[str, Any]:
    """Score the company's golden set (E830 without one, E831 when it cannot be read) and count."""
    checks = read_golden(run.root, company, set(load_accounts(run.root)))
    with company_database(run, company) as con:
        scored = [score_check(con, check) for check in checks]
        found = counts(run.root, company, con)
    return {"company": company, "summary": summary(scored), "checks": scored, "counts": found}


def format_report(report: dict[str, Any]) -> str:
    s = report["summary"]
    score = "no checked lines" if s["score_percent"] is None else f"{s['score_percent']}%"
    out = [
        f"# Golden eval: {report['company']} (run {report['run_id']})",
        "",
        f"Score: {score} ({s['pass']} pass, {s['fail']} fail, {s['missing']} missing in fin; "
        f"{s['unchecked']} unchecked, not scored)",
        "",
        "| Kind | Pass | Fail | Missing | Unchecked |",
        "|---|---|---|---|---|",
    ]
    out += [
        f"| {k} | {v['pass']} | {v['fail']} | {v['missing']} | {v['unchecked']} |"
        for k, v in s["by_kind"].items()
    ]
    out += ["", "## Not passing", ""]
    bad = [c for c in report["checks"] if c["outcome"] in ("fail", "missing")]
    out += [
        f"- line {c['line']} {c['outcome']}: {c['kind']} {c['period']} {c['scope']} "
        f"{c['account'] or c['ledger']}: expected {c['expected']}, fin {c['fin']} "
        f"({c['source']}){' codes ' + ', '.join(c['codes']) if c['codes'] else ''}"
        for c in bad
    ] or ["none"]
    out += [
        "",
        "## Counts",
        "",
        "```json",
        json.dumps(report["counts"], indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(out)


def run_eval(run: Run, company: str) -> dict[str, Any]:
    """Score, then write the report (Markdown and JSON) to `evals/<company>/`."""
    report = {"run_id": run.run_id, **evaluate(run, company)}
    folder = evals_dir(run.root) / company
    folder.mkdir(parents=True, exist_ok=True)
    md, js = folder / f"eval-{run.run_id}.md", folder / f"eval-{run.run_id}.json"
    md.write_text(format_report(report), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    s = report["summary"]
    run.event(
        "eval run",
        f"{company}: {s['pass']} pass, {s['fail']} fail, {s['missing']} missing, "
        f"{s['unchecked']} unchecked; report {md}",
        context={"company": company, "report": str(md)},
    )
    return {**report, "report_md": md, "report_json": js}
