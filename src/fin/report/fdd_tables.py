"""The FDD report's own Markdown tables (templates/report/fdd.qmd), from the data
`fin.report.fdd_data.collect` read; the quality of earnings, working capital, reconciliation, gate
and source tables are the diligence report's (`fin.report.dd_tables`). Python only formats: every
value was computed in SQL. A section whose data is not loaded says so. Names are tokens; Claude's
readings are labelled as such, not verified.
"""

from typing import Any

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
NOT_VERIFIED = "Read by Claude from the documents (file + page); not verified by fin."


def _word(value: str) -> str:
    return LABELS.get(value, value.replace("_", " "))


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
        f"#### {r['title']} (finding #{r['finding_id']})" + (f" [{r['status']}]" if drafts else ""),
        "",
        r["observation"],
        "",
        f"- Area: {_word(r['area'])}; impact: {_word(r['impact_type'])}; amount: {amount}",
    ]
    if r["implication"]:
        lines.append(f"- Implication: {r['implication']}")
    lines.append(f"- Deal consideration: {_word(r['deal_consideration'])}")
    if r["query_id"] is not None:
        response = r["management_response"] or f"no answer yet (query {r['query_status']})"
        source = f"; {r['response_source']}" if r["response_source"] else ""
        lines.append(f"- Management response (query #{r['query_id']}{source}): {response}")
    else:
        lines.append("- Management response: no query raised")
    lines.append(f"- Evidence: {r['evidence']}")
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
            "yes" if r["expires_within_12m"] else "no",
            "yes" if r["change_of_control"] else "no",
            f"{r['file_name']} p. {r['page']}" if r["file_name"] else "",
        ]
        for r in data["coverage"]
    ]  # fmt: skip
    return table(
        ["Party", "Rank", "Name", "Amount", "Contract on file", "Expires within a year",
         "Change of control", "Contract"],
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
            r["source"], str(r["items"]), _m(r["remaining_amount"], unit),
            _m(r["covered_next_year"], unit), _m(r["core_revenue"], unit),
            f"{r['covered_pct']}%" if r["covered_pct"] is not None else "n/a",
        ]
        for r in found["sources"]
    ]  # fmt: skip
    return table(
        ["Source (never added together)", "Items", "Remaining", "In the next year",
         "Last year's core revenue", "Covered"],
        rows,
        "",
    )  # fmt: skip


def walkthroughs_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            f"#{r['sample_id']}", r["kind"], r["period"], str(r["lines"]), str(r["lines_walked"]),
            str(r["checks_passed"]), str(r["exceptions"]), str(r["not_available"]),
            _m(r["exception_amount"], unit),
        ]
        for r in data["walkthroughs"]
    ]  # fmt: skip
    return table(
        ["Sample", "Kind", "Period", "Lines", "Walked", "Checks passed", "Exceptions",
         "Not available", "Exception amount"],
        rows,
        "Not loaded: no sample drawn or walked (`fin sample draw`, `fin walkthrough record`).",
    )  # fmt: skip


def receivables_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"], r["bucket"], _m(r["open_inr"], unit), str(r["invoices"]),
            _ratio(r["share"]), _m(r["provision_inr"], unit), _ratio(r["coverage"]),
        ]
        for r in data["provisions"]["receivables"]
    ]  # fmt: skip
    return table(
        ["Year end", "Age (days)", "Open", "Invoices", "Share", "Doubtful debts provision",
         "Coverage of this and older"],
        rows,
        "Not loaded: no bank receipts matched to invoices (`fin reconcile <co> bank_vs_invoices`).",
    )  # fmt: skip


def gratuity_table(data: dict[str, Any], unit: str) -> str:
    found = data["provisions"]
    rows = [
        [
            r["period"], str(r["employees"]), str(r["eligible"]),
            _m(r["gratuity_basic_share"], unit), _m(r["gratuity_wage_code"], unit),
            _m(r["provision_inr"], unit), _m(r["shortfall_basic_share"], unit),
        ]
        for r in found["gratuity"]
    ]  # fmt: skip
    if not rows:
        return "Not loaded: no payroll snapshot confirmed annual (`fin payroll confirm-unit`)."
    a = found["assumptions"]
    note = (
        f"Gratuity is {a['label']}: basic pay is the stated share of gross salary "
        "(config.toml [fdd] basic_share), with the minimum years and the cap of [fdd]."
    )
    return note + "\n\n" + table(
        ["Period", "Employees", "Eligible", "Gratuity (basic share)", "Gratuity (wage code)",
         "Provision", "Shortfall (basic share)"],
        rows,
        "",
    )  # fmt: skip


def tax_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"], _m(r["pbt"], unit), _m(r["current_tax"], unit),
            _m(r["deferred_tax"], unit), _m(r["total_tax"], unit), _ratio(r["effective_tax_rate"]),
        ]
        for r in data["tax"]["tax_charge"]
        if r["scope"] == data["scope"]
    ]  # fmt: skip
    return table(
        ["Period", "Profit before tax", "Current tax", "Deferred tax", "Total tax",
         "Effective tax rate"],
        rows,
        "Not loaded: no tax figures in the statements.",
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
            _word(r["kind"]), r["period"], r["counterparty"] or "", r["nature"],
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
        [r["party"], r["period"], r["source"], r["nature"] or "", _m(r["amount"], unit)]
        for r in found["transactions"]
    ]
    if books:
        text += "\n\nIn the books (linked customers, vendors and ledgers):\n\n" + table(
            ["Party", "Period", "Source", "Nature", "Amount"], books, ""
        )
    return text


def inquiries_table(data: dict[str, Any]) -> str:
    rows = [
        [_word(t["topic"]), f"#{q['query_id']}", q["status"], q["question"], q["answer"] or ""]
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
