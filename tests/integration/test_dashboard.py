"""Phase 10: `fin dashboard` data snapshot, ingest-while-open, and build failure handling.

The Evidence pages themselves are built and checked in the CI `outputs` job."""

import shutil
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.dashboard import snapshot_analytics
from fin.db.conn import connect
from fin.paths import analytics_db_path

runner = CliRunner()
CLEAN = Path(__file__).parents[1] / "fixtures" / "sample-financials" / "clean"

# One-line launcher for a real, separate `fin` process (an ingest running next to the dashboard).
FIN = [sys.executable, "-c", "from fin.cli import app; app()"]


def ingest(company: str) -> None:
    result = runner.invoke(app, ["ingest", company, str(CLEAN / company)])
    assert result.exit_code == 0, result.output


def test_snapshot_copies_the_analytics_tables(data_root: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    ingest("kestrel")
    ingest("bluepeak")

    snapshot = snapshot_analytics(data_root)

    assert snapshot == data_root / "dashboard" / "analytics.duckdb"
    with connect(snapshot) as con:
        assert con.execute("SELECT company FROM companies ORDER BY company").fetchall() == [
            ("bluepeak",),
            ("kestrel",),
        ]
        assert con.execute("SELECT count(*) FROM metrics").fetchone() == (461,)
        # Every table the pages read is in the snapshot.
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        assert tables == {
            "metrics",
            "companies",
            "metric_definitions",
            "statements",
            "findings",
            "coverage",
            "unread_pages",
            "unverified_facts",
        }
    # The live file is not held after the snapshot: a writer opens it at once.
    with connect(analytics_db_path(data_root), write=True):
        pass


def test_analytics_has_categories_statements_and_coverage(data_root: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    ingest("bluepeak")

    with connect(analytics_db_path(data_root)) as con:
        assert con.execute(
            "SELECT metric, category FROM metrics WHERE company = 'bluepeak' AND period = 'FY25' "
            "AND scope = 'standalone' AND metric IN ('ebitda_margin', 'roe', 'receivable_days') "
            "ORDER BY metric"
        ).fetchall() == [
            ("ebitda_margin", "profitability"),
            ("receivable_days", "efficiency"),
            ("roe", "returns"),
        ]
        assert con.execute(
            "SELECT category, unit, periods FROM metric_definitions WHERE metric = 'roe_avg'"
        ).fetchall() == [("returns", "ratio", "annual")]
        # Statements in the documents' unit (Rs. in Crores).
        assert con.execute(
            "SELECT scope, statement, label, value, unit FROM statements "
            "WHERE period = 'FY25' AND label IN ('Total assets', 'Revenue from operations') "
            "ORDER BY ALL"
        ).fetchall() == [
            ("consolidated", "balance_sheet", "Total assets", Decimal("289.05"), "₹ in crore"),
            (
                "consolidated",
                "profit_and_loss",
                "Revenue from operations",
                Decimal("285.75"),
                "₹ in crore",
            ),
            ("standalone", "balance_sheet", "Total assets", Decimal("272.03"), "₹ in crore"),
            (
                "standalone",
                "profit_and_loss",
                "Revenue from operations",
                Decimal("268.82"),
                "₹ in crore",
            ),
        ]
        assert con.execute(
            "SELECT scope, statement, figures, mapped, mapped_share, claude_unverified "
            "FROM coverage WHERE scope = 'standalone' ORDER BY statement"
        ).fetchall() == [
            ("standalone", "balance_sheet", 84, 84, Decimal("1"), 0),
            ("standalone", "cash_flow", 144, 144, Decimal("1"), 0),
            ("standalone", "profit_and_loss", 72, 72, Decimal("1"), 0),
        ]
        assert con.execute("SELECT count(*) FROM findings").fetchone() == (0,)


def test_analytics_lists_findings_of_a_held_document(data_root: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    ingest("kestrel")
    broken = CLEAN.parent / "broken" / "Kestrel_UNBALANCED_balance_sheet.xlsx"
    result = runner.invoke(app, ["ingest", "kestrel", str(broken)])
    assert result.exit_code == 0, result.output

    with connect(analytics_db_path(data_root)) as con:
        rows = con.execute(
            "SELECT DISTINCT company, status, document_status, file_name, code FROM findings "
            "ORDER BY code"
        ).fetchall()
    file = "Kestrel_UNBALANCED_balance_sheet.xlsx"
    assert rows == [
        ("kestrel", "open", "held", file, "E401"),  # the balance sheet does not tie
        ("kestrel", "open", "held", file, "E404"),  # figures differ from the clean workbook
    ]


def test_an_analytics_file_from_an_older_fin_is_made_again(data_root: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    with connect(analytics_db_path(data_root), write=True) as con:
        con.execute("DROP TABLE coverage")

    ingest("kestrel")

    with connect(analytics_db_path(data_root)) as con:
        assert con.execute("SELECT count(*) FROM coverage").fetchone() == (3,)


def test_ingest_runs_while_the_dashboard_reads(data_root: Path) -> None:
    """The dashboard only ever reads a snapshot, and takes the live file briefly to make it. Take
    a snapshot every 0.2 s while a separate `fin ingest` process loads another company: both
    succeed with no lock error."""
    assert runner.invoke(app, ["init"]).exit_code == 0
    ingest("kestrel")

    errors: list[Exception] = []
    snapshots = 0
    stop = threading.Event()

    def refresh_forever() -> None:
        nonlocal snapshots
        while not stop.is_set():
            try:
                snapshot_analytics(data_root)
                snapshots += 1
                stop.wait(0.2)  # a dashboard refresh, not a busy loop
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                errors.append(exc)
                return

    reader = threading.Thread(target=refresh_forever)
    reader.start()
    try:
        done = subprocess.run(
            [*FIN, "ingest", "bluepeak", str(CLEAN / "bluepeak")],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        stop.set()
        reader.join()

    assert done.returncode == 0, done.stdout + done.stderr
    assert "E501" not in done.stdout + done.stderr
    assert errors == []
    assert snapshots >= 1
    with connect(snapshot_analytics(data_root)) as con:
        assert con.execute("SELECT count(*) FROM companies").fetchone() == (2,)


@pytest.fixture
def fake_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in Evidence project whose `sources` and `build` scripts run real npm."""
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed")
    project = tmp_path / "dashboards"
    (project / "node_modules").mkdir(parents=True)
    (project / "package.json").write_text(
        '{"scripts": {"sources": "node ok.js", "build": "node build.js"}}', encoding="utf-8"
    )
    (project / "ok.js").write_text("", encoding="utf-8")
    monkeypatch.setattr("fin.dashboard.PROJECT_DIR", project)
    return project


def test_build_copies_the_site_to_the_data_folder(data_root: Path, fake_project: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    (fake_project / "build.js").write_text(
        "const fs = require('fs');"
        "fs.mkdirSync('build');"
        "fs.writeFileSync('build/index.html', 'v1');",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["dashboard", "--build"])

    assert result.exit_code == 0, result.output
    assert (data_root / "dashboard" / "build" / "index.html").read_text() == "v1"


def test_failed_build_is_e603_and_keeps_the_last_good_build(
    data_root: Path, fake_project: Path
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    good = data_root / "dashboard" / "build"
    good.mkdir(parents=True)
    (good / "index.html").write_text("last good", encoding="utf-8")
    (fake_project / "build.js").write_text("process.exit(1);", encoding="utf-8")

    result = runner.invoke(app, ["dashboard", "--build"])

    assert result.exit_code == 2
    assert "E603" in result.output
    assert (good / "index.html").read_text() == "last good"
