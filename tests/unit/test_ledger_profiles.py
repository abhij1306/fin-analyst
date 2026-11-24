"""Column profiles (fin.extract.profiles), the tabular reader (fin.extract.ledger) and masking
(fin.diligence.mask)."""

from pathlib import Path

import pytest

from fin.audit.errors import LayoutUnknown, SourcePeriodInvalid
from fin.db.conn import connect
from fin.diligence.mask import Masker
from fin.extract import profiles
from fin.extract.ledger import read_table
from tests.tally_fixtures import write_trial_balance

MASK_VALUES = """
CREATE TABLE mask_values (token VARCHAR PRIMARY KEY, kind VARCHAR, real_value VARCHAR,
    match_key VARCHAR, first_file_id INTEGER, run_id VARCHAR, UNIQUE (kind, match_key))
"""


def test_the_shipped_profiles_load() -> None:
    assert [p.name for p in profiles.profiles_for("trial_balance")] == ["tally_trial_balance"]
    lv = profiles.load_profile("tally_ledger_vouchers")
    assert (lv.kind, lv.block, lv.block_prefix, lv.mask_fields) == (
        "ledger_vouchers", "date", "Ledger:", ("particulars",)
    )  # fmt: skip


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ('kind = "bank"', "kind 'bank' is not one of"),
        ('kind = "trial_balance"\n[columns]\nParticulars = "name"', "no header for credit, debit"),
        (
            'kind = "trial_balance"\ndate_format = "%d"\nperiod = "(?P<start>x)"\n'
            '[columns]\nA = "name"\nB = "debit"\nC = "credit"',
            "named groups start and end",
        ),
    ],
)
def test_an_invalid_profile_is_e807(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, why: str
) -> None:
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "mine.toml").write_text(text, encoding="utf-8")

    with pytest.raises(LayoutUnknown, match="is invalid") as error:
        profiles.load_profile("mine")

    assert why in str(error.value)


def test_the_reader_finds_a_two_row_header_and_the_period(tmp_path: Path) -> None:
    table = read_table(write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")

    assert (table.start.isoformat(), table.end.isoformat()) == ("2024-04-01", "2025-03-31")
    first = table.rows[0]
    assert (first.sheet, first.row, first.cells["name"], first.bold, first.indent) == (
        "Trial Balance", 6, "Capital Account", True, 0.0
    )  # fmt: skip
    assert table.rows[-1].cells["name"] == "Grand Total"


def test_no_period_line_is_e808(tmp_path: Path) -> None:
    path = write_trial_balance(tmp_path / "tb.xlsx", period="For the year")
    with pytest.raises(SourcePeriodInvalid, match="no header line above the column header"):
        read_table(path, "trial_balance")


def test_tokens_are_stable_and_known_names_are_replaced_in_text() -> None:
    with connect(Path(":memory:"), write=True) as con:
        con.execute(MASK_VALUES)
        masker = Masker(con, "r1", 1)
        assert masker.token("customer", "Orchid  Systems") == "CUST-0001"
        assert masker.token("customer", "orchid systems") == "CUST-0001"
        assert masker.token("vendor", "Banyan Traders") == "VEND-0001"
        assert masker.text("Paid to BANYAN  traders for Orchid Systems") == (
            "Paid to VEND-0001 for CUST-0001"
        )
        again = Masker(con, "r2", 2)
        assert again.known("Banyan Traders") == "VEND-0001"
        assert again.token("customer", "Lotus Infotech") == "CUST-0002"
        assert con.execute("SELECT count(*) FROM mask_values").fetchone() == (3,)
