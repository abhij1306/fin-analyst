"""`fin report` on the ingested synthetic company Kestrel (standalone).

Rendering needs Quarto. Most tests put a small stand-in `quarto` first on PATH that writes the three
output files, so the lint, substitution, data hash, swap-in and audit row are tested everywhere.
`test_quarto_renders_all_three_formats` uses the real Quarto (marker `quarto`) and is skipped
without it; the CI `outputs` job runs it.
"""

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import audit_db_path
from tests.integration.test_ingest_results import q1_results, q2_results, q4_results

runner = CliRunner()
TESTS = Path(__file__).resolve().parents[1]
SAMPLES = TESTS / "fixtures" / "sample-financials"
NARRATIVE = TESTS / "fixtures" / "report" / "kestrel_narrative.md"
OUTPUTS = ("health.html", "health.docx", "health.pdf")

FAKE_QUARTO = """\
import os
import re
import sys
from pathlib import Path

if os.environ.get("FAKE_QUARTO_FAIL"):
    print("ERROR: typst compile failed", file=sys.stderr)
    sys.exit(1)
assert sys.argv[1:] == ["render", "health.qmd", "--to", "all"], sys.argv
text = Path("health.qmd").read_text(encoding="utf-8")
# Every chart the report shows is written next to it.
for chart in re.findall("[(](chart-[a-z_]+[.]svg)[)]", text):
    assert Path(chart).read_text(encoding="utf-8").startswith("<svg"), chart
for name in ("health.html", "health.docx", "health.pdf"):
    Path(name).write_text(text, encoding="utf-8")
"""


@pytest.fixture(scope="module")
def ingested(clean_template: Callable[..., Path]) -> Path:
    """A data folder with Kestrel ingested (shared, do not change it)."""
    return clean_template("kestrel")


@pytest.fixture
def root(ingested: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "FinAnalysis"
    shutil.copytree(ingested, copy)
    monkeypatch.setenv("FIN_DATA_DIR", str(copy))
    return copy


@pytest.fixture
def fake_quarto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a stand-in `quarto` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake_quarto.py"
    script.write_text(FAKE_QUARTO, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "quarto.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        launcher = bin_dir / "quarto"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("FAKE_QUARTO_FAIL", raising=False)


def folder(root: Path) -> Path:
    return root / "reports" / "kestrel" / "standalone"


def report(*args: str) -> Result:
    return runner.invoke(app, ["report", "kestrel", *args])


def write_narrative(root: Path, text: str | None = None) -> Path:
    path = folder(root) / "narrative.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else NARRATIVE.read_text(encoding="utf-8"), "utf-8")
    return path


def report_rows(root: Path) -> list[tuple[Any, ...]]:
    with connect(audit_db_path(root)) as con:
        return con.execute(
            "SELECT company, output_paths, template_commit, data_hash, narrative_path "
            "FROM reports ORDER BY recorded_at"
        ).fetchall()


def test_first_run_writes_the_stub_and_stops(root: Path) -> None:
    result = report()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "stub_written"
    stub = (folder(root) / "narrative.md").read_text(encoding="utf-8")
    headings = [line for line in stub.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Summary",
        "## Profitability",
        "## Returns",
        "## Liquidity",
        "## Solvency",
        "## Efficiency and working capital",
        "## Cash flow",
        "## Growth",
        "## Earnings quality",
        "## Findings",
        "## Data coverage",
    ]
    assert "Years: FY23, FY24, FY25" in stub
    assert not any((folder(root) / name).exists() for name in OUTPUTS)
    assert report_rows(root) == []
    # The stub itself passes the lint and renders (empty sections) once Quarto is there.
    assert "E602" not in report().output


def test_render_only_without_narrative_is_e601(root: Path) -> None:
    result = report("--render")
    assert result.exit_code == 2
    assert "E601: " in result.stderr
    assert "narrative.md does not exist" in result.stderr


def test_render_substitutes_references_and_logs_the_report(root: Path, fake_quarto: None) -> None:
    narrative = write_narrative(root)
    result = report()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "rendered"
    assert payload["missing_values"] == []
    outputs = [str(folder(root) / name) for name in (*OUTPUTS, "report_data.json")]
    assert payload["outputs"] == outputs
    assert all(Path(path).is_file() for path in outputs)
    assert not list(folder(root).glob(".render-*"))

    html = (folder(root) / "health.html").read_text(encoding="utf-8")
    # Narrative references, in the config's display unit (crores).
    assert "Revenue was ₹105.95 Cr in FY23, ₹119.73 Cr in FY24 and ₹134.09 Cr\nin FY25." in html
    assert "EBITDA margin moved from 20.0% in FY23 to 18.0%\nin FY25." in html
    assert "Receivable days went from 62.0 days in FY23 to\n94.0 days in FY25." in html
    # Tables come from the metrics view; missing data is listed.
    assert "**Standalone**\n\n|  | FY23 | FY24 | FY25 |" in html  # the summary
    assert "| EBITDA margin | 20.0% | 19.0% | 18.0% |" in html
    assert "| Free cash flow | ₹5.87 Cr | -₹0.34 Cr | ₹3.60 Cr |" in html
    assert "| ROE (average equity) | n/a | 19.5% | 16.9% |" in html
    assert "| Receivable days | 62.0 days | 78.0 days | 94.0 days |" in html
    assert "![Profitability, FY23 to FY25](chart-profitability.svg){width=100%}" in html
    assert "## Lending" not in html  # no lender metrics for a manufacturer
    assert "## Latest quarters" not in html
    assert "## Peer comparison" not in html
    # Findings, coverage and the appendices.
    assert "No open or accepted findings." in html
    assert "| Balance sheet | 54 | 54 | 100.0% | 0 |" in html
    assert "None: fin read every statement page of the loaded documents." in html
    assert "| FY23 | Revenue growth | revenue (FY22) |" in html
    assert "| Total assets | ₹98.75 Cr | ₹116.44 Cr | ₹128.64 Cr |" in html
    assert "| Inventories |" not in html  # condensed: subtotals only
    assert "| profitability | EBITDA margin (`ebitda_margin`) | EBITDA as a fraction of" in html
    assert "| `revenue` | FY25 | ₹134.09 Cr | Kestrel_Forgings_Financials_FY23-FY25.xlsx" in html
    assert "{{" not in html

    data = json.loads((folder(root) / "report_data.json").read_text(encoding="utf-8"))
    assert data["references"]["{{metric:pat_margin:FY25}}"] == "9.6%"
    assert data["references"]["{{fact:other_income:FY25}}"] == "₹0.54 Cr"
    assert data["data"]["metrics"]["ebitda_margin"] == {
        "FY23": "0.199999",
        "FY24": "0.190000",
        "FY25": "0.180000",
    }
    [(company, paths, commit, digest, narrative_path)] = report_rows(root)
    assert (company, json.loads(paths), digest, narrative_path) == (
        "kestrel",
        outputs,
        payload["data_hash"],
        str(narrative),
    )
    assert digest == data["data_hash"]
    assert commit is None or len(commit) == 40


def test_rerender_with_unchanged_data_gives_identical_numbers_and_hash(
    root: Path, fake_quarto: None
) -> None:
    write_narrative(root)
    data_file = folder(root) / "report_data.json"
    assert report().exit_code == 0
    first = data_file.read_text(encoding="utf-8")
    assert report().exit_code == 0
    assert data_file.read_text(encoding="utf-8") == first
    hashes = [row[3] for row in report_rows(root)]
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]

    # A changed value changes the hash.
    sql = (
        "UPDATE facts SET value = value + 1 WHERE fact_id = "
        "(SELECT fact_id FROM current_facts WHERE account = 'revenue' AND fiscal_year = 'FY25')"
    )
    assert runner.invoke(app, ["sql", "kestrel", "--write", "--sql", sql]).exit_code == 0
    assert report().exit_code == 0
    assert report_rows(root)[-1][3] != hashes[0]


def test_typed_number_blocks_the_render(root: Path, fake_quarto: None) -> None:
    text = NARRATIVE.read_text(encoding="utf-8").replace(
        "Revenue growth was {{metric:revenue_growth:FY24}}", "Revenue growth was 13%"
    )
    write_narrative(root, text)
    result = report()
    assert result.exit_code == 2
    assert "E602: " in result.stderr
    assert "  line 9: in FY25. Revenue growth was 13% in FY24 and" in result.stderr
    assert not any((folder(root) / name).exists() for name in OUTPUTS)
    assert report_rows(root) == []


def test_unknown_reference_is_e601(root: Path, fake_quarto: None) -> None:
    text = "## Growth\n\nRevenue was {{fact:revenue:FY21}} and {{metric:nope:FY25}}.\n"
    write_narrative(root, text)
    result = report()
    assert result.exit_code == 2
    assert (
        "E601: the narrative has references that cannot be filled:\n"
        "  line 3: {{fact:revenue:FY21}}: no FY21 data\n"
        "  line 3: {{metric:nope:FY25}}: no metric nope"
    ) in result.stderr


def test_failed_render_keeps_the_last_good_report(
    root: Path, fake_quarto: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_narrative(root)
    assert report().exit_code == 0
    before = {name: (folder(root) / name).read_bytes() for name in OUTPUTS}

    write_narrative(root, "## Growth\n\nRevenue was {{fact:revenue:FY25}}.\n")
    monkeypatch.setenv("FAKE_QUARTO_FAIL", "1")
    result = report()
    assert result.exit_code == 2
    assert "E601: quarto render exited with 1:\nERROR: typst compile failed" in result.stderr
    assert {name: (folder(root) / name).read_bytes() for name in OUTPUTS} == before
    assert not list(folder(root).glob(".render-*"))
    assert len(report_rows(root)) == 1


ACME_NARRATIVE = """\
## Summary

Revenue was {{fact:revenue:FY25}} in FY25.

## Latest quarters

Revenue was {{metric:revenue:FY26-Q2}} in FY26-Q2 and {{metric:ttm_revenue:FY26-Q2}} over the
last four quarters.

## Peer comparison

The table sets acme next to kestrel for the latest fiscal year both have.
"""


def ingest_quarters(root: Path, tmp_path: Path) -> Path:
    """acme: results statements for FY24-Q4 to FY26-Q2 with their 'Year ended' columns (FY24,
    FY25), and its narrative."""
    for path in (
        q4_results(tmp_path / "q4.pdf"),
        q1_results(tmp_path / "q1.pdf"),
        q2_results(tmp_path / "q2.pdf"),
    ):
        result = runner.invoke(app, ["ingest", "acme", str(path)])
        assert result.exit_code == 0, result.output
    narrative = root / "reports" / "acme" / "standalone" / "narrative.md"
    narrative.parent.mkdir(parents=True)
    narrative.write_text(ACME_NARRATIVE, encoding="utf-8")
    return narrative.parent


def test_latest_quarters_and_peer_comparison(root: Path, tmp_path: Path, fake_quarto: None) -> None:
    acme = ingest_quarters(root, tmp_path)

    result = runner.invoke(app, ["report", "acme", "--peers", "kestrel"])

    assert result.exit_code == 0, result.output
    html = (acme / "health.html").read_text(encoding="utf-8")
    assert "Revenue was ₹12.10 Cr in FY26-Q2 and ₹43.90 Cr over the\nlast four quarters." in html
    assert "|  | FY25-Q2 | FY25-Q3 | FY25-Q4 | FY26-Q1 | FY26-Q2 |" in html
    assert "| Revenue | ₹10.20 Cr | ₹10.30 Cr | ₹10.50 Cr | ₹11.00 Cr | ₹12.10 Cr |" in html
    assert "| Revenue growth on the previous quarter | 2.0% | 1.0% | 1.9% | 4.8% | 10.0% |" in html
    assert (
        "| Revenue, last four quarters | n/a | ₹40.00 Cr | ₹41.00 Cr | ₹42.00 Cr | ₹43.90 Cr |"
        in (html)
    )
    assert "Fiscal year FY25, standalone.\n\n|  | acme | kestrel |" in html
    assert "| Revenue | ₹41.00 Cr | ₹134.09 Cr |" in html
    assert "| Profit after tax | ₹29.00 Cr | ₹12.90 Cr |" in html

    # Without --peers the narrative's peer section has nowhere to go.
    result = runner.invoke(app, ["report", "acme"])
    assert result.exit_code == 2
    assert "the narrative has text under 'Peer comparison'" in result.stderr

    result = runner.invoke(app, ["report", "acme", "--peers", "nobody"])
    assert result.exit_code == 2
    assert "E601: no database for peer nobody" in result.stderr


@pytest.mark.quarto
@pytest.mark.skipif(shutil.which("quarto") is None, reason="needs Quarto (CI `outputs` job)")
def test_quarto_renders_all_three_formats(root: Path, tmp_path: Path) -> None:
    write_narrative(root)
    result = report()
    assert result.exit_code == 0, result.output
    for name in OUTPUTS:
        assert (folder(root) / name).stat().st_size > 0
    html = (folder(root) / "health.html").read_text(encoding="utf-8")
    assert "18.0%" in html
    assert (folder(root) / "health.pdf").read_bytes().startswith(b"%PDF")

    # Every optional section too: latest quarters and the peer comparison, with charts.
    acme = ingest_quarters(root, tmp_path)
    result = runner.invoke(app, ["report", "acme", "--peers", "kestrel"])
    assert result.exit_code == 0, result.output
    html = (acme / "health.html").read_text(encoding="utf-8")
    assert "Latest quarters" in html
    assert "Peer comparison" in html
    assert "<svg" in html  # the charts are embedded
    assert (acme / "health.pdf").read_bytes().startswith(b"%PDF")
