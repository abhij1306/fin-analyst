"""Registers (fin.diligence.registers): a synthetic billing-system workbook loads with sheet!row
lineage and masked names; a planted duplicate invoice is a finding, not a failure; what cannot load
is refused with its code and nothing is loaded."""

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import LayoutUnknown, ParseError, RegisterLineInvalid
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence.sources import ingest_source
from fin.paths import company_db_path
from tests.register_fixtures import HANDLERS, sales_line, write_registers
from tests.tally_fixtures import CUSTOMERS, VENDORS, write_trial_balance

REAL_NAMES = (*CUSTOMERS, *VENDORS, *HANDLERS)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with company acme and an open engagement for FY25 (made once)."""
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def load(root: Path, path: Path, profile: str | None = None) -> list[str]:
    run = Run("ingest", {}, root)
    return ingest_source(run, load_config(root), "acme", path, "registers", profile).lines


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def test_registers_load_with_lineage_and_masked_names(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_registers(tmp_path / "reg.xlsx"))

    assert lines[0] == "loaded as source file_id 1 (registers FY25, profile billing_registers)"
    assert lines[1].startswith(
        "registers FY25 (2024-05-11 to 2024-06-02): 3 sales lines, 2 purchase lines, "
        "6 names newly masked, 0 findings"
    )
    assert rows(
        root, "SELECT s.kind, s.period, s.basis, s.status, s.profile, s.period_start, s.period_end "
        "FROM source_files s"
    ) == [("registers", "FY25", "audited", "loaded", "billing_registers",
           date(2024, 5, 11), date(2024, 6, 2))]  # fmt: skip
    assert rows(
        root,
        "SELECT sheet, \"row\", kind, invoice_no, customer, vendor, handler, submitted_by, "
        "amount_inr, currency, purchase_id FROM register_lines ORDER BY sheet DESC, \"row\"",
    )[:2] == [
        ("Sales Register", 2, "sales", "INV-1001", "CUST-0001", None, "USER-0001", "USER-0002",
         120000, "INR", "P1"),
        ("Sales Register", 3, "sales", "INV-1002", "CUST-0002", None, "USER-0001", "USER-0002",
         80000, "INR", None),
    ]  # fmt: skip
    assert rows(
        root,
        "SELECT vendor, amount_inr FROM register_lines WHERE kind = 'purchase' ORDER BY \"row\"",
    ) == [("VEND-0001", 90000), ("VEND-0002", 40000)]
    stored = " ".join(str(r) for r in rows(root, "SELECT * FROM register_lines"))
    for name in REAL_NAMES:
        assert name not in stored
        assert name not in " ".join(lines)
    assert "Licence for CUST-0001" in stored  # a name inside a free-text column
    assert rows(root, "SELECT count(*) FROM mask_values") == [(6,)]
    usd = rows(
        root,
        'SELECT currency, currency_rate, amount, amount_inr FROM register_lines WHERE "row" = 4 '
        "AND kind = 'sales'",
    )
    assert [tuple(float(x) if x != "USD" else x for x in r) for r in usd] == [
        ("USD", 80.0, 2000.0, 160000.0)
    ]


def test_a_planted_duplicate_invoice_is_a_finding_and_both_rows_stay(
    root: Path, tmp_path: Path
) -> None:
    lines = load(root, write_registers(tmp_path / "reg.xlsx", duplicate=True))

    assert "2 findings" in lines[1]
    assert "2 invoice numbers appear more than once" in " ".join(lines)
    found = rows(
        root,
        "SELECT check_name, finding, statement, period, label, fin_value, difference, fin_source, "
        "other_source, status, file_id FROM findings ORDER BY finding_id",
    )
    assert [(f[0], f[1], f[2], f[3], f[4], f[7], f[8], f[9], f[10]) for f in found] == [
        ("register_duplicate_invoice", "duplicate_invoice_no", "sales_register", "FY25",
         "INV-1002 (Sales Register!5)", "Sales Register!5", "Sales Register!3", "open", 1),
        ("register_duplicate_invoice", "duplicate_invoice_no", "purchase_register", "FY25",
         "VEND-0001 PINV-71 (Purchase Register!4)", "Purchase Register!4",
         "Purchase Register!2", "open", 1),
    ]  # fmt: skip
    assert [(float(f[5]), float(f[6])) for f in found] == [(80000.0, 80000.0), (90000.0, 90000.0)]
    assert rows(root, "SELECT count(*) FROM register_lines") == [(7,)]
    message = rows(root, "SELECT message FROM findings ORDER BY finding_id")[0][0]
    assert "'INV-1002' is on Sales Register!3 and again on Sales Register!5" in message


def test_the_same_purchase_number_from_another_vendor_is_not_a_duplicate(
    root: Path, tmp_path: Path
) -> None:
    lines = load(root, write_registers(tmp_path / "reg.xlsx", same_number_other_vendor=True))

    assert "0 findings" in lines[1]
    assert rows(root, "SELECT count(*) FROM findings") == [(0,)]


def test_a_later_registers_file_replaces_the_earlier_and_its_findings(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_registers(tmp_path / "a.xlsx", duplicate=True))
    lines = load(root, write_registers(tmp_path / "b.xlsx"))

    assert "supersedes file_id 1" in lines[0]
    assert rows(root, "SELECT status FROM source_files ORDER BY file_id") == [
        ("superseded",), ("loaded",)
    ]  # fmt: skip
    assert rows(root, "SELECT DISTINCT status FROM findings") == [("superseded",)]


def test_lines_outside_the_engagement_are_loaded_and_counted(root: Path, tmp_path: Path) -> None:
    late = sales_line(9, CUSTOMERS[0], 1000, **{"Invoice Date": "05-06-2025"})
    lines = load(root, write_registers(tmp_path / "reg.xlsx", sales_extra=[late]))

    assert "(registers FY25-FY26, profile billing_registers)" in lines[0]
    assert "1 lines have an invoice date outside the engagement's periods" in " ".join(lines)
    assert rows(root, "SELECT count(*) FROM register_lines") == [(6,)]


def test_a_layout_no_profile_reads_is_e807_and_lists_headers_not_rows(
    root: Path, tmp_path: Path
) -> None:
    only_sales = write_registers(tmp_path / "s.xlsx", sheets=("sales",))
    with pytest.raises(LayoutUnknown) as error:
        load(root, only_sales)
    message = str(error.value)
    assert message.startswith("E807: s.xlsx: no registers column profile matches its headers")
    assert "Bill Id" in message
    for name in REAL_NAMES:
        assert name not in message
    with pytest.raises(LayoutUnknown, match="reads trial_balance, not registers"):
        load(root, only_sales, "tally_trial_balance")
    with pytest.raises(LayoutUnknown):
        load(root, write_trial_balance(tmp_path / "tb.xlsx"))
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"Invoice Date": "2024/05/11"}, "invoice date '2024/05/11' is not a date"),
        ({"Invoice No": None}, "no invoice no on this sales register row"),
        ({"Customer Name": None}, "no customer on this sales register row"),
        ({"Amount in Rs": None}, "no Amount in Rs"),
        ({"Service Start Date": "soon"}, "service start 'soon' is not a date"),
    ],
)
def test_an_unreadable_line_is_e813_and_nothing_is_loaded(
    root: Path, tmp_path: Path, over: dict[str, Any], why: str
) -> None:
    bad = sales_line(5, CUSTOMERS[0], 1000, **over)
    with pytest.raises(RegisterLineInvalid) as error:
        load(root, write_registers(tmp_path / "reg.xlsx", sales_extra=[bad]))

    assert str(error.value).startswith("E813: reg.xlsx Sales Register!5: ")
    assert why in str(error.value)
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]
    assert rows(root, "SELECT count(*) FROM mask_values") == [(0,)]


def test_a_number_that_is_not_a_number_is_e204(root: Path, tmp_path: Path) -> None:
    bad = sales_line(5, CUSTOMERS[0], 1000, Quantity="two")
    with pytest.raises(ParseError, match="'two' is not a number"):
        load(root, write_registers(tmp_path / "reg.xlsx", sales_extra=[bad]))


def test_every_repeat_of_an_invoice_is_its_own_finding(root: Path, tmp_path: Path) -> None:
    again = [sales_line(2, CUSTOMERS[1], 80000), sales_line(2, CUSTOMERS[1], 80000)]
    lines = load(root, write_registers(tmp_path / "reg.xlsx", sales_extra=again))

    assert "2 findings" in lines[1]
    assert rows(root, "SELECT fin_source FROM findings ORDER BY finding_id") == [
        ("Sales Register!5",), ("Sales Register!6",)
    ]  # fmt: skip


def test_a_dated_invoice_named_total_is_a_line_and_an_undated_total_row_is_not(
    root: Path, tmp_path: Path
) -> None:
    named = sales_line(7, CUSTOMERS[0], 500, Product="Total", **{"Customer Name": "Total"})
    lines = load(root, write_registers(tmp_path / "reg.xlsx", sales_extra=[named]))

    assert "4 sales lines, 2 purchase lines" in lines[1]
