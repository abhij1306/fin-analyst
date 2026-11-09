"""fin.diligence.bridge.format_bridge: the text of `fin engagement bridge`."""

from datetime import datetime
from decimal import Decimal

from fin.diligence.bridge import format_bridge

D = Decimal


def test_lines_without_a_value_name_their_missing_inputs() -> None:
    walk = {
        "period": "FY26",
        "scope": "consolidated",
        "ebitda": D("1500000.00"),
        "non_cash_and_other": D("-20000.00"),
        "working_capital_change": None,
        "taxes_paid": D("-300000.00"),
        "other_operating": None,
        "cfo": D("900000.00"),
        "capex": None,
        "free_cash_flow": None,
        "cash_conversion": D("0.600000"),
        "adjusted_cash_conversion": D("0.562500"),
        "cf_op_profit_before_wc": D("1480000.00"),
        "cf_cash_generated": None,
        "cf_taxes_paid": D("-300000.00"),
    }
    result = {
        "company": "acme",
        "unit": "rupees",
        "engagement": {
            "engagement_id": 4,
            "status": "open",
            "periods": "FY26",
            "query_threshold": D("100000.00"),
            "opened_at": datetime(2026, 11, 9),
            "closed_at": None,
        },
        "adjusted_ebitda": [
            {"period": "FY26", "scope": "consolidated", "proposed_count": 0},
        ],
        "bridge": [
            {"period": "FY26", "scope": "consolidated", "kind": "reported", "adj_id": None,
             "category": None, "description": "Reported EBITDA", "amount": D("1500000.00")},
            {"period": "FY26", "scope": "consolidated", "kind": "adjustment", "adj_id": 9,
             "category": "run_rate", "description": "New plant", "amount": D("100000.00")},
            {"period": "FY26", "scope": "consolidated", "kind": "adjusted", "adj_id": None,
             "category": None, "description": "Adjusted EBITDA", "amount": D("1600000.00")},
        ],
        "walk": [walk],
    }  # fmt: skip

    lines = [" ".join(line.split()) for line in format_bridge(result).splitlines()]

    assert lines == [
        "acme: engagement #4 open: periods FY26, query threshold ₹100,000.00, opened 2026-11-09",
        "",
        "acme consolidated FY26: EBITDA bridge (₹)",
        "Reported EBITDA 1,500,000.00",
        "#9 run_rate: New plant 100,000.00",
        "Adjusted EBITDA 1,600,000.00",
        "acme consolidated FY26: EBITDA to cash (₹)",
        "EBITDA 1,500,000.00",
        "Non-cash and other items -20,000.00",
        "Working capital change - (missing: cf_cash_generated)",
        "Taxes paid -300,000.00",
        "Other operating items - (missing: cf_cash_generated)",
        "Cash from operations 900,000.00",
        "Capex - (missing: cf_capex)",
        "Free cash flow - (missing: cf_capex)",
        "Cash conversion (CFO / EBITDA) 60.00%",
        "Adjusted cash conversion (CFO / adjusted EBITDA) 56.25%",
    ]
