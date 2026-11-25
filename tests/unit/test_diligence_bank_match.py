"""bank_vs_invoices and bank_vs_tb (fin.reconcile.bank): the synthetic matching answer key.

Receipts (bank credits) against sales invoices in fixed passes, on rows inserted directly (the
loaders are tested in test_bank and test_diligence_registers). The key below says which pass
makes each match, which lines are left over, the USD receipt's FX line, the queries drafted, the
collections view, proposals, G2 and the golden eval checks."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import MatchChangeRefused, MatchProposalInvalid, ReconCannotRun
from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import bank_proposals, gates
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from fin.reconcile import bank, bank_match, run_new
from fin.reconcile.items import raise_from_items

D = Decimal
BANK, USD = 1, 2  # bank source files (INR and USD accounts)
REG, LV = 3, 4

# (file, row, date, remarks, credit): INR account 111, USD account 222
RECEIPTS = [
    (BANK, 2, date(2024, 5, 20), "NEFT INV-1001 CUST-0001", 120000),
    (BANK, 3, date(2024, 5, 25), "NEFT CUST-0002", 80000),
    (BANK, 4, date(2024, 6, 10), "RTGS 55000", 55000),
    (BANK, 5, date(2024, 7, 1), "INV-1005 CUST-0004", 95000),
    (BANK, 6, date(2024, 8, 1), "UNKNOWN PAYER", 250000),
    (USD, 2, date(2024, 6, 20), "CUST-0005 wire", 2000),
]
# (row, customer, invoice no, date, currency, amount, amount in Rs)
INVOICES = [
    (2, "CUST-0001", "INV-1001", date(2024, 5, 1), "INR", 120000, 120000),
    (3, "CUST-0002", "INV-1002", date(2024, 5, 12), "INR", 80000, 80000),
    (4, "CUST-0003", "INV-1004", date(2024, 6, 1), "INR", 55000, 55000),
    (5, "CUST-0004", "INV-1005", date(2024, 6, 15), "INR", 100000, 100000),
    (6, "CUST-0005", "INV-1006", date(2024, 6, 5), "USD", 2000, 160000),
    (7, "CUST-0006", "INV-1007", date(2024, 12, 1), "INR", 300000, 300000),
    (8, "CUST-0006", "INV-1008", date(2025, 3, 20), "INR", 150000, 150000),
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
        for fid, kind in (
            (BANK, "bank"),
            (USD, "bank"),
            (REG, "registers"),
            (LV, "ledger_vouchers"),
        ):
            con.execute(
                "INSERT INTO source_files (file_id, engagement_id, kind, file_name, sha256, "
                "archive_path, period, period_start, period_end, basis, profile, status, run_id, "
                "ingested_at) VALUES (?, 1, ?, ?, ?, 'x', 'FY25', DATE '2024-04-01', "
                "DATE '2025-03-31', 'audited', 'p', 'loaded', 'r0', now())",
                [fid, kind, f"{kind}{fid}.xlsx", f"sha{fid}"],
            )
        for fid, sheet, row, day, remarks, credit in [
            (f, "S", r, d, t, c) for f, r, d, t, c in RECEIPTS
        ]:
            usd = fid == USD
            con.execute(
                'INSERT INTO bank_lines (file_id, sheet, page, "row", bank, account, currency, '
                "txn_date, remarks, reference, debit, credit, balance, run_id) "
                "VALUES (?, ?, NULL, ?, 'axis', ?, ?, ?, ?, NULL, 0, ?, NULL, 'r0')",
                [
                    fid,
                    sheet,
                    row,
                    "222" if usd else "111",
                    "USD" if usd else "INR",
                    day,
                    remarks,
                    credit,
                ],
            )
        for row, cust, number, day, cur, amount, inr in INVOICES:
            con.execute(
                'INSERT INTO register_lines (file_id, sheet, "row", kind, customer, invoice_no, '
                "invoice_date, amount, currency, amount_inr, run_id) "
                "VALUES (?, 'Sales', ?, 'sales', ?, ?, ?, ?, ?, ?, 'r0')",
                [REG, row, cust, number, day, amount, cur, inr],
            )
        con.execute(
            'INSERT INTO ledger_entries (file_id, sheet, "row", ledger, entry_date, particulars, '
            "voucher_type, voucher_no, debit, credit, run_id) VALUES (?, 'L', 5, 'CUST-0005', "
            "DATE '2024-06-21', 'Axis USD', 'Receipt', '9', 0, 163000, 'r0')",
            [LV],
        )
    return root


def run(root: Path) -> Run:
    return Run("t", {}, root)


def go(root: Path, name: str = "bank_vs_invoices") -> dict[str, Any]:
    [result] = run_new(run(root), "acme", name, None)
    return result


def by_kind(result: dict[str, Any]) -> dict[tuple[str, Decimal], dict[str, Any]]:
    return {(x["kind"], x["difference"]): x for x in result["lines"]}


def test_the_passes_reproduce_the_answer_key(root: Path) -> None:
    summary = sorted(
        (
            x["kind"],
            x["match_pass"],
            x["left_amount"],
            x["right_amount"],
            x["difference"],
            x["currency"],
        )
        for x in go(root)["lines"]
    )
    assert summary == sorted(
        [
            ("matched", "reference", D(120000), D(120000), D(0), "INR"),
            ("matched", "party_amount_date", D(80000), D(80000), D(0), "INR"),
            ("matched", "amount_date", D(55000), D(55000), D(0), "INR"),
            ("difference", "reference", D(95000), D(100000), D(-5000), "INR"),
            ("matched", "party_amount_date", D(2000), D(2000), D(0), "USD"),
            ("fx", "party_amount_date", D(163000), D(160000), D(3000), "INR"),
            ("left_only", None, D(250000), None, D(250000), "INR"),
            ("right_only", None, None, D(300000), D(-300000), "INR"),
            ("right_only", None, None, D(150000), D(-150000), "INR"),
        ],
        key=lambda t: tuple(str(v) for v in t),
    )


def test_what_needs_a_query_fx_and_not_yet_due(root: Path) -> None:
    lines = by_kind(go(root))
    assert lines[("left_only", D(250000))]["needs_query"]
    assert lines[("right_only", D(-300000))]["needs_query"]
    not_due = lines[("right_only", D(-150000))]
    assert not not_due["needs_query"]
    assert not_due["note"] == "not yet due"
    assert not lines[("difference", D(-5000))]["needs_query"]  # below the threshold
    fx = lines[("fx", D(3000))]
    assert not fx["needs_query"] and "FX gain" in fx["note"]  # listed, never queried
    # the booked receipt it used is named, so a person can see it is the right voucher
    assert fx["left_source"] and "!" in fx["left_source"]
    text = bank.format_result(go(root))
    assert "invoices None" not in text and "receipts None" not in text
    assert fx["left_source"] in text
    assert [x["level"] for x in lines.values()].count("receipt") == 1


def test_a_foreign_receipt_with_no_booked_value_is_noted_not_guessed(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("DELETE FROM ledger_entries")
    lines = go(root)["lines"]
    assert not [x for x in lines if x["kind"] == "fx"]
    usd = next(x for x in lines if x["currency"] == "USD")
    assert usd["note"].startswith("no booked value")


def test_a_booked_receipt_at_another_receipts_amount_is_not_taken_as_the_rupee_value(
    root: Path,
) -> None:
    # the customer's only Receipt voucher near the bank date books Rs 2,00,000 for USD 2,000 (a
    # rate of 100 against the invoice's 80): it is another receipt, not this one's rupee value
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE ledger_entries SET credit = 200000")
    lines = go(root)["lines"]
    assert not [x for x in lines if x["kind"] == "fx"]
    usd = next(x for x in lines if x["currency"] == "USD")
    assert usd["note"].startswith("no booked value") and "ledger_vouchers4.xlsx L!5" in usd["note"]
    # with the right voucher beside it, the right one is used even when the wrong one is nearer
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            'INSERT INTO ledger_entries (file_id, sheet, "row", ledger, entry_date, particulars, '
            "voucher_type, voucher_no, debit, credit, run_id) VALUES (?, 'L', 6, 'CUST-0005', "
            "DATE '2024-06-25', 'Axis USD', 'Receipt', '10', 0, 158000, 'r0')",
            [LV],
        )
    fx = by_kind(go(root))[("fx", D(-2000))]
    assert fx["left_amount"] == D(158000) and "L!6" in fx["left_source"]


def test_collections_view(root: Path) -> None:
    go(root)
    with connect(company_db_path(root, "acme")) as con:
        got = {
            r[0]: r[1:]
            for r in con.execute(
                "SELECT customer, invoiced_inr, received_inr, open_inr, collection_days, "
                "not_due_inr, overdue_61_90_inr FROM customer_collections"
            ).fetchall()
        }
    assert got["CUST-0001"] == (D(120000), D(120000), D(0), D("19.0"), D(0), D(0))
    assert got["CUST-0006"] == (D(450000), D(0), D(450000), None, D(150000), D(300000))


def test_a_confirmed_proposal_is_applied_and_a_rejected_one_is_not(root: Path) -> None:
    ref = [BANK, "S", 6]
    bad = [{"bank": [[BANK, "S", 99]], "invoices": [[REG, "Sales", 7]], "reason": "x"}]
    run_root = run(root)
    with pytest.raises(MatchProposalInvalid):
        bank_proposals.propose(run_root, "acme", bad)
    ids = bank_proposals.propose(
        run(root), "acme",
        [{"bank": [ref], "invoices": [[REG, "Sales", 7]], "confidence": 0.5, "reason": "payer"}],
    )  # fmt: skip
    assert "payer" in bank_proposals.format_proposal(
        bank_proposals.list_proposals(run(root), "acme")[0]
    )
    assert not [x for x in go(root)["lines"] if x["match_pass"] == "confirmed"]
    bank_proposals.decide(run(root), "acme", ids, "confirmed")
    run_root = run(root)
    with pytest.raises(MatchChangeRefused):
        bank_proposals.decide(run_root, "acme", ids, "rejected")
    confirmed = [x for x in go(root)["lines"] if x["match_pass"] == "confirmed"]
    assert [(x["kind"], x["difference"]) for x in confirmed] == [("difference", D(-50000))]


def test_a_proposal_cannot_repeat_a_line_or_mix_currencies(root: Path) -> None:
    twice = [
        {"bank": [[BANK, "S", 6], [BANK, "S", 6]], "invoices": [[REG, "Sales", 7]], "reason": "x"}
    ]
    run_root = run(root)
    with pytest.raises(MatchProposalInvalid, match="twice"):
        bank_proposals.propose(run_root, "acme", twice)
    mixed = [
        {"bank": [[BANK, "S", 6]], "invoices": [[REG, "Sales", 6]], "reason": "x"}
    ]  # INR vs USD
    run_root = run(root)
    with pytest.raises(MatchProposalInvalid, match="currencies"):
        bank_proposals.propose(run_root, "acme", mixed)


def test_an_invoice_outside_the_engagement_periods_is_not_a_candidate(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE register_lines SET invoice_date = DATE '2019-01-01' WHERE \"row\" = 7")
    keys = [x["key"] for x in go(root)["lines"]]
    assert "INV-1007" not in keys


def test_the_passes_in_isolation() -> None:
    def inv(n: int, party: str | None, day: int, amount: int) -> bank_match.Invoice:
        return bank_match.Invoice(
            (1, "s", n), date(2024, 1, day), "INR", D(amount), D(amount), party, f"I-{n}"
        )

    def rec(n: int, party: str | None, day: int, amount: int, text: str = "") -> bank_match.Receipt:
        return bank_match.Receipt((2, "s", n), date(2024, 1, day), "INR", D(amount), party, text)

    # two same-amount invoices of one customer: the closer date goes first (pass 2)
    groups = bank_match.match(
        [rec(1, "C1", 20, 500)], [inv(1, "C1", 1, 500), inv(2, "C1", 18, 500)], 60
    )
    assert [(g.pass_name, g.invoices[0].key[2]) for g in groups] == [("party_amount_date", 2)]
    # pass 3 never guesses between two candidates
    groups = bank_match.match(
        [rec(1, None, 20, 500)], [inv(1, "C1", 1, 500), inv(2, "C2", 18, 500)], 60
    )
    assert groups == []
    # a date outside the window does not match
    assert bank_match.match([rec(1, "C1", 20, 500)], [inv(1, "C1", 1, 500)], 10) == []
    # one receipt naming two invoices is one group
    groups = bank_match.match(
        [rec(1, None, 20, 800, "paid I-1 and I-2")],
        [inv(1, "C1", 1, 500), inv(2, "C1", 2, 300)],
        60,
    )
    assert [(g.pass_name, len(g.invoices)) for g in groups] == [("reference", 2)]


def test_g2_needs_the_reconciliation_and_its_queries(root: Path) -> None:
    def lines() -> list[tuple[bool, str]]:
        with connect(company_db_path(root, "acme"), write=True) as con:
            g2 = gates.evaluate(gates.context(con, root, "acme"))[1]
        return [(x["ok"], x["text"]) for x in g2["checklist"]]

    assert any(not ok and "bank_vs_invoices has not run" in t for ok, t in lines())
    result = go(root)
    assert any(not ok and "not in a query" in t for ok, t in lines())
    ids = [x["item_id"] for x in result["lines"] if x["needs_query"]]
    raise_from_items(run(root), "acme", ids, "Please explain these lines.")
    assert any(ok and "bank_vs_invoices ran" in t for ok, t in lines())
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute('UPDATE bank_lines SET credit = credit + 1 WHERE "row" = 2 AND file_id = 1')
    assert any(not ok and "changed since" in t for ok, t in lines())


def test_it_cannot_run_without_a_register(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE source_files SET status = 'superseded' WHERE kind = 'registers'")
    run_root = run(root)
    with pytest.raises(ReconCannotRun, match="sales register"):
        run_new(run_root, "acme", "bank_vs_invoices", None)
    run_root = run(root)
    with pytest.raises(ReconCannotRun):
        run_new(run_root, "acme", "bank_vs_tb", None)


def test_bank_vs_tb_compares_the_closing_balance_with_the_books(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO bank_statements VALUES (1, 'axis', '111', 'INR', DATE '2025-03-01', "
            "DATE '2025-03-31', 0, 'header', 700000, 'header', 0, 700000, 1, true, 'excel', "
            "NULL, 'r0')"
        )
        con.execute(
            "INSERT INTO source_files (file_id, engagement_id, kind, file_name, sha256, "
            "archive_path, period, period_start, period_end, basis, profile, status, run_id, "
            "ingested_at) VALUES (9, 1, 'trial_balance', 'tb.xlsx', 'sha9', 'x', 'FY25', "
            "DATE '2024-04-01', DATE '2025-03-31', 'audited', 'p', 'loaded', 'r0', now())"
        )
        con.execute(
            "INSERT INTO trial_balance VALUES (9, 'TB', 5, 'FY25', 'ledger', 'Bank', NULL, "
            "'Axis Bank', 450000, 0, 'r0')"
        )
        con.execute(
            "INSERT INTO ledger_accounts (ledger, status, account, statement, side, run_id, "
            "changed_at) VALUES ('Axis Bank', 'confirmed', 'cash', 'balance_sheet', 1, 'r0', now())"
        )
    [line] = go(root, "bank_vs_tb")["lines"]
    assert (line["kind"], line["left_amount"], line["right_amount"], line["difference"]) == (
        "difference",
        D(700000),
        D(450000),
        D(250000),
    )
    assert line["needs_query"]


def golden(root: Path, rows: list[tuple[str, ...]]) -> None:
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for kind, ledger, expected in rows:
            writer.writerow((kind, "FY25", "", "", ledger, expected, "rupees", "s", "yes"))


def test_golden_checks_score_the_match_outputs(root: Path) -> None:
    go(root)
    golden(
        root,
        [
            ("bank_unmatched", "111", "250000"),
            ("bank_unmatched", "222", "0"),
            ("invoice_unmatched", "CUST-0006", "450000"),
            ("fx_line", "CUST-0005", "3000"),
            ("fx_line", "CUST-0001", "0"),
            ("customer_received", "CUST-0001", "120000"),
            ("customer_received", "CUST-0006", "0"),
            ("invoice_unmatched", "CUST-0001", "999"),  # planted wrong
            ("bank_vs_tb", "", "1"),  # no bank_vs_tb run: missing
        ],
    )
    result = run_eval(run(root), "acme")
    outcomes = [c["outcome"] for c in result["checks"]]
    assert outcomes == ["pass"] * 7 + ["fail", "missing"]
    assert "not_reconciled" in result["checks"][-1]["codes"]
    assert result["counts"]["bank_matches"]["lines"]["matched, reference"] == 1


def test_init_lists_what_a_person_can_check(root: Path) -> None:
    go(root)
    init_golden(run(root), "acme")
    with golden_path(root, "acme").open(encoding="utf-8", newline="") as handle:
        got = {(r["kind"], r["ledger"]) for r in csv.DictReader(handle)}
    assert {
        ("bank_unmatched", "111"),
        ("invoice_unmatched", "CUST-0006"),
        ("fx_line", "CUST-0005"),
        ("customer_received", "CUST-0001"),
    } <= got
