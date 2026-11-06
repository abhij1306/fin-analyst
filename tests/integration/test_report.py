"""`fin report` on the ingested synthetic company Kestrel (standalone).

Rendering needs Quarto. Most tests put a small stand-in `quarto` first on PATH that writes the three
output files, so the lint, substitution, data hash, swap-in and audit row are tested everywhere.
`test_quarto_renders_all_three_formats` uses the real Quarto and is skipped without it; the CI
`report` job and the macOS workflow have it.
"""

import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import audit_db_path

runner = CliRunner()
TESTS = Path(__file__).resolve().parents[1]
SAMPLES = TESTS / "fixtures" / "sample-financials"
NARRATIVE = TESTS / "fixtures" / "report" / "kestrel_narrative.md"
OUTPUTS = ("health.html", "health.docx", "health.pdf")

FAKE_QUARTO = """\
import os
import sys
from pathlib import Path

if os.environ.get("FAKE_QUARTO_FAIL"):
    print("ERROR: typst compile failed", file=sys.stderr)
    sys.exit(1)
assert sys.argv[1:] == ["render", "health.qmd", "--to", "all"], sys.argv
text = Path("health.qmd").read_text(encoding="utf-8")
for name in ("health.html", "health.docx", "health.pdf"):
    Path(name).write_text(text, encoding="utf-8")
"""


@pytest.fixture(scope="module")
def ingested(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A data folder with Kestrel ingested (shared, do not change it)."""
    root = (tmp_path_factory.mktemp("report") / "FinAnalysis").resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIN_DATA_DIR", str(root))
        assert runner.invoke(app, ["init"]).exit_code == 0
        result = runner.invoke(app, ["ingest", "kestrel", str(SAMPLES / "clean" / "kestrel")])
        assert result.exit_code == 0, result.output
    yield root


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
        "## Growth",
        "## Profitability",
        "## Cash flow quality",
        "## Working capital",
        "## Leverage and liquidity",
        "## Earnings quality",
        "## Observations",
        "## Data quality",
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
    assert "| EBITDA margin | 20.0% | 19.0% | 18.0% |" in html
    assert "| Free cash flow | ₹5.87 Cr | -₹0.34 Cr | ₹3.60 Cr |" in html
    assert "| FY23 | Revenue growth | revenue (FY22) |" in html
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
    assert len(hashes) == 2 and hashes[0] == hashes[1]

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


@pytest.mark.skipif(shutil.which("quarto") is None, reason="needs Quarto (CI `report` job, macOS)")
def test_quarto_renders_all_three_formats(root: Path) -> None:
    write_narrative(root)
    result = report()
    assert result.exit_code == 0, result.output
    for name in OUTPUTS:
        assert (folder(root) / name).stat().st_size > 0
    html = (folder(root) / "health.html").read_text(encoding="utf-8")
    assert "18.0%" in html
    assert (folder(root) / "health.pdf").read_bytes().startswith(b"%PDF")
