"""The databook layout and the display unit (SPEC-06) on a synthetic company: the layout matches
the checked-in table, one setting changes the unit in `fin statements`, the Statements screen, the
Excel export and the report alike, and no Python or template code divides a stored value."""

import re
from pathlib import Path

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from fin.cli import app
from fin.config import DEFAULT_CONFIG_TOML, load_config
from fin.databook import databook, format_databook
from fin.display import display_settings
from fin.report.data import collect
from fin.report.render import build
from fin.statements import statement_rows
from fin.workbook import export_workbook
from tests.statements_fixtures import make_statements_root

EXPECTED = Path(__file__).parents[1] / "fixtures" / "databook" / "acme.txt"
SRC = Path(__file__).parents[2] / "src" / "fin"
LAKHS = (
    DEFAULT_CONFIG_TOML.replace('unit = "millions"', 'unit = "lakhs"')
    .replace("decimals = 1", "decimals = 2")
    .replace('unit_label = "INR Mn"', 'unit_label = "INR lakh"')
)

# Division by a unit's size (Python) and any division in a template expression. Loading a
# document converts its own unit to rupees (extract/, ingest.py): that is storing, not showing.
PY_DIVISION = re.compile(
    r"/\s*\(?\s*[\w.\[\]\"']*(?:per_unit|PER_UNIT|DISPLAY_UNITS|rupees_per)"
    r"|/\s*\(?\s*(?:Decimal\(\s*[\"']?)?"
    r"(?:1_?000|10_?000|100_?000|1_?000_?000|10_?000_?000|1e[3-9])\b"
)
TEMPLATE_EXPRESSION = re.compile(r"\{[{%].*?[}%]\}", re.DOTALL)
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
LOADING = ("extract", "ingest.py")


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_statements_root(tmp_path_factory.mktemp("databook"))


def test_the_databook_layout_matches_the_expected_table(root: Path) -> None:
    display = display_settings(root, "acme")
    rows = statement_rows(root, "acme", display=display)

    text = format_databook("acme", databook(rows, display, ("of_revenue", "yoy")))

    assert text + "\n" == EXPECTED.read_text(encoding="utf-8")


def test_one_setting_changes_the_unit_in_all_four_outputs(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Total assets FY25 is 16,40,000 rupees: 16.40 in lakhs to two decimals, everywhere."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fin.ui.server import create_app

    (root / "config.toml").write_text(LAKHS, encoding="utf-8")
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    try:
        shown = CliRunner().invoke(app, ["statements", "acme", "--layout", "databook"])
        assert shown.exit_code == 0, shown.output
        assert "acme balance sheet, standalone (INR lakh)" in shown.output
        [line] = [t for t in shown.output.splitlines() if "Total assets" in t]
        assert line.split()[-2:] == ["12.00", "16.40"]

        client = TestClient(create_app(root, "t", 8765), base_url="http://127.0.0.1:8765")
        client.get("/?token=t")
        page = client.get("/partials/statements?company=acme&statement=balance_sheet").text
        row = page.split("Total assets", 1)[1].split("</tr>", 1)[0]
        assert re.findall(r'hx-target="#source"[^>]*>([^<]+)</a>', row) == ["12.00", "16.40"]

        sheet = load_workbook(export_workbook(root, "acme"))["Balance sheet standalone"]
        assert sheet["A1"].value == "acme: Balance sheet, standalone (INR lakh)"
        [cells] = [r for r in sheet.iter_rows(min_row=3) if r[0].value == "Total assets"]
        assert [c.value for c in cells[1:]] == [12, 16.4]
        assert cells[2].number_format == '#,##0.00;(#,##0.00);"–"'

        display = load_config(root).display_for("acme")
        qmd = build("acme", "standalone", display, "", collect(root, "acme", "standalone")).qmd
        assert "| Total assets | 12.00 INR lakh | 16.40 INR lakh |" in qmd
    finally:
        (root / "config.toml").write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")


def _template_divisions(text: str) -> list[str]:
    """The template expressions with a division outside their quoted strings."""
    return [
        m.group() for m in TEMPLATE_EXPRESSION.finditer(text) if "/" in QUOTED.sub("", m.group())
    ]


def test_no_python_or_template_code_divides_a_stored_value() -> None:
    assert PY_DIVISION.search("value / RUPEES_PER_UNIT[unit]")  # what the search finds
    assert PY_DIVISION.search('value / Decimal("100000")')
    assert PY_DIVISION.search("value / 1_000_000")
    assert _template_divisions("{{ row.value / 100000 }}")
    assert not _template_divisions('{{ act("x", ask="backed up to backups/ first") }}')
    found = [
        f"{path.relative_to(SRC)}:{n}: {line.strip()}"
        for path in SRC.rglob("*.py")
        if not any(part in LOADING for part in path.relative_to(SRC).parts)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if PY_DIVISION.search(line) and not line.lstrip().startswith("#")
    ]
    found += [
        f"{path.relative_to(SRC)}: {expression}"
        for path in SRC.rglob("*.html")
        for expression in _template_divisions(path.read_text(encoding="utf-8"))
    ]
    assert found == []
