"""FDD F5 (fin.disclosures) on the synthetic set of tests/disclosure_fixtures.py: the reading loads
(a register customer as its token, other names as read), bad readings are refused whole, related
parties match the registers by token and the ledgers by name, links need the user, rpt_vs_books
finds the planted differences, and the pricing and loan views compute."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import DiligenceTableWrite, DisclosureReadingInvalid, RelatedPartyLinkInvalid
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence import ledgers
from fin.diligence.sources import ingest_source
from fin.disclosures import load, related
from fin.disclosures.data import section_data
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.query.write import check_write_sql
from fin.reconcile import run_new
from tests.disclosure_fixtures import ACCOUNTS, LOAN, RAVI, ROWS, row, write_reading, write_set
from tests.tally_fixtures import CUSTOMERS


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "acme"), "company")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    files = write_set(root.parent)
    for key, kind in (
        ("tb24", "trial_balance"),
        ("tb25", "trial_balance"),
        ("lv", "ledger_vouchers"),
        ("registers", "registers"),
    ):
        ingest_source(run(root), load_config(root), "acme", files[key], kind, None)
    proposals = [
        {"ledger": k, "account": v, "confidence": 0.9, "reason": "by name"}
        for k, v in ACCOUNTS.items()
    ]
    ledgers.propose(run(root), "acme", proposals)
    ledgers.confirm(
        run(root), "acme", [r["ledger_id"] for r in ledgers.list_ledgers(run(root), "acme")]
    )
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def loaded(root: Path, tmp_path: Path) -> list[dict[str, Any]]:
    notes = tmp_path / "notes.pdf"
    notes.write_bytes(b"%PDF-1.4 synthetic notes to accounts")
    return load.load(run(root), "acme", notes, write_reading(tmp_path / "reading.csv"))


def sql(root: Path, query: str) -> list[dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        return fetch_dicts(con, query)


def lines(root: Path) -> dict[str, dict[str, Any]]:
    [result] = run_new(run(root), "acme", "rpt_vs_books", "FY25")
    return {line["key"]: line for line in result["lines"]}


def test_the_reading_loads_in_rupees_with_register_names_as_tokens(
    root: Path, tmp_path: Path
) -> None:
    rows = loaded(root, tmp_path)
    by_nature = {row["nature"]: row for row in rows}
    text = load.format_schedules(rows)

    assert len(rows) == len(ROWS)
    assert {row["counterparty"] for row in rows} == {"CUST-0001", RAVI, None}
    assert by_nature[f"Bank guarantee given for {CUSTOMERS[0]}"]["amount"] == 2000000  # as read
    tax = by_nature["Income tax demand under appeal"]
    assert (tax["demand"], tax["paid_under_protest"], tax["forum"]) == (1250000, 250000, "CIT(A)")
    assert "not verified" in text and "tax_matter (1)" in text
    assert len(loaded(root, tmp_path)) == len(ROWS)  # the same document again replaces its rows


def test_a_bad_reading_is_refused_whole(root: Path, tmp_path: Path) -> None:
    bad = [
        row("guarantee", "x", 1, period="FY23"),
        row("unknown", "x", 1),
        row("related_party", "x", 1, relationship="Director"),
        row("related_party_transaction", "x", 1, counterparty="A", amount="5", books="debit"),
        row("related_party_transaction", "x", 1, counterparty="A", amount="5", books="debit",
            ledger="No Such Ledger"),
    ]  # fmt: skip
    notes = tmp_path / "notes.pdf"
    notes.write_bytes(b"x")

    with pytest.raises(DisclosureReadingInvalid) as first:
        load.load(run(root), "acme", notes, write_reading(tmp_path / "bad.csv", bad[:4]))
    with pytest.raises(DisclosureReadingInvalid) as second:
        load.load(run(root), "acme", notes, write_reading(tmp_path / "bad2.csv", bad[4:]))

    message = str(first.value)
    assert "line 2: period 'FY23'" in message and "line 3: kind 'unknown'" in message
    assert "line 4: a related_party row needs a counterparty" in message
    assert "line 5: ledger goes with books debit or credit" in message
    assert "line 2: the ledger is in no loaded" in str(second.value)
    assert "No Such Ledger" not in str(second.value)
    assert sql(root, "SELECT * FROM disclosures") == []


def test_rpt_vs_books_finds_the_planted_differences(root: Path, tmp_path: Path) -> None:
    loaded(root, tmp_path)
    matches = sql(root, "SELECT party, link_kind, target, status FROM related_party_matches")

    found = lines(root)

    assert {
        "party": "CUST-0001",
        "link_kind": "customer",
        "target": "CUST-0001",
        "status": "matched",
    } in matches
    # the customer's Tally ledger carries its name; it meets the register token through mask_values
    assert {
        "party": "CUST-0001",
        "link_kind": "ledger",
        "target": CUSTOMERS[0],
        "status": "matched",
    } in matches
    sales = found["CUST-0001 sales"]
    assert (sales["left_amount"], sales["right_amount"], sales["difference"]) == (
        1000000,
        860000,
        140000,
    )
    assert sales["needs_query"] and sales["left_source"] == "notes.pdf p.13"
    loan = found[f"{RAVI} credit {LOAN}"]
    assert (loan["difference"], loan["needs_query"]) == (-60000, False)
    unlinked = found[f"{RAVI} sales"]
    assert unlinked["kind"] == "not_reconciled" and "no customer is linked" in unlinked["note"]


def test_links_count_only_once_the_user_confirms(root: Path, tmp_path: Path) -> None:
    loaded(root, tmp_path)
    proposals = [
        {"party": RAVI, "kind": "customer", "target": "CUST-0002"},
        {"party": RAVI, "kind": "ledger", "target": LOAN},
    ]
    proposals = [p | {"confidence": 0.8, "reason": "notes"} for p in proposals]
    assert related.propose(run(root), "acme", proposals) == 2
    assert lines(root)[f"{RAVI} sales"]["kind"] == "not_reconciled"

    ids = [row["link_id"] for row in related.list_links(run(root), "acme")]
    related.decide(run(root), "acme", ids, "confirmed")
    sales = lines(root)[f"{RAVI} sales"]
    loans = {row["period"]: row for row in sql(root, "SELECT * FROM related_party_loans")}
    loan = loans["FY25"]

    assert (sales["kind"], sales["difference"], sales["needs_query"]) == (
        "difference",
        -150000,
        True,
    )
    assert (loan["party"], loan["opening"], loan["closing"]) == (RAVI, -400000, -600000)
    assert (loan["average_balance"], loan["interest"], loan["implied_rate_pct"]) == (
        -500000,
        50000,
        10,
    )
    assert (loans["FY24"]["interest"], loans["FY24"]["implied_rate_pct"]) == (None, None)
    assert related.list_links(run(root), "acme") == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"party": "CUST-0002"}, "is not a related party"),
        ({"target": "CUST-0099"}, "is not a customer"),
        ({"target": "CUST-0001", "party": "CUST-0001"}, "already matched"),
        ({"confidence": 2}, "confidence"),
        ({"kind": "lender"}, "kind must be one of"),
    ],
)
def test_a_bad_link_proposal_stores_nothing(
    root: Path, tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    loaded(root, tmp_path)
    proposal = {"party": RAVI, "kind": "customer", "target": "CUST-0002"}
    proposal |= {"confidence": 0.5, "reason": "r", **change}

    with pytest.raises(RelatedPartyLinkInvalid, match=message):
        related.propose(run(root), "acme", [proposal])
    with pytest.raises(RelatedPartyLinkInvalid, match="no related party link with id 7"):
        related.decide(run(root), "acme", [7], "confirmed")
    assert sql(root, "SELECT * FROM related_party_links") == []


def test_pricing_compares_a_related_party_with_the_other_customers(
    root: Path, tmp_path: Path
) -> None:
    loaded(root, tmp_path)

    [support] = sql(root, "SELECT * FROM related_party_pricing WHERE product = 'Support'")

    assert (support["party"], support["party_rate"], support["others_rate"]) == (
        "CUST-0001",
        140000,
        125000,
    )
    assert support["difference_pct"] == 12


def test_the_report_data_and_the_write_guard(root: Path, tmp_path: Path) -> None:
    loaded(root, tmp_path)
    lines(root)

    with connect(company_db_path(root, "acme")) as con:
        data = section_data(con, 1)
        with pytest.raises(DiligenceTableWrite):
            check_write_sql(con, "DELETE FROM disclosures")

    assert len(data["schedules"]["related_party_transaction"]) == 3
    assert {line["key"] for line in data["rpt_vs_books"]} >= {"CUST-0001 sales"}
    assert data["pricing"] and data["matches"] and data["loans"] == []


def test_a_counterparty_named_like_a_ledger_becomes_that_ledger(root: Path, tmp_path: Path) -> None:
    reading = [
        row("related_party_transaction", "Loan taken", 3, counterparty=LOAN, amount="150000",
            books="credit", ledger=LOAN),
    ]  # fmt: skip
    notes = tmp_path / "notes.pdf"
    notes.write_bytes(b"other notes")

    [item] = load.load(run(root), "acme", notes, write_reading(tmp_path / "r.csv", reading))

    assert (item["counterparty"], item["ledger"]) == (LOAN, LOAN)


def test_review_decides_only_the_open_engagements_links(root: Path, tmp_path: Path) -> None:
    loaded(root, tmp_path)
    proposal = {"party": RAVI, "kind": "customer", "target": "CUST-0002"}
    related.propose(run(root), "acme", [proposal | {"confidence": 0.5, "reason": "r"}])
    [link] = related.list_links(run(root), "acme")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE related_party_links SET engagement_id = 99")

    with pytest.raises(RelatedPartyLinkInvalid, match="in the open engagement"):
        related.decide(run(root), "acme", [link["link_id"]], "confirmed")
