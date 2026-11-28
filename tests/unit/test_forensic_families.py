"""SPEC-18 PR 1 on synthetic books: the tests the mock does not reach (a Benford group that does
not conform, a gap in a long series, too few entries, a test that cannot run), and the settings
writer (`fin forensic set`): precedence, "default", E990, every other line kept."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from fin.actor import USER
from fin.audit.errors import ForensicSettingInvalid
from fin.audit.logger import Run
from fin.config import write_default_config
from fin.forensic import registry, settings
from fin.forensic.books import Books, Context, Line
from fin.forensic.families.journal_vouchers import BENFORD, je_benford, je_gap
from fin.forensic.run import _outcome
from fin.paths import config_path

START, END = date(2024, 4, 1), date(2025, 3, 31)


def line(n: int, amount: Decimal, ledger: str = "Rent", number: str | None = None) -> Line:
    day = START + timedelta(days=n % 300)
    return Line(1, "lv.xlsx", "Sheet1", n + 2, ledger, day, "Journal", number, amount, Decimal(0))


def books(lines: list[Line], loaded: set[str] | None = None) -> Books:
    return Books(
        periods=["FY25"],
        files={"FY25": (1, "lv.xlsx", START, END)},
        lines={"FY25": lines},
        groups={"FY25": {"Rent": "Indirect Expenses"}},
        accounts={},
        data_hash="h",
        loaded={"FY25": loaded or {"ledger_vouchers", "trial_balance"}},
    )


def context(lines: list[Line], **changed: object) -> Context:
    values = {**settings.values({}, "acme"), **changed}
    return Context(books(lines), "FY25", values, Decimal(100000))


def benford_lines(count: int) -> list[Line]:
    """`count` amounts whose first digits follow Benford's law."""
    out: list[Line] = []
    for digit, share in BENFORD.items():
        out += [line(len(out), Decimal(digit * 100 + 7)) for _ in range(round(count * share))]
    return out


def test_benford_flags_a_group_that_does_not_conform() -> None:
    nines = [line(n, Decimal(950 + n % 40)) for n in range(1000)]
    outcome = je_benford(context(nines))

    assert outcome.outcome == "ran" and outcome.population == 1000
    [flag] = outcome.flags
    assert "Tally group Indirect Expenses: first digit 9 on 100.0% of 1000 lines" in flag.reason
    assert flag.amount == sum(x.amount for x in nines)


def test_benford_passes_a_conforming_group_and_counts_too_few() -> None:
    assert je_benford(context(benford_lines(1000))).flags == []
    few = je_benford(context(benford_lines(400)))
    assert (few.outcome, few.minimum) == ("too_few", 1000)


def test_a_gap_in_a_long_series_is_flagged_once() -> None:
    lines = [line(n, Decimal(1000), number=f"JV/{n:03d}") for n in range(1, 31) if n != 17]
    outcome = je_gap(context(lines))

    assert [f.voucher_no for f in outcome.flags] == ["JV/017"]
    assert outcome.flags[0].amount is None
    assert je_gap(context(lines[:10])).outcome == "too_few"


def test_a_test_cannot_run_without_its_input_or_its_files() -> None:
    ctx = context([line(1, Decimal(495000))])
    limit = _outcome(registry.tests()["je_under_limit"], ctx, ctx.books)
    assert limit.outcome == "cannot_run" and "approval_limits" in (limit.needs or "")

    only = books([line(1, Decimal(10))], {"ledger_vouchers"})
    benford = _outcome(
        registry.tests()["je_benford"], Context(only, "FY25", ctx.settings, Decimal(1)), only
    )
    assert benford.outcome == "cannot_run" and "trial balance" in (benford.needs or "")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_default_config(tmp_path)
    with config_path(tmp_path).open("a", encoding="utf-8") as out:
        out.write("\n# kept\n[forensic]\nperiod_end_days = 7\n")
    return tmp_path


def by_key(root: Path) -> dict[str, dict]:  # type: ignore[type-arg]
    return {row["key"]: row for row in settings.rows(root, "acme")}


def test_the_company_value_wins_and_default_removes_it(root: Path) -> None:
    before = config_path(root).read_text(encoding="utf-8")
    assert by_key(root)["period_end_days"]["from"] == "config"

    settings.set_value(Run("t", {}, root), root, "acme", "period_end_days", "3", USER)
    settings.set_value(Run("t", {}, root), root, "acme", "holidays", "08-15, 2025-01-26", USER)
    rows = by_key(root)
    assert (rows["period_end_days"]["value"], rows["period_end_days"]["from"]) == ("3", "company")
    assert rows["holidays"]["value"] == "08-15, 2025-01-26"
    assert settings.resolve(root, "other")["period_end_days"] == 7

    settings.set_value(Run("t", {}, root), root, "acme", "period_end_days", "default", USER)
    after = config_path(root).read_text(encoding="utf-8")
    assert after.startswith(before)  # every line before the company's table is kept
    assert by_key(root)["period_end_days"]["from"] == "config"


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("no_such", "1"),
        ("period_end_days", "two"),
        ("limit_band", "1.5"),
        ("weekend_days", "Funday"),
    ],
)
def test_a_bad_key_or_value_is_refused_and_nothing_changes(root: Path, key: str, text: str) -> None:
    before = config_path(root).read_text(encoding="utf-8")
    with pytest.raises(ForensicSettingInvalid) as raised:
        settings.set_value(Run("t", {}, root), root, "acme", key, text, USER)

    assert raised.value.code == "E990"
    assert config_path(root).read_text(encoding="utf-8") == before


def test_an_invalid_forensic_table_is_refused(root: Path) -> None:
    with config_path(root).open("a", encoding="utf-8") as out:
        out.write("round_unit = -5\n")
    with pytest.raises(ForensicSettingInvalid):
        settings.resolve(root, "acme")
