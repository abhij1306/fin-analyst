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
from fin.evals.golden import LATEST_ENGAGEMENT, RECON_KINDS, Check, read_golden
from fin.map.dictionary import load_accounts
from fin.paths import evals_dir
from fin.reconcile.bank import INVOICES as BANK_RUN
from fin.reconcile.bank import TB as BANK_TB

OUTCOMES = ("pass", "fail", "missing", "unchecked")

_RUN = (
    f"(SELECT max(recon_id) FROM recon_runs WHERE name = '{BANK_RUN}' AND period = ? "
    f"AND engagement_id = {LATEST_ENGAGEMENT})"
)

_FIN_VALUE = {
    "statement": (
        "SELECT value FROM current_facts WHERE period = ? AND scope = ? AND account = ?",
        ("period", "scope", "account"),
    ),
    "tb_ledger": (
        "SELECT sum(t.closing_debit - t.closing_credit) FROM trial_balance t "
        "JOIN source_files s USING (file_id) WHERE s.status = 'loaded' AND t.row_type = 'ledger' "
        f"AND s.engagement_id = {LATEST_ENGAGEMENT} AND t.period = ? AND t.ledger = ? "
        "HAVING count(*) > 0",
        ("period", "ledger"),
    ),
    "tb_vs_statements": (
        "SELECT difference FROM recon_items WHERE account = ? AND recon_id = "
        f"(SELECT max(recon_id) FROM recon_runs WHERE name = '{RECON}' AND period = ? "
        f"AND engagement_id = {LATEST_ENGAGEMENT})",
        ("account", "period"),
    ),
    "bank_closing": (
        "SELECT b.closing_balance FROM bank_statements b JOIN source_files s USING (file_id) "
        f"WHERE s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT} "
        "AND b.period_end::VARCHAR = ? AND b.account = ? ORDER BY file_id LIMIT 1",
        ("period", "ledger"),
    ),
    "core_revenue": ("SELECT core_revenue FROM revenue_split WHERE period = ?", ("period",)),
    "passthrough_revenue": (
        "SELECT passthrough_revenue FROM revenue_split WHERE period = ?",
        ("period",),
    ),
    "adjusted_revenue": (
        "SELECT adjusted_revenue FROM adjusted_ebitda WHERE period = ? AND scope = ?",
        ("period", "scope"),
    ),
    "adjusted_ebitda": (
        "SELECT adjusted_ebitda FROM adjusted_ebitda WHERE period = ? AND scope = ?",
        ("period", "scope"),
    ),
    "register_total": (
        "SELECT sum(r.amount_inr) FROM register_lines r JOIN source_files s USING (file_id) "
        f"WHERE s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT} AND s.period = ? "
        "AND r.kind = ? HAVING count(*) > 0",
        ("period", "ledger"),
    ),
    "payroll_total": (
        "SELECT CASE WHEN ? = 'gross_salary' THEN sum(p.gross_salary) ELSE sum(p.ctc) END "
        "FROM payroll_lines p JOIN source_files s USING (file_id) "
        f"WHERE s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT} AND s.period = ? "
        "HAVING count(*) > 0",
        ("ledger", "period"),
    ),
    "bank_unmatched": (
        "SELECT coalesce((SELECT sum(m.amount) FROM recon_match_lines m "
        "JOIN recon_items i USING (recon_id, item_no) JOIN bank_lines b ON b.file_id = m.file_id "
        'AND b.sheet = m.sheet AND b."row" = m."row" '
        f"WHERE m.recon_id = {_RUN} AND m.side = 'bank' AND i.kind = 'left_only' "
        f"AND b.account = ?), 0) WHERE EXISTS (SELECT 1 FROM recon_runs WHERE recon_id = {_RUN})",
        ("period", "ledger", "period"),
    ),
    "invoice_unmatched": (
        "SELECT coalesce((SELECT sum(m.amount_inr) FROM recon_match_lines m "
        "JOIN recon_items i USING (recon_id, item_no) "
        f"WHERE m.recon_id = {_RUN} AND m.side = 'invoice' AND i.kind = 'right_only' "
        f"AND m.party = ?), 0) WHERE EXISTS (SELECT 1 FROM recon_runs WHERE recon_id = {_RUN})",
        ("period", "ledger", "period"),
    ),
    "fx_line": (
        f"SELECT coalesce((SELECT sum(difference) FROM recon_items WHERE recon_id = {_RUN} "
        f"AND kind = 'fx' AND ledger = ?), 0) "
        f"WHERE EXISTS (SELECT 1 FROM recon_runs WHERE recon_id = {_RUN})",
        ("period", "ledger", "period"),
    ),
    "bank_vs_tb": (
        "SELECT difference FROM recon_items WHERE recon_id = (SELECT max(recon_id) FROM "
        f"recon_runs WHERE name = '{BANK_TB}' AND period = ? "
        f"AND engagement_id = {LATEST_ENGAGEMENT})",
        ("period",),
    ),
    "customer_revenue": (
        "SELECT sum(revenue) FROM customer_revenue WHERE period = ? AND customer = ? "
        "HAVING count(*) > 0",
        ("period", "ledger"),
    ),
    "sector_revenue": (
        "SELECT revenue FROM sector_mix WHERE period = ? AND sector = ? AND segment = 'core'",
        ("period", "ledger"),
    ),
    "product_revenue": (
        "SELECT revenue FROM product_revenue WHERE period = ? AND product = ? AND segment = 'core'",
        ("period", "ledger"),
    ),
    "top_customers_revenue": (
        "SELECT CASE ? WHEN '1' THEN top1_revenue WHEN '5' THEN top5_revenue "
        "WHEN '10' THEN top10_revenue END FROM customer_concentration "
        "WHERE segment = 'core' AND period = ?",
        ("ledger", "period"),
    ),
    "retained_revenue": (
        "SELECT retained_revenue FROM customer_retention WHERE period = ?",
        ("period",),
    ),
    "pvm_price_effect": (
        "SELECT price_effect FROM product_pvm_summary WHERE period = ?",
        ("period",),
    ),
    "pvm_volume_effect": (
        "SELECT volume_effect FROM product_pvm_summary WHERE period = ?",
        ("period",),
    ),
    "pvm_mix_effect": (
        "SELECT mix_effect FROM product_pvm_summary WHERE period = ?",
        ("period",),
    ),
    "stub": (
        "SELECT value FROM stub_figures WHERE period = ? AND account = ? "
        f"AND engagement_id = {LATEST_ENGAGEMENT}",
        ("period", "account"),
    ),
}


_EXPORTED = "a.status IN ('approved', 'sent', 'answered', 'closed')"
_FIN_VALUE.update(
    {
        "query_issue": (
            "SELECT a.amount FROM accountant_queries a WHERE a.query_id::VARCHAR = ? "
            f"AND a.period = ? AND a.engagement_id = {LATEST_ENGAGEMENT} AND {_EXPORTED}",
            ("ledger", "period"),
        ),
        "query_difference": (
            "SELECT sum(abs(i.difference)) FROM query_items q "
            "JOIN recon_items i ON i.item_id = q.item_id "
            "JOIN accountant_queries a ON a.query_id = q.query_id WHERE a.period = ? "
            f"AND a.engagement_id = {LATEST_ENGAGEMENT} AND {_EXPORTED} "
            "AND a.query_id::VARCHAR || ' ' || i.kind = ? HAVING count(*) > 0",
            ("period", "ledger"),
        ),
    }
)
_FIN_VALUE.update(
    {
        kind: (
            "SELECT difference FROM recon_items WHERE key = ? AND level IS NOT NULL AND level <> "
            "'month' AND level <> 'party' AND recon_id = (SELECT max(recon_id) FROM recon_runs "
            f"WHERE name = '{kind}' AND period = ? AND engagement_id = {LATEST_ENGAGEMENT})",
            ("ledger", "period"),
        )
        for kind in RECON_KINDS
    }
)


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
    expected = check.rupees if check.checked else None
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
    checks = read_golden(run.root, company, load_accounts(run.root))
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
