"""FDD F4 (docs/fdd.md): net debt classes (defaults, Claude's proposals, the user's decisions),
debt-like items, the net_debt views, the show command and the golden kind, on a small synthetic
company."""

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import FinError
from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import netdebt, netdebt_items
from fin.evals import netdebt as netdebt_evals
from fin.evals.golden import KINDS, LEDGERS, NEEDS, Check
from fin.evals.score import fin_value
from fin.paths import company_db_path
from tests.fdd_netdebt_fixtures import seed

D = Decimal


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("f4") / "data"
    data.mkdir()
    write_default_config(data)
    ensure_database(company_db_path(data, "acme"), "company")
    with connect(company_db_path(data, "acme"), write=True) as con:
        seed(con)
    return data


@pytest.fixture
def root(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    monkeypatch.setenv("FIN_DATA_DIR", str(data))
    return data


def query(root: Path, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql, args or []).fetchall()


def code(exc: pytest.ExceptionInfo[FinError]) -> str:
    return exc.value.code


def classify(root: Path) -> None:
    """Claude proposes three classes; the user confirms two and leaves TDS Payable waiting."""
    ids = netdebt.propose(
        run(root),
        "acme",
        [
            {"ledger": "Gratuity Provision", "class": "debt_like", "reason": "employee benefit"},
            {"ledger": "CUST-0001", "class": "none", "reason": "trade receivable"},
            {"ledger": "TDS Payable", "class": "debt_like", "reason": "statutory due"},
        ],
    )
    netdebt.decide(run(root), "acme", ids[:2], "confirmed")


def test_defaults_come_from_the_confirmed_account_and_the_rest_is_unclassified(root: Path) -> None:
    got = query(
        root,
        "SELECT ledger, debt_class, class_source FROM netdebt_ledger_classes ORDER BY ledger",
    )
    assert got == [
        ("CUST-0001", None, None),
        ("Cash in hand", "cash", "default"),
        ("Gratuity Provision", None, None),
        ("HDFC Bank", "cash", "default"),
        ("Lease Liability", "debt", "default"),
        ("Sales", "none", "default"),
        ("Suspense", None, None),
        ("TDS Payable", None, None),
        ("Term Loan", "debt", "default"),
    ]


def test_net_debt_per_period_end_with_confirmed_classes_and_items(root: Path) -> None:
    classify(root)
    accepted = netdebt_items.propose(
        run(root), "acme", "FY25", D("200000"), "Gratuity shortfall", "fin provisions show FY25"
    )
    netdebt_items.propose(run(root), "acme", "FY25", D("75000"), "Contingency", "notes p. 12")
    netdebt_items.decide(run(root), "acme", [accepted["item_id"]], "accepted")
    got = query(
        root,
        "SELECT period, basis, cash, debt, debt_like_ledgers, debt_like_items, debt_like, "
        "net_debt, proposed_items, proposed_item_count, unclassified_ledgers, proposed_classes "
        "FROM net_debt ORDER BY n, period",
    )
    assert got == [
        ("FY24", "audited", D("500000"), D("1050000"), D("100000"), D("0"), D("100000"),
         D("650000"), D("0"), 0, 0, 0),
        ("FY25", "audited", D("-10000"), D("850000"), D("120000"), D("200000"), D("320000"),
         D("1180000"), D("75000"), 1, 2, 1),
        ("FY26-YTD-SEP", "unaudited", None, None, None, D("0"), None, None, D("0"), 0, None,
         None),
    ]  # fmt: skip
    lines = query(
        root,
        "SELECT line_kind, debt_class, coalesce(ledger, description), amount, applied, source "
        "FROM net_debt_lines WHERE period = 'FY25' ORDER BY line_kind, debt_class, 3",
    )
    assert lines == [
        ("item", "debt_like", "Contingency", D("75000"), False, "notes p. 12"),
        ("item", "debt_like", "Gratuity shortfall", D("200000"), True, "fin provisions show FY25"),
        ("ledger", "cash", "Cash in hand", D("10000"), True, "tb25.xlsx TB!3"),
        ("ledger", "cash", "HDFC Bank", D("-20000"), True, "tb25.xlsx TB!2"),
        ("ledger", "debt", "Lease Liability", D("50000"), True, "tb25.xlsx TB!5"),
        ("ledger", "debt", "Term Loan", D("800000"), True, "tb25.xlsx TB!4"),
        ("ledger", "debt_like", "Gratuity Provision", D("120000"), True, "tb25.xlsx TB!6"),
    ]


def test_a_confirmed_class_wins_over_the_default_and_a_reject_restores_it(root: Path) -> None:
    [class_id] = netdebt.propose(
        run(root), "acme", [{"ledger": "Lease Liability", "class": "none", "reason": "short lease"}]
    )
    debt = "SELECT debt FROM net_debt WHERE period = 'FY25'"
    assert query(root, debt) == [(D("850000"),)]  # a proposal is not applied
    netdebt.decide(run(root), "acme", [class_id], "confirmed")
    assert query(root, debt) == [(D("800000"),)]
    [row] = netdebt.decide(run(root), "acme", [class_id], "rejected")
    assert row["status"] == "rejected"
    assert query(root, debt) == [(D("850000"),)]


@pytest.mark.parametrize(
    "proposals",
    [
        [],
        [{"ledger": "Term Loan", "class": "loan", "reason": "x"}],
        [{"ledger": "Nope", "class": "debt", "reason": "x"}],
        [{"ledger": "Term Loan", "class": "debt", "reason": " "}],
        [{"ledger": "Term Loan", "class": "debt"}],
        [
            {"ledger": "Term Loan", "class": "debt", "reason": "x"},
            {"ledger": "Term Loan", "class": "none", "reason": "y"},
        ],
    ],
)
def test_bad_proposals_store_nothing(root: Path, proposals: Any) -> None:
    ev = run(root)
    with pytest.raises(FinError) as exc:
        netdebt.propose(ev, "acme", proposals)
    assert code(exc) == "E930"
    assert query(root, "SELECT count(*) FROM netdebt_classes") == [(0,)]


def test_only_the_user_changes_a_confirmed_class(root: Path) -> None:
    classify(root)
    ev = run(root)
    with pytest.raises(FinError) as exc:
        netdebt.propose(ev, "acme", [{"ledger": "CUST-0001", "class": "debt", "reason": "x"}])
    assert code(exc) == "E930"
    for ids, status in (([1], "confirmed"), ([99], "rejected")):
        with pytest.raises(FinError) as exc:
            netdebt.decide(ev, "acme", ids, status)
        assert code(exc) == "E931"
    waiting = netdebt.list_classes(run(root), "acme", open_only=True)
    assert [(r["ledger"], r["account"], r["default_class"]) for r in waiting] == [
        ("TDS Payable", "other_current_liabilities", None)
    ]
    assert "-> debt_like: statutory due" in netdebt.format_class(waiting[0])
    assert netdebt.format_class({**waiting[0], "company": "acme"}).startswith("acme net debt class")
    assert [r["ledger"] for r in netdebt.unclassified(run(root), "acme")] == [
        "Suspense",
        "TDS Payable",
    ]


@pytest.mark.parametrize(
    ("period", "amount", "description", "evidence"),
    [
        ("FY23", "1", "x", "y"),
        ("FY25", "0", "x", "y"),
        ("FY25", "1", " ", "y"),
        ("FY25", "1", "x", ""),
    ],
)
def test_bad_items_store_nothing(
    root: Path, period: str, amount: str, description: str, evidence: str
) -> None:
    ev, value = run(root), D(amount)
    with pytest.raises(FinError) as exc:
        netdebt_items.propose(ev, "acme", period, value, description, evidence)
    assert code(exc) == "E932"
    assert query(root, "SELECT count(*) FROM netdebt_items") == [(0,)]


def test_items_mask_names_and_follow_the_adjustment_flow(root: Path) -> None:
    item = netdebt_items.propose(
        run(root), "acme", "FY26-YTD-SEP", D("-5000.50"), "Advance from Acme  Traders", "q #3"
    )
    assert item["description"] == "Advance from CUST-0001"
    assert item["status"] == "proposed"
    ev, ids = run(root), [item["item_id"]]
    with pytest.raises(FinError) as exc:
        netdebt_items.decide(ev, "acme", ids, "rejected")
    assert code(exc) == "E933"
    [rejected] = netdebt_items.decide(run(root), "acme", ids, "rejected", "paid by Acme Traders")
    assert "rejected: paid by CUST-0001" in netdebt_items.format_item(rejected)
    with pytest.raises(FinError) as exc:
        netdebt_items.decide(ev, "acme", ids, "accepted")
    assert code(exc) == "E933"
    assert netdebt_items.list_items(run(root), "acme", "proposed") == []
    assert query(root, "SELECT count(*) FROM net_debt_lines WHERE line_kind = 'item'") == [(0,)]


def test_show_labels_the_stub_and_the_proposed_items(root: Path) -> None:
    classify(root)
    netdebt_items.propose(run(root), "acme", "FY25", D("75000"), "Contingency", "notes p. 12")
    data = netdebt.show(run(root), "acme", None)
    assert [r["period"] for r in data["net_debt"]] == ["FY24", "FY25", "FY26-YTD-SEP"]
    text = netdebt.format_show(data)
    assert "FY26-YTD-SEP (unaudited management accounts): no trial balance loaded" in text
    assert "proposed items, not applied: 1" in text
    assert "2 ledgers unclassified: net debt may be incomplete" in text
    assert "1 class proposals wait for the user (not applied" in text
    assert "item #1 [proposed] Contingency (notes p. 12)" in text
    one = netdebt.show(run(root), "acme", "FY24")
    assert [r["period"] for r in one["net_debt"]] == ["FY24"]
    assert {line["period"] for line in one["lines"]} == {"FY24"}
    ev = run(root)
    with pytest.raises(FinError) as exc:
        netdebt.show(ev, "acme", "FY23")
    assert code(exc) == "E934"


def test_show_needs_an_engagement(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    ensure_database(company_db_path(tmp_path, "empty"), "company")
    ev = run(tmp_path)
    with pytest.raises(FinError) as exc:
        netdebt.show(ev, "empty", None)
    assert code(exc) == "E934"


def check(part: str) -> Check:
    return Check(2, "net_debt", "FY25", "", "", part, "", "rupees", "", False)


def test_golden_kind_net_debt(root: Path) -> None:
    classify(root)
    assert "net_debt" in KINDS
    assert NEEDS["net_debt"] == ("period", "ledger")
    assert LEDGERS["net_debt"] == ("cash", "debt", "debt_like", "net_debt")

    def row(kind: str, period: str, source: str, **values: str) -> dict[str, str]:
        return {"kind": kind, "period": period, "source": source, **values}

    with connect(company_db_path(root, "acme")) as con:
        lines = netdebt_evals.candidates(con, row)
        values = {
            part: fin_value(con, check(part)) for part in ("cash", "debt", "debt_like", "net_debt")
        }
    assert [(r["period"], r["ledger"]) for r in lines][:4] == [
        ("FY24", "cash"), ("FY24", "debt"), ("FY24", "debt_like"), ("FY24", "net_debt"),
    ]  # fmt: skip
    assert len(lines) == 8
    assert lines[0]["source"].startswith("tb24.xlsx")
    assert values == {
        "cash": D("-10000"),
        "debt": D("850000"),
        "debt_like": D("120000"),
        "net_debt": D("980000"),
    }
