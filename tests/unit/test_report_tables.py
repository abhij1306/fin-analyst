"""The report's key-metric tables show a metric the main scope lacks: one the other scope or a peer
has. Definitions come from `metric_info` (every metric), as `fin.report.data.collect` returns it."""

from decimal import Decimal
from typing import Any

from fin.report.tables import peers_table, summary_tables

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
