"""`fin eval init <company>`: list what a person can check, with where it is printed.

Each line names one output of fin (a statement figure in current figures, a trial balance ledger, a
`tb_vs_statements` line, a stub figure, a bank closing balance, a register or payroll total, the
pass-through revenue split, adjusted revenue and EBITDA) and its source, and leaves `expected` and
`checked` empty: the value comes from the document, never from fin. Run again after loading
more, it adds the lines the golden set does not have yet and never changes or removes one."""

from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.diligence.engagement import company_database
from fin.diligence.recon import NAME as RECON
from fin.evals.golden import (
    COLUMNS,
    LATEST_ENGAGEMENT,
    OPTIONAL,
    RECON_KINDS,
    golden_path,
    read_golden,
    write_golden,
)
from fin.map.dictionary import Account, load_accounts
from fin.reconcile.bank import INVOICES as BANK_RUN
from fin.reconcile.bank import TB as BANK_TB

LATEST = LATEST_ENGAGEMENT

UNITS = {1: "rupees", 1_000: "thousand", 100_000: "lakh", 1_000_000: "million", 10_000_000: "crore"}

_STATEMENT = """
SELECT c.account, c.period, c.scope, d.file_name, source_ref(r.sheet, r.cell, r.page),
       r.rupees_per_unit, len(c.fact_ids)
FROM current_facts c
JOIN facts f ON f.fact_id = c.fact_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = c.doc_id
ORDER BY c.scope, c.period_months DESC, c.period, r.statement, f.raw_id
"""
_LEDGER = f"""
SELECT t.period, t.ledger, s.file_name, t.sheet, t."row"
FROM trial_balance t JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND t.row_type = 'ledger' AND s.engagement_id = {LATEST}
ORDER BY t.period, s.file_id, t.sheet, t."row"
"""
_RECON = f"""
SELECT r.period, i.account, i.left_source, i.right_source
FROM recon_items i JOIN recon_runs r USING (recon_id)
WHERE r.recon_id IN (
    SELECT max(recon_id) FROM recon_runs WHERE name = ? AND engagement_id = {LATEST} GROUP BY period
)
    AND i.account IS NOT NULL
ORDER BY r.period, i.item_no
"""
_BANK = f"""
SELECT b.period_end::VARCHAR, b.account, s.file_name, b.currency
FROM bank_statements b JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST}
ORDER BY b.account, b.period_end, b.file_id
"""
_D4 = f"""
SELECT r.name, r.period, i.key, i.left_source, i.right_source
FROM recon_items i JOIN recon_runs r USING (recon_id)
WHERE r.recon_id IN (
    SELECT max(recon_id) FROM recon_runs WHERE engagement_id = {LATEST} GROUP BY name, period
)
    AND r.name IN ({", ".join(f"'{k}'" for k in RECON_KINDS)})
    AND i.level IN ('invoice', 'ledger', 'payroll')
    AND i.kind NOT IN ('matched', 'unmapped', 'not_reconciled')
ORDER BY r.name, r.period, i.item_no
"""
_SPLIT = """
SELECT r.period,
       (SELECT string_agg(DISTINCT f.file_name, '; ') FROM source_files f
        WHERE f.kind = 'registers' AND f.status = 'loaded' AND f.engagement_id = r.engagement_id)
FROM revenue_split r WHERE r.sales_revenue IS NOT NULL AND r.basis = 'audited' ORDER BY r.period
"""
_ADJUSTED = """
SELECT period, scope, adjusted_revenue, adjusted_ebitda FROM adjusted_ebitda
WHERE adjusted_ebitda IS NOT NULL OR adjusted_revenue IS NOT NULL ORDER BY period, scope
"""
_REGISTERS = f"""
SELECT s.period, r.kind, s.file_name, r.sheet, s.file_id
FROM register_lines r JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST}
GROUP BY ALL ORDER BY s.file_id, r.kind DESC
"""
_PAYROLL = f"""
SELECT DISTINCT s.period, s.file_name, p.sheet, s.file_id
FROM payroll_lines p JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST}
ORDER BY s.file_id
"""
_ISSUES = f"""
SELECT a.query_id, a.period, a.sources FROM accountant_queries a
WHERE a.engagement_id = {LATEST} AND a.status IN ('approved', 'sent', 'answered', 'closed')
    AND a.amount IS NOT NULL
ORDER BY a.query_id
"""
_ISSUE_KINDS = f"""
SELECT a.query_id, a.period, i.kind, string_agg(
    coalesce(i.left_source, i.right_source), '; ' ORDER BY i.item_id)
FROM query_items q JOIN recon_items i ON i.item_id = q.item_id
JOIN accountant_queries a ON a.query_id = q.query_id
WHERE a.engagement_id = {LATEST} AND a.status IN ('approved', 'sent', 'answered', 'closed')
GROUP BY a.query_id, a.period, i.kind ORDER BY a.query_id, i.kind
"""
_MATCH = f"""
SELECT r.period, {{column}}, string_agg(DISTINCT s.file_name, '; ' ORDER BY s.file_name)
FROM recon_match_lines m
JOIN recon_items i ON i.recon_id = m.recon_id AND i.item_no = m.item_no
JOIN recon_runs r ON r.recon_id = m.recon_id
JOIN source_files s ON s.file_id = m.file_id
{{join}}
WHERE r.name = ? AND i.kind = ? AND m.side = ? AND r.engagement_id = {LATEST}
    AND r.recon_id IN (
        SELECT max(recon_id) FROM recon_runs WHERE name = ? GROUP BY period
    )
GROUP BY r.period, {{column}}
ORDER BY 1, 2
"""
_BANK_TB = f"""
SELECT r.period, i.left_source, i.right_source
FROM recon_items i JOIN recon_runs r USING (recon_id)
WHERE r.name = ? AND r.engagement_id = {LATEST}
    AND r.recon_id IN (SELECT max(recon_id) FROM recon_runs WHERE name = ? GROUP BY period)
ORDER BY r.period
"""
_ANALYTICS = """
SELECT 'customer_revenue', period, customer FROM customer_revenue
UNION ALL
SELECT 'sector_revenue', period, sector FROM sector_mix WHERE segment = 'core'
UNION ALL
SELECT 'product_revenue', period, product FROM product_revenue WHERE segment = 'core'
UNION ALL
SELECT 'top_customers_revenue', period, n::VARCHAR
FROM customer_concentration, (VALUES (1), (5), (10)) AS t(n) WHERE segment = 'core'
UNION ALL
SELECT 'retained_revenue', period, '' FROM customer_retention
UNION ALL
SELECT k, period, '' FROM product_pvm_summary,
    (VALUES ('pvm_price_effect'), ('pvm_volume_effect'), ('pvm_mix_effect')) AS t(k)
ORDER BY 1, 2, 3
"""
_STUB = f"""
SELECT x.period, x.account, s.file_name
FROM stub_figures x JOIN source_files s USING (file_id)
WHERE x.account IS NOT NULL
    AND x.engagement_id = {LATEST}
ORDER BY x.statement, x.account
"""


def _row(kind: str, period: str, source: str, **values: str) -> dict[str, str]:
    row = dict.fromkeys((*COLUMNS, *OPTIONAL), "")
    row.update(kind=kind, period=period, source=source, **values)
    return row


BANK_JOIN = 'JOIN bank_lines b ON b.file_id = m.file_id AND b.sheet = m.sheet AND b."row" = m."row"'


def _matches(con: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
    """The bank_vs_invoices and bank_vs_tb outputs a person can check."""
    rows = []
    for kind, column, join, item, side, note in (
        ("bank_unmatched", "b.account", BANK_JOIN,
         "left_only", "bank", "receipts with no invoice, in the account's currency"),
        ("invoice_unmatched", "m.party", "", "right_only", "invoice",
         "invoices with no receipt, Amount in Rs"),
        ("fx_line", "i.ledger", "", "fx", "invoice", "FX gain positive, loss negative; 0 if none"),
    ):  # fmt: skip
        sql = _MATCH.format(column=column, join=join)
        for period, ledger, file_name in con.execute(
            sql, [BANK_RUN, item, side, BANK_RUN]
        ).fetchall():
            rows.append(
                _row(kind, period, f"{file_name} (unmatched lines of the run)", ledger=ledger,
                     unit="rupees", note=note)
            )  # fmt: skip
    for period, left, right in con.execute(_BANK_TB, [BANK_TB, BANK_TB]).fetchall():
        rows.append(
            _row("bank_vs_tb", period, "; ".join(s for s in (left, right) if s), unit="rupees",
                 note="bank statements less trial balance")
        )  # fmt: skip
    return rows


def candidates(
    con: duckdb.DuckDBPyConnection, accounts: dict[str, Account]
) -> list[dict[str, str]]:
    """One line per output fin has for the company now, with its source. A per-share figure is
    stored as printed, so its unit is rupees whatever the statement's unit."""
    rows = []
    statement = con.execute(_STATEMENT).fetchall()
    for account, period, scope, file_name, ref, per_unit, lines in statement:
        note = f"fin adds {lines} printed lines into this account" if lines > 1 else ""
        rows.append(
            _row(
                "statement",
                period,
                f"{file_name} {ref}",
                scope=scope,
                account=account,
                unit=UNITS.get(per_unit, "rupees")
                if accounts[account].unit == "rupees"
                else "rupees",
                note=note,
            )
        )
    for period, ledger, file_name, sheet, number in con.execute(_LEDGER).fetchall():
        rows.append(
            _row(
                "tb_ledger",
                period,
                f"{file_name} {sheet}!{number}",
                ledger=ledger,
                unit="rupees",
                note="closing debit less credit",
            )
        )
    for period, account, left, right in con.execute(_RECON, [RECON]).fetchall():
        rows.append(
            _row(
                "tb_vs_statements",
                period,
                "; ".join(s for s in (left, right) if s),
                account=account,
                unit="rupees",
                note="trial balance less statements",
            )
        )
    for period, account, file_name, currency in con.execute(_BANK).fetchall():
        rows.append(
            _row(
                "bank_closing",
                period,
                f"{file_name} closing balance",
                ledger=account,
                unit="rupees",
                note=f"closing balance as printed, in {currency}",
            )
        )
    for name, period, key, left, right in con.execute(_D4).fetchall():
        rows.append(
            _row(
                name,
                period,
                "; ".join(s for s in (left, right) if s),
                ledger=key,
                unit="rupees",
                note="register (or Ledger Vouchers, or payroll) less books (or trial balance); "
                "lines that show a difference",
            )
        )
    for period, files in con.execute(_SPLIT).fetchall():
        for kind, note in (
            ("core_revenue", "sales register, customers not on the pass-through list"),
            ("passthrough_revenue", "sales register, customers on the pass-through list"),
        ):
            rows.append(_row(kind, period, files or "", unit="rupees", note=note))
    for period, scope, revenue, ebitda in con.execute(_ADJUSTED).fetchall():
        for kind, value in (("adjusted_revenue", revenue), ("adjusted_ebitda", ebitda)):
            if value is not None:
                rows.append(
                    _row(
                        kind,
                        period,
                        "statements and accepted adjustments",
                        scope=scope,
                        unit="rupees",
                        note="reported plus accepted adjustments (statements)",
                    )
                )
    for period, kind, file_name, sheet, _ in con.execute(_REGISTERS).fetchall():
        rows.append(
            _row(
                "register_total",
                period,
                f"{file_name} {sheet}, Amount in Rs column",
                ledger=kind,
                unit="rupees",
                note="sum of Amount in Rs (before GST) over every row, total row left out",
            )
        )
    for period, file_name, sheet, _ in con.execute(_PAYROLL).fetchall():
        for field, label in (("gross_salary", "gross salary"), ("ctc", "CTC")):
            rows.append(
                _row(
                    "payroll_total",
                    period,
                    f"{file_name} {sheet}, {label} column",
                    ledger=field,
                    unit="rupees",
                    note=f"sum of {label} (annual rupees) over the employees, total row left out",
                )
            )
    for query_id, period, sources in con.execute(_ISSUES).fetchall():
        rows.append(
            _row(
                "query_issue",
                period,
                sources,
                ledger=str(query_id),
                unit="rupees",
                note="the issue's amount at issue: its lines' differences added without signs",
            )
        )
    for query_id, period, kind, sources in con.execute(_ISSUE_KINDS).fetchall():
        rows.append(
            _row(
                "query_difference",
                period,
                sources,
                ledger=f"{query_id} {kind}",
                unit="rupees",
                note="value of this kind of difference in the issue, without signs",
            )
        )
    rows += _matches(con)
    files = con.execute(
        "SELECT string_agg(DISTINCT file_name, '; ') FROM source_files WHERE kind = 'registers' "
        f"AND status = 'loaded' AND engagement_id = {LATEST}"
    ).fetchone()
    for kind, period, key in con.execute(_ANALYTICS).fetchall():
        rows.append(
            _row(
                kind,
                period,
                (files[0] if files else "") or "",
                ledger=key,
                unit="rupees",
                note="sales register (Amount in Rs, before GST); core is customers not on the "
                "pass-through list",
            )
        )
    for period, account, file_name in con.execute(_STUB).fetchall():
        rows.append(
            _row(
                "stub",
                period,
                file_name,
                account=account,
                unit="rupees",
                note="unaudited management accounts",
            )
        )
    return rows


def init_golden(run: Run, company: str) -> dict[str, Any]:
    """Write or extend the company's golden set. E831 (nothing written) when the one there cannot be
    read, so a person's checks are never overwritten."""
    path = golden_path(run.root, company)
    accounts = load_accounts(run.root)
    existing = read_golden(run.root, company, accounts) if path.is_file() else []
    with company_database(run, company) as con:
        found = candidates(con, accounts)
    keys = {check.key for check in existing}
    kept = [
        {
            **{c: getattr(check, c) for c in (*COLUMNS, *OPTIONAL) if c != "checked"},
            "checked": "yes" if check.checked else "",
        }
        for check in existing
    ]
    added = [
        row
        for row in found
        if (row["kind"], row["period"], row["scope"], row["account"], row["ledger"]) not in keys
    ]
    write_golden(path, kept + added)
    run.event(
        "eval init",
        f"{company}: golden set {path}, {len(added)} lines added, {len(kept)} kept",
        context={"company": company, "added": len(added), "kept": len(kept)},
    )
    return {"path": path, "added": len(added), "kept": len(kept)}
