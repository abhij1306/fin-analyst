"""FDD F1 on a synthetic register: contract terms (customer and vendor readings), the coverage view,
reproducible samples, walkthroughs and their exceptions as a reconciliation for queries."""

import csv
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import (
    ContractNotOnRegister,
    ContractReadingInvalid,
    ReconItemNotQueryable,
    SampleCannotDraw,
    SampleUnknown,
    WalkthroughInvalid,
)
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence.mask import Masker
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.reconcile import contract_coverage, contracts, items
from fin.sampling import draw, walkthrough
from tests.register_fixtures import write_registers

# Register of write_registers, FY25: sales INV-1001 120000 and INV-1003 160000 (the first
# customer), INV-1002 80000 (the second); purchases PINV-71 90000 and PINV-72 40000. The
# engagement threshold is 100000.


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
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
    registers = write_registers(root / "reg.xlsx")
    ingest_source(run(root), load_config(root), "acme", registers, "registers", None)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def rows(root: Path, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql, list(params)).fetchall()


def party(root: Path, invoice: str, column: str = "customer") -> str:
    return str(
        rows(root, f"SELECT {column} FROM register_lines WHERE invoice_no = ?", invoice)[0][0]
    )


def reading(path: Path, *table: dict[str, str]) -> Path:
    names = list(dict.fromkeys(name for row in table for name in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(table)
    return path


def document(root: Path) -> Path:
    path = root / "contract.pdf"
    path.write_bytes(b"synthetic contract")
    return path


def load_contracts(root: Path, *table: dict[str, str]) -> list[dict[str, Any]]:
    return contracts.load(run(root), "acme", document(root), reading(root / "r.csv", *table))


def customer_contract(root: Path, **over: str) -> dict[str, str]:
    return {
        "counterparty": party(root, "INV-1001"), "start": "2024-04-01", "end": "2025-12-31",
        "page": "3", **over,
    }  # fmt: skip


# --- contract terms ---------------------------------------------------------------------------


def test_a_customer_and_a_vendor_contract_load_with_their_terms(root: Path) -> None:
    vendor = party(root, "PINV-71", "vendor")
    stored = load_contracts(
        root,
        customer_contract(
            root, lock_in_months="12", notice_days="90", escalation_pct="5%", auto_renew="Yes",
            change_of_control="no", sla_terms="99.5% uptime", value="12,00,000.00",
        ),
        {
            "party_kind": "vendor", "counterparty": vendor, "start": "2024-04-01",
            "end": "2026-03-31", "page": "7", "invoice_no": "PINV-71", "exclusivity": "yes",
        },
    )  # fmt: skip

    first, second = stored
    assert (first["party_kind"], first["lock_in_months"], first["notice_days"]) == (
        "customer", 12, 90
    )  # fmt: skip
    assert first["escalation_pct"] == Decimal("5.00")
    assert (first["auto_renew"], first["change_of_control"]) == (True, False)
    assert first["termination_convenience"] is None  # not read is not "no clause"
    assert first["value"] == Decimal("1200000.00")
    assert (second["party_kind"], second["exclusivity"], second["page"]) == ("vendor", True, 7)
    assert "lock in 12" in contracts.format_contract(first)
    assert "termination convenience" not in contracts.format_contract(first)


def test_the_old_customer_column_still_loads_and_names_are_masked_in_terms(root: Path) -> None:
    token = party(root, "INV-1001")
    real = str(rows(root, "SELECT real_value FROM mask_values WHERE token = ?", token)[0][0])
    old = {"customer": real, "start": "2024-04-01", "end": "2025-03-31", "page": "1",
           "penalty_terms": f"{real} pays 2% a month late"}  # fmt: skip
    [stored] = load_contracts(root, old)

    assert stored["counterparty"] == token
    assert stored["penalty_terms"] == f"{token} pays 2% a month late"


def test_a_vendor_that_is_not_on_the_purchase_register_is_refused(root: Path) -> None:
    wrong = {**customer_contract(root), "party_kind": "vendor"}  # a customer token as a vendor
    with pytest.raises(ContractNotOnRegister, match="line 2"):
        load_contracts(root, wrong)
    assert rows(root, "SELECT count(*) FROM contracts")[0][0] == 0


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"auto_renew": "maybe"}, "auto_renew 'maybe' is not yes or no"),
        ({"lock_in_months": "1.5"}, "lock_in_months '1.5' is not a whole number"),
        ({"escalation_pct": "lots"}, "escalation_pct 'lots' is not a number"),
        ({"party_kind": "partner"}, "party_kind 'partner' is not one of customer, vendor"),
    ],
)
def test_a_term_that_cannot_be_read_names_its_line(root: Path, over: dict, message: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ContractReadingInvalid, match=message):
        load_contracts(root, customer_contract(root, **over))
    assert rows(root, "SELECT count(*) FROM contracts")[0][0] == 0


# --- coverage ---------------------------------------------------------------------------------


def coverage(root: Path, top_n: int = 10) -> dict[tuple[str, int], dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        found = contract_coverage.coverage(con, 1, top_n)
    return {(r["party_kind"], r["rank"]): r for r in found}


def test_coverage_ranks_parties_and_reads_the_contract_terms(root: Path) -> None:
    load_contracts(
        root,
        customer_contract(root, lock_in_months="12", auto_renew="yes", change_of_control="yes"),
    )

    found = coverage(root)

    assert [(k, r["amount_inr"]) for k, r in found.items() if k[0] == "customer"] == [
        (("customer", 1), Decimal("280000.00")),
        (("customer", 2), Decimal("80000.00")),
    ]
    top = found[("customer", 1)]
    assert top["contract_on_file"] and top["period_end"] == date(2025, 3, 31)
    assert top["expires_within_12m"] and not top["expired"]  # ends 2025-12-31
    assert (top["auto_renew"], top["change_of_control"]) == (True, True)
    assert top["lock_in_left_months"] == 0  # the lock-in ends 2025-04-01: one day, no whole month
    assert not found[("customer", 2)]["contract_on_file"]
    assert not any(r["contract_on_file"] for k, r in found.items() if k[0] == "vendor")
    assert [r["amount_inr"] for k, r in found.items() if k[0] == "vendor"] == [
        Decimal("90000.00"),
        Decimal("40000.00"),
    ]


@pytest.mark.parametrize(("lock_in", "left"), [("17", 5), ("18", 6)])
def test_lock_in_months_left_counts_whole_months(root: Path, lock_in: str, left: int) -> None:
    # Starts 2024-04-01: 17 months end 2025-09-01 (5 whole months after 2025-03-31, though six
    # month boundaries are crossed), 18 months end 2025-10-01 (6 whole months).
    load_contracts(root, customer_contract(root, lock_in_months=lock_in))

    assert coverage(root)[("customer", 1)]["lock_in_left_months"] == left


def test_coverage_counts_only_the_top_n_and_leaves_out_pass_through_customers(root: Path) -> None:
    assert sorted(coverage(root, 1)) == [("customer", 1), ("vendor", 1)]

    top = party(root, "INV-1001")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO passthrough_customers VALUES (1, ?, 'user', 'r0', ?)", [top, utcnow()]
        )
    found = coverage(root)

    assert found[("customer", 1)]["counterparty"] == party(root, "INV-1002")
    assert ("customer", 2) not in found


def test_an_ended_contract_is_expired_and_the_contract_ending_last_is_the_one_shown(
    root: Path,
) -> None:
    load_contracts(
        root,
        customer_contract(root, end="2024-12-31"),
        customer_contract(root, end="2025-02-28", page="9"),
    )

    top = coverage(root)[("customer", 1)]

    assert top["contracts_on_file"] == 2
    assert top["end_date"] == date(2025, 2, 28) and top["expired"]
    assert "no contract on file" in contract_coverage.format_row(coverage(root)[("customer", 2)])
    assert "before the period end" in contract_coverage.format_row(top)
    assert top["lock_in_left_months"] is None  # not read


# --- samples ----------------------------------------------------------------------------------


def drawn(root: Path, kind: str = "sales", n: int = 25, seed: int | None = 5) -> dict[str, Any]:
    return draw.draw(run(root), "acme", kind, "FY25", n, seed)


def test_a_sample_takes_the_lines_at_or_above_the_threshold_largest_first_then_random(
    root: Path,
) -> None:
    sample = drawn(root, n=3)

    assert [(x["invoice_no"], x["selection"]) for x in sample["lines"]] == [
        ("INV-1003", "threshold"),
        ("INV-1001", "threshold"),
        ("INV-1002", "random"),
    ]
    assert (sample["population"], sample["above_threshold"], sample["seed"]) == (3, 2, 5)


def test_a_sample_smaller_than_the_threshold_lines_says_how_many_were_left_out(
    root: Path,
) -> None:
    sample = drawn(root, n=1)

    assert [x["invoice_no"] for x in sample["lines"]] == ["INV-1003"]
    assert "1 of them not sampled" in draw.format_sample(sample)


def test_the_same_seed_gives_the_same_random_lines(root: Path) -> None:
    first = [x["invoice_no"] for x in drawn(root, kind="purchase", n=1, seed=11)["lines"]]
    again = [x["invoice_no"] for x in drawn(root, kind="purchase", n=1, seed=11)["lines"]]

    assert first == again
    assert rows(root, "SELECT count(*), count(DISTINCT seed) FROM samples") == [(2, 1)]
    assert drawn(root, n=3, seed=None)["seed"] >= 0  # a seed is made and stored


def test_a_sample_that_cannot_be_drawn_is_refused(root: Path) -> None:
    with pytest.raises(SampleCannotDraw, match="not a period"):
        draw.draw(run(root), "acme", "sales", "FY30", 5, 1)
    with pytest.raises(SampleCannotDraw, match="at least 1"):
        drawn(root, n=0)
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE source_files SET status = 'superseded'")
    with pytest.raises(SampleCannotDraw, match="no loaded sales register line"):
        drawn(root)
    assert rows(root, "SELECT count(*) FROM samples")[0][0] == 0


# --- walkthroughs -----------------------------------------------------------------------------


def results(path: Path, *table: dict[str, Any]) -> Path:
    path.write_text(json.dumps(list(table)), encoding="utf-8")
    return path


def result(line: int, check: str, outcome: str, **over: Any) -> dict[str, Any]:
    return {"line_no": line, "check": check, "result": outcome, "evidence": "seen", **over}


def test_a_walkthrough_is_summarised_and_a_large_exception_becomes_a_line_for_a_query(
    root: Path,
) -> None:
    drawn(root, n=3)  # lines: 1 INV-1003 (160000), 2 INV-1001 (120000), 3 INV-1002 (80000)
    real = str(
        rows(root, "SELECT real_value FROM mask_values WHERE token = ?", party(root, "INV-1002"))[
            0
        ][0]
    )
    file = results(
        root / "w.json",
        result(1, "in_register", "pass"),
        result(1, "contract_covers", "exception", evidence="rate 900 in the contract, 1000 billed",
               file="contract.pdf", page=3),
        result(2, "bank_matched", "not_available"),
        result(3, "ledger_posting", "exception", evidence=f"{real} posting missing"),
    )  # fmt: skip

    out = walkthrough.record(run(root), "acme", 1, file)

    summary = out["summary"]
    assert (summary["lines"], summary["lines_walked"], summary["checks"]) == (3, 3, 4)
    assert (summary["checks_passed"], summary["exceptions"], summary["not_available"]) == (1, 2, 1)
    assert (summary["exception_lines"], summary["exception_amount"]) == (2, Decimal("240000.00"))
    lines = {x["key"]: x for x in out["recon"]["lines"]}
    big = lines["sample 1 line 1: contract_covers"]
    small = lines["sample 1 line 3: ledger_posting"]
    assert big["needs_query"] and big["difference"] == Decimal("160000.00")
    assert "contract_covers: rate 900" in big["note"] and "contract.pdf p.3" in big["right_source"]
    assert not small["needs_query"]  # below the threshold: listed
    assert party(root, "INV-1002") in small["note"] and real not in small["note"]  # masked

    query, _ = items.raise_from_items(run(root), "acme", [big["item_id"]], "Contract rate?")
    assert query["amount"] == Decimal("160000.00")
    with pytest.raises(ReconItemNotQueryable):
        items.raise_from_items(run(root), "acme", [small["item_id"]], "x")


def test_recording_a_check_again_replaces_it_and_clears_its_exception(root: Path) -> None:
    drawn(root, n=3)
    walkthrough.record(
        run(root), "acme", 1, results(root / "a.json", result(1, "contract_covers", "exception"))
    )

    out = walkthrough.record(
        run(root), "acme", 1, results(root / "b.json", result(1, "contract_covers", "pass"))
    )

    assert out["summary"]["checks"] == 1 and out["summary"]["exceptions"] == 0
    assert out["recon"]["lines"] == []
    assert rows(root, "SELECT count(*) FROM recon_runs WHERE name = 'walkthrough'")[0][0] == 2


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (result(9, "in_register", "pass"), "line_no 9 is not a line of the sample"),
        (result(1, "vibes", "pass"), "check 'vibes' is not one of"),
        (result(1, "in_register", "maybe"), "result 'maybe' is not one of"),
        (result(1, "in_register", "pass", evidence=" "), "evidence is empty"),
        (result(1, "in_register", "pass", page=0), "page starts at 1"),
    ],
)
def test_a_walkthrough_that_cannot_be_used_records_nothing(
    root: Path, bad: dict[str, Any], message: str
) -> None:
    drawn(root, n=3)
    with pytest.raises(WalkthroughInvalid, match=message):
        walkthrough.record(run(root), "acme", 1, results(root / "w.json", bad))
    repeated = results(
        root / "r.json", result(1, "in_register", "pass"), result(1, "in_register", "pass")
    )
    with pytest.raises(WalkthroughInvalid, match="repeats line 1 in_register"):
        walkthrough.record(run(root), "acme", 1, repeated)
    assert rows(root, "SELECT count(*) FROM walkthroughs")[0][0] == 0


def test_an_unknown_sample_is_refused(root: Path) -> None:
    with pytest.raises(SampleUnknown):
        walkthrough.record(run(root), "acme", 4, results(root / "w.json", result(1, "x", "pass")))


def test_golden_checks_score_a_contract_value_and_a_sample_exception_amount(root: Path) -> None:
    [contract] = load_contracts(root, customer_contract(root, value="12,00,000"))
    drawn(root, n=3)
    walkthrough.record(
        run(root), "acme", 1, results(root / "w.json", result(1, "contract_covers", "exception"))
    )
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True)
    lines = [
        ("contract_value", "", "", "", str(contract["contract_id"]), "12,00,000", "rupees",
         "contract.pdf p.3", "yes"),
        ("sample_exception_amount", "FY25", "", "", "1", "1,60,000", "rupees",
         "sample #1", "yes"),
    ]  # fmt: skip

    def score() -> list[str]:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(lines)
        return [c["outcome"] for c in run_eval(run(root), "acme")["checks"]]

    assert score()[:2] == ["pass", "pass"]
    lines[1] = (*lines[1][:5], "1,50,000", *lines[1][6:])
    assert score()[:2] == ["pass", "fail"]


def test_a_different_failure_on_the_same_line_does_not_inherit_the_earlier_query(
    root: Path,
) -> None:
    drawn(root, n=3)
    first = walkthrough.record(
        run(root), "acme", 1, results(root / "a.json", result(1, "contract_covers", "exception"))
    )
    [line] = first["recon"]["lines"]
    items.raise_from_items(run(root), "acme", [line["item_id"]], "Contract rate?")

    second = walkthrough.record(
        run(root),
        "acme",
        1,
        results(
            root / "b.json",
            result(1, "contract_covers", "pass"),
            result(1, "bank_matched", "exception"),
        ),
    )

    [moved] = second["recon"]["lines"]
    assert moved["needs_query"] and moved["query_id"] is None  # a new failure needs its own query


def test_a_name_learned_later_is_masked_in_the_reconciliation_note_too(root: Path) -> None:
    drawn(root, n=3)
    walkthrough.record(
        run(root),
        "acme",
        1,
        results(
            root / "w.json", result(1, "ledger_posting", "exception", evidence="Zed Corp short")
        ),
    )
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO mask_values VALUES "
            "('CUST-0099', 'customer', 'Zed Corp', 'zed corp', 1, 'r0')"
        )
        masker = Masker(con, "r1")
        masker.added = 1
        masker.remask()

    [(note,)] = rows(root, "SELECT note FROM recon_items WHERE level = 'invoice'")
    assert "CUST-0099 short" in note and "Zed Corp" not in note
