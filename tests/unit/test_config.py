from decimal import Decimal
from pathlib import Path

import pytest

from fin.audit.errors import FinError
from fin.config import DEFAULT_CONFIG_TOML, Config, load_config, write_default_config


def test_default_config_round_trips(tmp_path: Path) -> None:
    assert write_default_config(tmp_path) is True
    assert load_config(tmp_path) == Config(
        display_unit="crores",
        default_scope="standalone",
        fiscal_year_start_month=4,
        tolerances={
            "balance_sheet": Decimal("100000.00"),
            "subtotal": Decimal("100000.00"),
            "cash_flow": Decimal("100000.00"),
            "cross_document": Decimal("100000.00"),
        },
    )


def test_values_come_from_the_file(tmp_path: Path) -> None:
    edited = (
        DEFAULT_CONFIG_TOML.replace('unit = "crores"', 'unit = "lakhs"')
        .replace("subtotal = 100000.00", "subtotal = 0.5")
        .replace("cash_flow = 100000.00", "cash_flow = 7")
    )
    (tmp_path / "config.toml").write_text(edited, encoding="utf-8")
    config = load_config(tmp_path)
    assert config.display_unit == "lakhs"
    assert config.tolerances["subtotal"] == Decimal("0.5")
    assert config.tolerances["cash_flow"] == Decimal("7")


def test_write_default_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("# mine\n", encoding="utf-8")
    assert write_default_config(tmp_path) is False
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "# mine\n"


def test_missing_config_is_e001(tmp_path: Path) -> None:
    with pytest.raises(FinError) as caught:
        load_config(tmp_path)
    assert str(caught.value) == (
        f"E001: {tmp_path / 'config.toml'} is missing; run `uv run fin init` to create it"
    )


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        (
            'unit = "crores"',
            'unit = "millions"',
            "display.unit = 'millions'; expected one of rupees, thousands, lakhs, crores",
        ),
        (
            "balance_sheet = 100000.00",
            "balance_sheet = -1",
            "tolerances.balance_sheet = -1; expected a number of rupees >= 0",
        ),
        (
            "balance_sheet = 100000.00",
            'balance_sheet = "1000"',
            "tolerances.balance_sheet = '1000'; expected a number of rupees >= 0",
        ),
        ("subtotal = 100000.00\n", "", "[tolerances] is missing subtotal"),
        (
            "subtotal = 100000.00",
            "subtotal = 1\nsubtotals = 1",
            "[tolerances] has unknown key(s) subtotals",
        ),
        (
            'scope = "standalone"',
            'scope = "both"',
            "defaults.scope = 'both'; expected one of standalone, consolidated",
        ),
        (
            "fiscal_year_start_month = 4",
            "fiscal_year_start_month = 13",
            "defaults.fiscal_year_start_month = 13; expected an integer 1-12",
        ),
        (
            "config_version = 1",
            "config_version = 2",
            "config_version = 2; this fin reads version 1",
        ),
    ],
)
def test_invalid_config_is_e002(tmp_path: Path, old: str, new: str, problem: str) -> None:
    assert old in DEFAULT_CONFIG_TOML
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML.replace(old, new), encoding="utf-8")
    with pytest.raises(FinError) as caught:
        load_config(tmp_path)
    assert str(caught.value) == f"E002: {path} is invalid: {problem}"


def test_unparseable_config_is_e002(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("unit = \n", encoding="utf-8")
    with pytest.raises(FinError) as caught:
        load_config(tmp_path)
    assert caught.value.code == "E002"
    assert "is invalid: not valid TOML" in caught.value.message
