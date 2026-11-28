"""The report's customers, sectors and pricing section (D9): read from the views on the registers
(db/schema/diligence_analytics.sql, read by `fin.report.data`) and formatted as Markdown tables. A
company with no engagement or no sales register in it has no such section. Python only formats:
every figure and share was computed in SQL. Describes, never judges: no narrative placeholder, no
references to type.

`customers_section` is what the diligence report (D7) includes as it is.
"""

from decimal import Decimal
from typing import Any

from fin.config import Display
from fin.report.tables import metric_text, money, table


def _share(value: Decimal | None) -> str:
    return metric_text("ratio", value, None)


def customers_section(data: dict[str, Any], display: Display) -> str:
    """The section as Markdown (empty when the company has none)."""
    rows = data.get("customers")
    if not rows:
        return ""

    def money_of(value: Decimal | None) -> str:
        return money(value, display)

    concentration = table(
        ["Period", "Segment", "Customers", "Revenue", "Top 1", "Top 5", "Top 10"],
        [
            [r["period"], r["segment"], str(r["customers"]), money_of(r["revenue"])]
            + [_share(r[f"top{n}_share"]) for n in (1, 5, 10)]
            for r in rows["concentration"]
        ],
        "No sales register lines.",
    )
    retention = table(
        ["Period", "Customers last year", "Kept", "Lost", "New", "Net", "Gross", "Logo"],
        [
            [
                r["period"],
                str(r["prior_customers"]),
                str(r["retained_customers"]),
                str(r["lost_customers"]),
                str(r["new_customers"]),
                _share(r["net_retention"]),
                _share(r["gross_retention"]),
                _share(r["logo_retention"]),
            ]
            for r in rows["retention"]
        ],
        "No period has a previous annual period in the engagement.",
    )
    sectors = table(
        ["Period", "Segment", "Sector", "Customers", "Revenue", "Share"],
        [
            [r["period"], r["segment"], r["sector"], str(r["customers"]), money_of(r["revenue"])]
            + [_share(r["share"])]
            for r in rows["sectors"]
        ],
        "No sales register lines.",
    )
    pvm = table(
        ["Period", "Price", "Volume", "Mix", "New products", "Discontinued", "Change"],
        [
            [r["period"]]
            + [
                money_of(r[k])
                for k in (
                    "price_effect",
                    "volume_effect",
                    "mix_effect",
                    "new_revenue",
                    "discontinued_revenue",
                    "change",
                )
            ]  # fmt: skip
            for r in rows["pvm"]
        ],
        "No product has a quantity and a rate in two consecutive annual periods.",
    )
    return "\n\n".join(
        [
            "## Customers, sectors and pricing",
            "From the sales register (Amount in Rs, before GST); customers are tokens. Core and "
            "pass-through revenue are shown separately. Retention and price, volume and mix are "
            "core revenue in annual periods only; the current-year stub is never included.",
            "### Customer concentration\n\nShare of the segment's revenue from its largest "
            "customers.\n\n" + concentration,
            "### Retention of core customers\n\nAgainst the previous annual period: net and "
            "gross revenue retention and the share of customers kept (logo).\n\n" + retention,
            "### Sector mix\n\nThe sector is the one the user confirmed; the rest is "
            "unmapped.\n\n" + sectors,
            "### Price, volume and mix\n\nChange in the revenue of core products with a "
            "quantity and a rate, in consecutive annual periods.\n\n" + pvm,
        ]
    )
