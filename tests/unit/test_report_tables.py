"""The report's tables, from data shaped as `fin.report.data.collect` returns it: key-metric tables
show a metric only the other scope or a peer has (definitions come from `metric_info`), facts show
in their account's unit with every line of a sum, printed subtotals stay, and missing data covers
quarters."""

from decimal import Decimal
from typing import Any

from fin.report.render import substitute
from fin.report.tables import (
    coverage_tables,
    facts_table,
    peers_table,
    statements_tables,
    summary_tables,
)

METRIC_INFO = {
    "roce": {"metric": "roce", "label": "ROCE", "unit": "ratio", "times": False},
    "revenue_growth": {
        "metric": "revenue_growth",
        "label": "Revenue growth",
        "unit": "ratio",
        "times": False,
    },
}


def data() -> dict[str, Any]:
    # The main scope (consolidated) has no metrics, so `definitions` lists none.
    return {"scope": "consolidated", "metrics": {}, "definitions": [], "metric_info": METRIC_INFO}


def test_summary_shows_a_metric_only_the_other_scope_has() -> None:
    report = data()
    report["summary"] = {
        "standalone": {"years": ["FY25"], "rows": [["metric", "roce", {"FY25": Decimal("0.2")}]]}
    }

    assert (
        summary_tables(report, "crores")
        == "**Standalone**\n\n|  | FY25 |\n|---|---|\n| ROCE | 20.0% |"
    )


def fact(account: str, period: str, value: str, unit: str, source: str) -> dict[str, Any]:
    return {
        "scope": "standalone",
        "account": account,
        "period": period,
        "value": Decimal(value),
        "unit": unit,
        "file_name": "ar25.pdf",
        "source_ref": source,
    }


EPS = fact("eps_basic", "FY25", "12.34", "rupees_per_share", "page 2")
PAYABLES = fact("trade_payables", "FY26-Q1", "15000000", "rupees", "sum of 2: page 1; page 1")


def test_appendix_shows_eps_unscaled_and_every_line_of_a_sum() -> None:
    report = data() | {"appendix": [EPS, PAYABLES]}

    assert facts_table(report, "crores") == (
        "| Scope | Account | Period | Value | File | Source |\n|---|---|---|---|---|---|\n"
        "| standalone | `eps_basic` | FY25 | ₹12.34 | ar25.pdf | page 2 |\n"
        "| standalone | `trade_payables` | FY26-Q1 | ₹1.50 Cr | ar25.pdf "
        "| sum of 2: page 1; page 1 |"
    )


def test_a_fact_reference_to_eps_is_in_rupees_per_share() -> None:
    report = data() | {"periods": ["FY25"], "facts": [EPS]}

    text, _ = substitute("EPS was {{fact:eps_basic:FY25}}.", report, "crores")

    assert text == "EPS was ₹12.34."


def test_a_printed_subtotal_its_lines_do_not_explain_is_kept_and_marked() -> None:
    line = {"statement": "balance_sheet", "line_no": 20, "label": "Total non-current assets"}
    report = data() | {
        "years": ["FY24", "FY25"],
        "statements": [
            line | {"period": "FY24", "value": Decimal("5000000"), "complete": True},
            line | {"period": "FY25", "value": Decimal("6500000"), "complete": False},
        ],
    }

    assert statements_tables(report, "lakhs") == (
        "### Balance sheet\n\n|  | FY24 | FY25 |\n|---|---|---|\n"
        "| Total non-current assets | ₹50.00 lakh | ₹65.00 lakh † |\n\n"
        "† As printed; the lines under it that are mapped do not add up to it (some are not "
        "mapped, or not printed). `fin statements` shows what the lines explain."
    )


def test_missing_data_lists_a_quarter() -> None:
    report = data() | {
        "coverage": [],
        "unread": [],
        "unconfirmed": [],
        "missing": [
            {
                "scope": "standalone",
                "period": "FY25-Q2",
                "metric": "roce",
                "missing_inputs": "revenue (FY24-Q3)",
            }
        ],
    }

    assert coverage_tables(report)["missing"] == (
        "| Scope | Period | Metric | Missing inputs |\n|---|---|---|---|\n"
        "| standalone | FY25-Q2 | ROCE | revenue (FY24-Q3) |"
    )


def test_peers_show_a_metric_only_a_peer_has() -> None:
    report = data()
    report["peers"] = {
        "year": "FY25",
        "companies": ["acme", "kestrel"],
        "rows": {
            "acme": [],
            "kestrel": [["metric", "revenue_growth", {"FY25": Decimal("0.1")}]],
        },
    }

    assert peers_table(report, "crores") == (
        "Fiscal year FY25, consolidated.\n\n"
        "|  | acme | kestrel |\n|---|---|---|\n| Revenue growth | n/a | 10.0% |"
    )
