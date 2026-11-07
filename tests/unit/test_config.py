from decimal import Decimal
from pathlib import Path

import pytest

from fin.audit.errors import FinError
from fin.config import (
    DEFAULT_CONFIG_TOML,
    Config,
    load_config,
    set_docling,
    write_default_config,
)

# config.toml as written before the Docling add-on: no [pdf] table.
OLDER_CONFIG = DEFAULT_CONFIG_TOML[: DEFAULT_CONFIG_TOML.index("\n\n[pdf]")] + "\n"


def test_docling_is_off_by_default_and_without_a_pdf_table(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    assert load_config(tmp_path).docling is False
    (tmp_path / "config.toml").write_text(OLDER_CONFIG, encoding="utf-8")
    assert load_config(tmp_path).docling is False


def test_set_docling_changes_only_that_line(tmp_path: Path) -> None:
    write_default_config(tmp_path)

    set_docling(tmp_path, True)

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert text == DEFAULT_CONFIG_TOML.replace("docling = false", "docling = true")
    assert load_config(tmp_path).docling is True


def test_set_docling_keeps_a_trailing_comment(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    path = tmp_path / "config.toml"
    path.write_text(
        DEFAULT_CONFIG_TOML.replace("docling = false", "docling = false  # off"), encoding="utf-8"
    )

    set_docling(tmp_path, True)

    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TOML.replace(
        "docling = false", "docling = true  # off"
    )
    assert load_config(tmp_path).docling is True


def test_set_docling_adds_the_pdf_table_to_an_older_config(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(OLDER_CONFIG, encoding="utf-8")

    set_docling(tmp_path, True)

    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == OLDER_CONFIG + (
        "\n[pdf]\n# Docling, the optional PDF add-on (`./install.sh --docling` / `--no-docling`).\n"
        "docling = true\n"
    )
    assert load_config(tmp_path).docling is True


def test_docling_must_be_true_or_false(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        DEFAULT_CONFIG_TOML.replace("docling = false", 'docling = "yes"'), encoding="utf-8"
    )
    with pytest.raises(FinError) as caught:
        load_config(tmp_path)
    assert caught.value.code == "E002"
    assert "pdf.docling = 'yes'; expected true or false" in caught.value.message


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
