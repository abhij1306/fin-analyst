"""The diligence report's Markdown tables and sections, from the data `fin.report.dd_data.collect`
read. Python only formats: every value was computed in SQL. Describes, never judges: no
recommendation, rating or verdict anywhere. Names are tokens.

Each table says where its figures come from (a view, or file + sheet!row); the sources appendix
lists
the rows behind the totals.
"""

from typing import Any

from fin.diligence.stub import UNAUDITED
from fin.report.customers import customers_section
from fin.report.tables import metric_text, money, table

RECON_TITLES = {
    "tb_vs_statements": "Trial balance vs audited statements",
    "sales_register_vs_gl": "Sales register vs books",
    "purchase_register_vs_gl": "Purchase register vs books",
    "gl_vs_tb": "Ledger Vouchers vs trial balance",
    "payroll_vs_gl": "Payroll vs books",
    "bank_vs_invoices": "Bank receipts vs invoices",
    "bank_vs_tb": "Bank statements vs trial balance",
    "service_recast": "Service-period recast vs booked revenue",
}


def _m(value: Any, unit: str) -> str:
    return money(value, unit)


def _ratio(value: Any) -> str:
    return metric_text("ratio", value, "rupees")


def _label(period: str, stub: str | None) -> str:
    return f"{period} ({UNAUDITED})" if period == stub else period


def basis_text(data: dict[str, Any]) -> str:
    """What the report rests on: the periods, the stub and its label, the scope."""
    stub = data["stub"]
    line = (
        f"Engagement periods {', '.join(data['periods'])}; {data['scope']} statements for the "
        "audited years."
    )
    if stub:
        line += (
            f" The stub {stub} ({data['engagement']['stub_start']} to "
            f"{data['engagement']['stub_end']}) is {UNAUDITED}: it is shown on its own, never "
            "annualised, and is not in the audited base figures or growth."
        )
    return line


def gates_table(data: dict[str, Any]) -> str:
    return table(
        ["Gate", "State when rendered", "Why"],
        [[f"{g['gate']} {g['title']}", g["state"], g["reason"] or ""] for g in data["gates"]],
        "No gates.",
    )


def ebitda_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"],
            _m(r["revenue"], unit),
            _m(r["reported_ebitda"], unit),
            _m(r["accepted_adjustments"], unit),
            _m(r["adjusted_ebitda"], unit),
            _m(r["adjusted_revenue"], unit),
            _ratio(r["reported_ebitda_margin"]),
            _ratio(r["adjusted_ebitda_margin"]),
            _m(r["proposed_adjustments"], unit),
        ]
        for r in data["ebitda"]
    ]
    return table(
        ["Period", "Revenue", "Reported EBITDA", "Accepted adjustments", "Adjusted EBITDA",
         "Adjusted revenue", "Reported margin", "Adjusted margin", "Proposed, not applied"],
        rows,
        "No periods in the engagement.",
    )  # fmt: skip


def bridge_table(data: dict[str, Any], unit: str) -> str:
    rows = []
    for r in data["bridge"]:
        detail = r["description"]
        if r["kind"] == "adjustment":
            detail = f"{r['category']}: {r['description']} (adjustment #{r['adj_id']})"
        rows.append([r["period"], str(r["step"]), detail, _m(r["amount"], unit)])
    return table(["Period", "Step", "Item", "Amount"], rows, "No bridge.")


def adjustments_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            f"#{a['adj_id']}",
            a["period"],
            a["status"] if a["status"] == "accepted" else f"{a['status']} (not applied)",
            a["category"],
            _m(a["revenue_effect"], unit),
            _m(a["cost_effect"], unit),
            _m(a["amount"], unit),
            a["description"],
            "; ".join(
                x
                for x in (
                    f"query #{a['query_id']}" if a["query_id"] else "",
                    f"finding #{a['finding_id']}" if a["finding_id"] else "",
                    a["source_ref"] or "",
                )
                if x
            ),
        ]
        for a in data["adjustments"]
    ]
    return table(
        ["Id", "Period", "Status", "Category", "Revenue effect", "Cost effect", "EBITDA effect",
         "Description", "Evidence"],
        rows,
        "No adjustments are recorded.",
    )  # fmt: skip


def other_income_table(data: dict[str, Any], unit: str) -> str:
    return table(
        ["Period", "Other income (not in EBITDA)"],
        [
            [r["period"], _m(r["value"], unit)]
            for r in sorted(data["other_income"], key=lambda r: r["period"])
        ],
        "No other income is printed in the statements.",
    )


def revenue_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            _label(r["period"], data["stub"]),
            _m(r["sales_revenue"], unit),
            _m(r["core_revenue"], unit),
            _m(r["passthrough_revenue"], unit),
            _ratio(r["passthrough_share"]),
            _m(r["passthrough_licence_revenue"], unit),
            _m(r["passthrough_services_revenue"], unit),
            _m(r["linked_purchase_cost"], unit),
            _ratio(r["core_revenue_growth"]),
        ]
        for r in data["revenue_split"]
    ]
    return table(
        ["Period", "Sales register", "Core", "Pass-through (project revenue)", "Pass-through share",
         "Licence part", "Services part", "Linked OEM purchase cost", "Core growth"],
        rows,
        "No sales register is loaded.",
    )  # fmt: skip


def walk_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"],
            _m(r["ebitda"], unit),
            _m(r["adjustments"], unit),
            _m(r["adjusted_ebitda"], unit),
            _m(r["non_cash_and_other"], unit),
            _m(r["working_capital_change"], unit),
            _m(r["taxes_paid"], unit),
            _m(r["other_operating"], unit),
            _m(r["cfo"], unit),
            _m(r["capex"], unit),
            _m(r["free_cash_flow"], unit),
            _ratio(r["cash_conversion"]),
            _ratio(r["adjusted_cash_conversion"]),
        ]
        for r in data["walk"]
    ]
    return table(
        ["Period", "EBITDA", "Adjustments", "Adjusted EBITDA", "Non-cash and other",
         "Working capital change", "Taxes paid", "Other operating", "CFO", "Capex",
         "Free cash flow", "CFO / EBITDA", "CFO / adjusted EBITDA"],
        rows,
        "No cash flow statement is loaded.",
    )  # fmt: skip


def _nwc_cells(r: dict[str, Any], unit: str) -> list[str]:
    return [
        _m(r["trade_receivables"], unit),
        _m(r["inventories"], unit),
        _m(r["other_current_assets"], unit),
        _m(r["trade_payables"], unit),
        _m(r["other_current_liabilities"], unit),
        _m(r["net_working_capital"], unit),
    ]


NWC_HEADERS = ["Trade receivables", "Inventories", "Other current assets", "Trade payables",
               "Other current liabilities", "Net working capital"]  # fmt: skip


def nwc_year_end_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [_label(r["period"], data["stub"]), str(r["period_end"]), *_nwc_cells(r, unit),
         str(r["unmapped_ledgers"])]
        for r in data["nwc_year_end"]
    ]  # fmt: skip
    return table(
        ["Period", "As at", *NWC_HEADERS, "Ledgers not mapped"],
        rows,
        "No trial balance is loaded.",
    )


def nwc_monthly_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [r["period"], str(r["month_end"]), *_nwc_cells(r, unit), f"{r['ledgers_included']} in, "
         f"{r['ledgers_excluded']} out"]
        for r in data["nwc_monthly"]
    ]  # fmt: skip
    return table(
        ["Period", "Month end", *NWC_HEADERS, "Ledgers"],
        rows,
        "No monthly view: it needs the previous year's trial balance and Ledger Vouchers whose "
        "movements tie to the trial balance.",
    )


def nwc_excluded_table(data: dict[str, Any]) -> str:
    rows = [
        [r["period"], r["ledger"], r["account"], r["reason"]]
        for r in data["nwc_ledgers"]
        if r["status"] == "excluded"
    ]
    return table(
        ["Period", "Ledger", "Account", "Left out of the monthly view because"],
        rows,
        "Every working capital ledger is in the monthly view.",
    )


def recon_table(data: dict[str, Any]) -> str:
    rows = [
        [
            RECON_TITLES.get(r["name"], r["name"]),
            _label(r["period"], data["stub"]),
            str(r["matched"]),
            str(r["differences"]),
            str(r["left_only"]),
            str(r["right_only"]),
            str(r["fx"]),
            str(r["not_reconciled"] + r["unmapped_lines"]),
            f"{r['in_query']} of {r['needs_query']}",
            f"run {r['recon_id']}",
        ]
        for r in data["recon"]
    ]
    return table(
        ["Reconciliation", "Period", "Matched", "Differences", "Left only", "Right only", "FX",
         "Not compared", "Lines in a query", "Source"],
        rows,
        "No reconciliation has run.",
    )  # fmt: skip


def queries_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [f"#{q['query_id']}", q["period"] or "", q["status"], _m(q["amount"], unit), q["question"]]
        for q in data["queries"]
    ]
    return table(
        ["Query", "Period", "Status", "Amount at issue", "Question"], rows, "No queries are raised."
    )


def collections_table(data: dict[str, Any], unit: str) -> str:
    runs = {r["period"]: r for r in data["collection_runs"]}
    rows = [
        [
            _label(r["period"], data["stub"]),
            str(runs[r["period"]]["as_of"]) if r["period"] in runs else "",
            _m(r["invoiced_inr"], unit),
            _m(r["received_inr"], unit),
            _m(r["partly_paid_inr"], unit),
            _m(r["open_inr"], unit),
            _m(r["not_due_inr"], unit),
            _m(r["overdue_1_30_inr"], unit),
            _m(r["overdue_31_60_inr"], unit),
            _m(r["overdue_61_90_inr"], unit),
            _m(r["overdue_over_90_inr"], unit),
        ]
        for r in data["collections"]
    ]
    return table(
        ["Period", "Aged at", "Invoiced", "Received", "Partly paid", "Open", "Not yet due",
         "Overdue 1-30 days", "31-60", "61-90", "Over 90"],
        rows,
        "No bank statement is matched to invoices.",
    )  # fmt: skip


def collections_customers_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["period"],
            r["customer"],
            _m(r["invoiced_inr"], unit),
            _m(r["received_inr"], unit),
            _m(r["partly_paid_inr"], unit),
            _m(r["open_inr"], unit),
            "n/a" if r["collection_days"] is None else f"{r['collection_days']} days",
        ]
        for r in data["collections_by_customer"]
    ]
    return table(
        ["Period", "Customer", "Invoiced", "Received", "Partly paid", "Open", "Days to collect"],
        rows,
        "No bank statement is matched to invoices.",
    )


def stub_table(data: dict[str, Any], unit: str) -> str:
    rows = [
        [
            r["statement"] or "",
            r["account"] or "not mapped",
            _m(r["value"], unit),
            str(r["ledgers"]),
        ]
        for r in data["stub_figures"]
    ]
    return table(
        ["Statement", "Account", f"{UNAUDITED} (as filed)", "Ledgers"],
        rows,
        "The engagement has no stub, or its trial balance is not loaded.",
    )


def findings_table(data: dict[str, Any]) -> str:
    rows = [
        [f["status"], f["period"] or "", f["code"] or "", f["label"] or "", f["file_name"]]
        for f in data["findings"]
    ]
    return table(
        ["Status", "Period", "Check", "Item", "File"], rows, "No open or accepted findings."
    )


def mappings_text(data: dict[str, Any]) -> str:
    rows = data["ledgers_not_confirmed"]
    text = (
        "Ledgers not confirmed to an account: "
        + ", ".join(f"{r['ledgers']} {r['status']}" for r in rows)
        + ". Their balances are left out of every figure by account."
        if rows
        else "Every ledger is confirmed to an account."
    )
    if data["facts_not_confirmed"]:
        text += (
            f" Statement figures not confirmed to an account: {data['facts_not_confirmed']}; "
            "they are left out of every metric."
        )
    return text


def recast_section(data: dict[str, Any], unit: str) -> str:
    """The service-period recast (D6): per period the register's revenue spread over its service
    dates against what was booked by invoice date, by month, the invoices with no usable service
    dates, and the contracts Claude read (evidence only, never the schedule). Empty without a
    run."""
    if not data["recast"]:
        return ""
    periods = table(
        ["Period", "Recast", "Booked (by invoice date)", "Difference (revenue effect)", "Source"],
        [
            [r["period"], _m(r["recast"], unit), _m(r["booked"], unit), _m(r["difference"], unit),
             f"run {r['recon_id']}"]
            for r in data["recast"]
            if r["level"] == "period"
        ],
        "No recast has run.",
    )  # fmt: skip
    months = table(
        ["Period", "Month", "Recast", "Booked", "Difference"],
        [
            [
                r["period"],
                r["key"],
                _m(r["recast"], unit),
                _m(r["booked"], unit),
                _m(r["difference"], unit),
            ]
            for r in data["recast"]
            if r["level"] == "month"
        ],
        "No months.",
    )
    undated = table(
        ["Period", "Invoice", "Amount", "Note", "Source"],
        [
            [r["period"], r["key"], _m(r["recast"], unit), r["note"] or "", r["left_source"] or ""]
            for r in data["recast"]
            if r["kind"] == "undated"
        ],
        "Every sales invoice has usable service dates.",
    )
    contracts = table(
        ["Customer", "Invoice", "Start", "End", "Value", "Billing terms", "Read from"],
        [
            [c["customer"], c["invoice_no"] or "", str(c["start_date"]), str(c["end_date"]),
             _m(c["value"], unit), c["billing_terms"] or "", f"{c['file_name']} page {c['page']}"]
            for c in data["contracts"]
        ],
        "No contracts were read.",
    )  # fmt: skip
    return "\n\n".join(
        [
            "### Recast by period\n\n" + periods,
            "### Recast by month\n\n" + months,
            "### Invoices with no usable service dates\n\n" + undated,
            "### Contracts read (evidence only)\n\n" + contracts,
        ]
    )


def sources_appendix(data: dict[str, Any], unit: str) -> str:
    """The rows behind the totals: statement cells, register sheets, pass-through lines and the
    trial balance rows of the working capital."""
    facts = table(
        ["Account", "Period", "Value", "File", "Cell or page"],
        [
            [f["account"], f["period"], _m(f["value"], unit), f["file_name"], f["source_ref"]]
            for f in data["fact_sources"]
        ],
        "No statement figures.",
    )
    registers = table(
        ["Period", "File", "Sheet", "Sales lines", "Amount in Rs (before GST)"],
        [
            [r["period"], r["file_name"], r["sheet"], str(r["lines"]), _m(r["amount_inr"], unit)]
            for r in data["register_sources"]
        ],
        "No sales register.",
    )
    passthrough = table(
        [
            "Period",
            "Customer",
            "Invoice",
            "Amount",
            "Link",
            "Sales row",
            "Purchase",
            "Purchase row",
        ],
        [
            [
                r["period"],
                r["customer"],
                r["invoice_no"],
                _m(r["amount_inr"], unit),
                r["link"],
                f"{r['sales_file']} {r['sales_sheet']}!{r['sales_row']}",
                ""
                if r["purchase_invoice_no"] is None
                else (
                    f"{r['vendor']} {r['purchase_invoice_no']} {_m(r['purchase_amount_inr'], unit)}"
                ),
                ""
                if r["purchase_file"] is None
                else (f"{r['purchase_file']} {r['purchase_sheet']}!{r['purchase_row']}"),
            ]
            for r in data["passthrough_lines"]
        ],
        "No pass-through customers are recorded.",
    )
    ledgers = table(
        ["Period", "Account", "Ledger", "Balance", "Trial balance row"],
        [
            [
                _label(r["period"], data["stub"]),
                r["account"],
                r["ledger"],
                _m(r["balance"], unit),
                f"{r['file_name']} {r['sheet']}!{r['row']}",
            ]
            for r in data["nwc_year_end_lines"]
        ],
        "No working capital ledgers.",
    )
    openings = table(
        [
            "Period",
            "Ledger",
            "Included",
            "Opening (previous trial balance)",
            "Opening row",
            "Movements from",
        ],
        [
            [
                r["period"],
                r["ledger"],
                r["status"],
                _m(r["opening"], unit),
                r["opening_source"] or "not in the previous trial balance",
                r["ledger_vouchers_file"],
            ]
            for r in data["nwc_ledgers"]
        ],
        "No monthly working capital.",
    )
    return "\n\n".join(
        [
            "### Statement figures behind EBITDA and the walk\n\n" + facts,
            "### Sales register lines behind revenue\n\n" + registers,
            "### Pass-through lines and the purchase behind each\n\n" + passthrough,
            "### Working capital: trial balance rows\n\n" + ledgers,
            "### Working capital by month: openings and movements\n\n" + openings,
        ]
    )


def customers_block(data: dict[str, Any], unit: str) -> str:
    return customers_section(data, unit)
