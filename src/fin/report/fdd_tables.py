"""The FDD report's own Markdown tables (templates/report/fdd.qmd), from the data
`fin.report.fdd_data.collect` read; the quality of earnings, working capital, reconciliation, gate
and source tables are the diligence report's (`fin.report.dd_tables`). Python only formats: every
value was computed in SQL. A section whose data is not loaded says so. Names are tokens; Claude's
readings are labelled as such, not verified.
"""

import html
from decimal import Decimal
from typing import Any

from fin.fdd import findings
from fin.reconcile.visibility import orderbook_date_note
from fin.report.dd_tables import _label, _m, _ratio
from fin.report.tables import table

SEVERITY_TITLES = {"high": "High", "medium": "Medium", "low": "Low"}
LABELS = {
    "spa_protection": "SPA indemnity or warranty",
    "condition_precedent": "condition precedent",
    "further_work": "further work",
    "working_capital": "working capital",
    "net_debt": "net debt",
    "qoe": "quality of earnings",
    "related_parties": "related parties",
}
NOT_READ = "not read"  # a term the reading left empty: not the same as no clause
NOT_VERIFIED = "Read by Claude from the documents (file + page); not verified by fin."


def _t(text: str | None) -> str:
    """Stored free text (Claude's findings and readings, accountants' answers) for Markdown: HTML
    markup is escaped, so it shows as text and never runs in fdd.html."""
    return html.escape(text or "", quote=False)


def _known(value: bool | None) -> str:
    """A term Claude read, or did not: yes, no, or "not read" (never shown as absent)."""
    return NOT_READ if value is None else ("yes" if value else "no")


def _word(value: str) -> str:
    return LABELS.get(value, value.replace("_", " "))


def _months(value: int | None) -> str:
    return NOT_READ if value is None else f"{value} months"


def _days(value: int | None) -> str:
    return NOT_READ if value is None else f"{value} days"


def _rate(value: Any) -> str:
    """A rate per unit of quantity: in rupees whatever the report's display unit. SQL gives a
    float when the quantity column is DOUBLE; money() needs a Decimal."""
    return "n/a" if value is None else _m(Decimal(str(value)), "rupees")


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{value}%"


def draft_note(data: dict[str, Any]) -> str:
    if not data["drafts"]:
        return ""
    return (
        "**Draft copy**: this report includes findings the user has not marked final "
        "(status shown on each)."
    )


def key_findings(data: dict[str, Any], unit: str) -> str:
    """Each finding, grouped by severity: observation, implication, impact and amount (from the
    linked evidence), deal consideration, management response and evidence."""
    rows = data["fdd_findings"]
    if not rows:
        return "No findings marked final yet." if not data["drafts"] else "No findings yet."
    out = []
    for severity, title in SEVERITY_TITLES.items():
        mine = [r for r in rows if r["severity"] == severity]
        if mine:
            out.append(f"### {title} severity")
            out.extend(_finding(r, unit, data["drafts"]) for r in mine)
    return "\n\n".join(out)


def _finding(r: dict[str, Any], unit: str, drafts: bool) -> str:
    amount = (
        f"{_m(r['amount'], unit)} ({r['amount_source']})" if r["amount"] is not None else "none"
    )
    lines = [
        f"#### {_t(r['title'])} (finding #{r['finding_id']})"
        + (f" [{r['status']}]" if drafts else ""),
        "",
        _t(r["observation"]),
        "",
        f"- Area: {_word(r['area'])}; impact: {_word(r['impact_type'])}; amount: {amount}",
    ]
    if r["implication"]:
        lines.append(f"- Implication: {_t(r['implication'])}")
    lines.append(f"- Deal consideration: {_word(r['deal_consideration'])}")
    if r["query_id"] is not None:
        response = _t(r["management_response"]) or f"no answer yet (query {r['query_status']})"
        source = f"; {_t(r['response_source'])}" if r["response_source"] else ""
        lines.append(f"- Management response (query #{r['query_id']}{source}): {response}")
    else:
        lines.append("- Management response: no query raised")
    lines.append(f"- Evidence: {_t(r['evidence'])}")
    lines += [f"- **Flag**: {_t(flag)}" for flag in findings.flags(r)]
    return "\n".join(lines)


def net_debt_table(data: dict[str, Any], unit: str) -> str:
    stub = data["stub"]
    rows = [
        [
            _label(r["period"], stub), _m(r["cash"], unit), _m(r["debt"], unit),
            _m(r["debt_like"], unit), _m(r["net_debt"], unit),
            f"{_m(r['proposed_items'], unit)} ({r['proposed_item_count']})",
            str(r["unclassified_ledgers"]), r["trial_balance_file"] or "no trial balance",
        ]
        for r in data["net_debt"]["net_debt"]
    ]  # fmt: skip
    return table(
        ["Period end", "Cash", "Debt", "Debt-like", "Net debt", "Proposed items (not applied)",
         "Unclassified ledgers", "Trial balance"],
        rows,
        "Not loaded: no trial balance in the engagement (`fin netdebt show`).",
    )  # fmt: skip


def coverage_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["party_kind"], str(r["rank"]), r["counterparty"], _m(r["amount_inr"], unit),
            "yes" if r["contract_on_file"] else "no",
            _known(r["expires_within_12m"]),
            _known(r["change_of_control"]), _known(r["auto_renew"]),
            _months(r["lock_in_left_months"]), _days(r["notice_days"]),
            f"{r['file_name']} p. {r['page']}" if r["file_name"] else "",
        ]
        for r in data["coverage"]
    ]  # fmt: skip
    return table(
        ["Party", "Rank", "Name", "Amount", "Contract on file", "Expires within a year",
         "Change of control", "Auto-renew", "Lock-in left", "Notice period", "Contract"],
        rows,
        "Not loaded: no sales or purchase register in the last period (`fin contracts coverage`).",
    )  # fmt: skip


def visibility_table(data: dict[str, Any], unit: str) -> str:
    found = data["visibility"]
    if found is None:
        return (
            "Not loaded: no orderbook, billed-not-earned line or customer contract "
            "(`fin visibility`)."
        )
    rows = [
        [
            r["source"], str(r["items"]), str(r["unusable_items"]), _m(r["remaining_amount"], unit),
            _m(r["covered_next_year"], unit), _m(r["core_revenue"], unit),
            f"{r['covered_pct']}%" if r["covered_pct"] is not None else "n/a",
        ]
        for r in found["sources"]
    ]  # fmt: skip
    notes = "".join(
        f"\n\n- {n['source']}: {n['note']} ({n['items']} items)" for n in found["notes"]
    )
    if (dated := orderbook_date_note(found)) is not None:
        notes = f"\n\n- {_t(dated)}" + notes
    return table(
        ["Source (never added together)", "Items", "Not usable (left out)", "Remaining",
         "In the next year", "Last year's core revenue", "Covered"],
        rows,
        "",
    ) + notes  # fmt: skip


def walkthroughs_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            f"#{r['sample_id']}", r["kind"], r["period"], str(r["lines"]), str(r["lines_walked"]),
            str(r["checks_passed"]), str(r["exceptions"]), str(r["not_available"]),
            str(r["exception_lines"]), _m(r["exception_amount"], unit),
        ]
        for r in data["walkthroughs"]
    ]  # fmt: skip
    text = table(
        ["Sample", "Kind", "Period", "Lines", "Walked", "Checks passed", "Exceptions",
         "Not available", "Lines with an exception", "Exception amount"],
        rows,
        "Not loaded: no sample drawn or walked (`fin sample draw`, `fin walkthrough record`).",
    )  # fmt: skip
    lines = [
        [
            f"#{r['sample_id']}.{r['line_no']}", _t(r["party"]), _t(r["invoice_no"]),
            str(r["invoice_date"]), _m(r["amount_inr"], unit), _word(r["check_name"]),
            _t(r["evidence"]),
            _t(f"{r['evidence_file']} p. {r['evidence_page']}") if r["evidence_file"] else "",
        ]
        for r in data["walkthrough_exceptions"]
    ]  # fmt: skip
    if lines:
        text += (
            "\n\nExceptions Claude recorded ("
            + NOT_VERIFIED.lower()
            + "):\n\n"
            + table(
                [
                    "Sample line",
                    "Party",
                    "Invoice",
                    "Date",
                    "Amount",
                    "Check",
                    "Evidence",
                    "Source",
                ],
                lines,
                "",
            )
        )
    return text


def receivables_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"], r["bucket"], _m(r["open_inr"], unit), str(r["invoices"]),
            _ratio(r["share"]), _m(r["provision_inr"], unit), _ratio(r["coverage"]),
            _m(r["not_in_bank_run_inr"], unit),
        ]
        for r in data["provisions"]["receivables"]
    ]  # fmt: skip
    return table(
        ["Year end", "Age (days)", "Open", "Invoices", "Share", "Doubtful debts provision",
         "Coverage of this and older", "Invoices in no bank run (not aged)"],
        rows,
        "Not loaded: no bank receipts matched to invoices (`fin reconcile <co> bank_vs_invoices`).",
    )  # fmt: skip


def gratuity_table(data: dict[str, Any], unit: str) -> str:
    found = data["provisions"]
    rows = [
        [
            r["period"], str(r["employees"]), str(r["eligible"]), str(r["incomplete"]),
            _m(r["gratuity_basic_share"], unit), _m(r["gratuity_wage_code"], unit),
            _m(r["provision_inr"], unit), _m(r["shortfall_basic_share"], unit),
        ]
        for r in found["gratuity"]
    ]  # fmt: skip
    if not rows:
        return "Not loaded: no payroll snapshot confirmed annual (`fin payroll confirm-unit`)."
    a = found["assumptions"]
    note = (
        f"Gratuity is {a['label']}. Assumptions (config.toml [fdd]): basic pay is "
        f"{_ratio(a['basic_share'])} of gross salary, at least {a['gratuity_min_years']} completed "
        f"years, capped at {_m(a['gratuity_cap'], unit)} per employee."
    )
    return note + "\n\n" + table(
        ["Period", "Employees", "Eligible", "Incomplete (not computed)", "Gratuity (basic share)",
         "Gratuity (wage code)",
         "Provision", "Shortfall (basic share)"],
        rows,
        "",
    )  # fmt: skip


def tax_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"], _m(r["pbt"], unit), _m(r["current_tax"], unit),
            _m(r["deferred_tax"], unit), _m(r["total_tax"], unit), _ratio(r["effective_tax_rate"]),
            _m(r["net_deferred_tax"], unit), _m(r["deferred_tax_movement"], unit),
            _m(r["mat_credit"], unit),
        ]
        for r in data["tax"]["tax_charge"]
        if r["scope"] == data["scope"]
    ]  # fmt: skip
    return table(
        ["Period", "Profit before tax", "Current tax", "Deferred tax", "Total tax",
         "Effective tax rate", "Net deferred tax asset (liability)", "Movement in the year",
         "MAT credit"],
        rows,
        "Not loaded: no tax figures in the statements.",
    )  # fmt: skip


def gst_returns_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"], r["return_type"], str(r["months"]), _m(r["taxable_value"], unit),
            _m(r["output_tax"], unit), _m(r["itc"], unit), _m(r["rcm_liability"], unit),
            _m(r["paid_cash"], unit), _m(r["paid_credit"], unit), _t(r["file_name"]),
        ]
        for r in data["tax"]["returns"]
    ]  # fmt: skip
    return table(
        ["Period", "Return", "Months", "Taxable value", "Output tax", "Input tax credit",
         "Reverse charge", "Paid in cash", "Paid from credit", "File"],
        rows,
        "Not loaded: no GST returns (`fin ingest <co> <file> --kind gst`).",
    )  # fmt: skip


def gst_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["name"], r["period"], str(r["differences"]), str(r["at_or_above_threshold"]),
            str(r["in_a_query"]), _m(r["total_difference"], unit),
        ]
        for r in data["tax"]["reconciliations"]
    ]  # fmt: skip
    return table(
        ["GST reconciliation", "Period", "Lines that differ", "At or above the threshold",
         "In a query", "Total difference"],
        rows,
        "Not loaded: no GST return reconciled (`fin reconcile <co> gstr1_vs_sales_register`).",
    )  # fmt: skip


def _schedule(rows: list[dict[str, Any]], unit: str, empty: str) -> str:
    out = [
        [
            _word(r["kind"]), r["period"], r["counterparty"] or "", _t(r["nature"]),
            _m(r["amount"], unit) if r["amount"] is not None else "",
            " ".join(x for x in (r["forum"], r["stage"]) if x),
            f"{r['file_name']} p. {r['page']}",
        ]
        for r in rows
    ]  # fmt: skip
    if not out:
        return empty
    return (
        NOT_VERIFIED
        + "\n\n"
        + table(
            ["Kind", "Period", "Party", "Nature", "Amount", "Forum and stage", "Source"], out, ""
        )
    )


def contingencies_table(data: dict[str, Any], unit: str) -> str:
    found = data["disclosures"]
    rows = [r for kind, items in found["schedules"].items() if kind not in found["related_kinds"]
            for r in items]  # fmt: skip
    return _schedule(rows, unit, "Not loaded: no notes or notices read (`fin disclosures load`).")


def related_table(data: dict[str, Any], unit: str) -> str:
    found = data["disclosures"]
    rows = [r for kind in found["related_kinds"] for r in found["schedules"][kind]]
    text = _schedule(rows, unit, "Not loaded: no related party disclosure read.")
    books = [
        [r["party"], r["period"], r["source"], _t(r["nature"]), _m(r["amount"], unit)]
        for r in found["transactions"]
    ]
    if books:
        text += "\n\nIn the books (linked customers, vendors and ledgers):\n\n" + table(
            ["Party", "Period", "Source", "Nature", "Amount"], books, ""
        )
    pricing = [
        [
            _t(r["party"]), r["kind"], r["period"], _t(r["product"]), _rate(r["party_rate"]),
            _rate(r["others_rate"]), _pct(r["difference_pct"]),
        ]
        for r in found["pricing"]
    ]  # fmt: skip
    if pricing:
        text += "\n\nPricing against other customers and vendors (rate per unit):\n\n" + table(
            ["Party", "Kind", "Period", "Product", "Party rate", "Others' rate", "Difference"],
            pricing,
            "",
        )
    loans = [
        [
            _t(r["party"]), _t(r["ledger"]), r["period"], _m(r["opening"], unit),
            _m(r["closing"], unit), _m(r["interest"], unit), _pct(r["implied_rate_pct"]),
            r["average_basis"],
        ]
        for r in found["loans"]
    ]  # fmt: skip
    if loans:
        text += (
            "\n\nLoans with related parties (closing: debit less credit, so a loan given is "
            "positive and a borrowing negative):\n\n"
            + table(
                ["Party", "Ledger", "Period", "Opening", "Closing", "Interest", "Implied rate",
                 "Average on"],
                loans,
                "",
            )
        )  # fmt: skip
    return text


def inquiries_table(data: dict[str, Any]) -> str:
    rows = [
        [_word(t["topic"]), f"#{q['query_id']}", q["status"], _t(q["question"]), _t(q["answer"])]
        for t in data["inquiries"]
        for q in t["queries"]
    ]
    missing = [_word(t["topic"]) for t in data["inquiries"] if not t["active"]]
    text = table(
        ["Topic", "Query", "Status", "Question", "Answer"],
        rows,
        "No management inquiry raised (`fin query checklist`).",
    )
    if missing:
        text += "\n\nTopics with no inquiry: " + ", ".join(missing) + "."
    return text
