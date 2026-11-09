"""`fin.verify.compare`: what a reading is compared with."""

from decimal import Decimal
from pathlib import Path

from fin.extract.reading import read_reading
from fin.verify import Loaded, compare

HEADER = "statement,scope,period_end,period_months,unit,section,label,value_as_printed,source"


def reading(tmp_path: Path, *rows: str) -> list:
    path = tmp_path / "r.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return read_reading(path, 4)


def fact(fact_id: int, label: str, raw: str, value: str, per_share: bool = False) -> Loaded:
    statement = "profit_and_loss"
    return Loaded(
        fact_id, "fin", statement, "standalone", "FY25", "", label, raw, Decimal(value), per_share,
        "page 2",
    )  # fmt: skip


def test_compares_the_value_fin_uses_now_not_the_printed_text(tmp_path: Path) -> None:
    lines = reading(
        tmp_path,
        "profit_and_loss,standalone,2025-03-31,12,(Rs. in Lakhs),,Other income,28.60,page 2",
        "profit_and_loss,standalone,2025-03-31,12,(Rs. in Lakhs),,Finance costs,28.60,page 2",
    )
    loaded = [
        # Printed 28.50, corrected since to 28.60 lakh with `fin sql --write`.
        fact(1, "Other income", "28.50", "2860000.00"),
        # Printed 28.60, but the value fin uses was changed to 28.50 lakh.
        fact(2, "Finance costs", "28.60", "2850000.00"),
    ]

    outcomes = [(outcome, f.fact_id) for outcome, _, f in compare(lines, loaded) if f]

    assert outcomes == [("agree", 1), ("differ", 2)]


def test_a_per_share_figure_is_compared_as_printed_not_scaled_by_the_unit(tmp_path: Path) -> None:
    lines = reading(
        tmp_path,
        "profit_and_loss,standalone,2025-03-31,12,(Rs. in Lakhs),,Basic EPS,5.25,page 2",
        "profit_and_loss,standalone,2025-03-31,12,(Rs. in Lakhs),,Diluted EPS,5.20,page 2",
    )
    loaded = [
        fact(1, "Basic EPS", "5.25", "5.25", per_share=True),
        fact(2, "Diluted EPS", "5.25", "5.25", per_share=True),
    ]

    outcomes = [(outcome, f.fact_id) for outcome, _, f in compare(lines, loaded) if f]

    assert outcomes == [("agree", 1), ("differ", 2)]
