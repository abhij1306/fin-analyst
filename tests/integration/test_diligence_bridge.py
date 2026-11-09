"""The EBITDA bridge and the EBITDA-to-cash walk (db/schema/diligence_views.sql,
`fin engagement bridge`) on Kestrel, against the answer key's figures (in rupees).

Adjustments are inserted with SQL, in each status, so these tests do not depend on how
`fin adjust accept` is gated."""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path

runner = CliRunner()
SAMPLES = Path(__file__).parents[1] / "fixtures" / "sample-financials"
WALK = (
    "ebitda",
    "non_cash_and_other",
    "working_capital_change",
    "taxes_paid",
    "other_operating",
    "cfo",
    "capex",
    "free_cash_flow",
    "cash_conversion",
    "adjusted_cash_conversion",
)
D = Decimal


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture(scope="module")
def ingested(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Kestrel ingested (shared, do not change it)."""
    root = (tmp_path_factory.mktemp("bridge") / "FinAnalysis").resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIN_DATA_DIR", str(root))
        assert runner.invoke(app, ["init"]).exit_code == 0
        result = runner.invoke(app, ["ingest", "kestrel", str(SAMPLES / "clean" / "kestrel")])
        assert result.exit_code == 0, result.output
    return root


@pytest.fixture
def root(ingested: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "FinAnalysis"
    shutil.copytree(ingested, copy)
    monkeypatch.setenv("FIN_DATA_DIR", str(copy))
    return copy


def execute(root: Path, sql: str, params: list[Any] | None = None) -> None:
    with connect(company_db_path(root, "kestrel"), write=True) as con:
        con.execute(sql, params or [])


def add_adjustment(
    root: Path, engagement_id: int, period: str, status: str, amount: str, description: str
) -> None:
    execute(
        root,
        "INSERT INTO adjustments (engagement_id, period, scope, category, amount, description, "
        "source_ref, status, created_run_id, created_at, status_run_id, status_at) "
        "VALUES (?, ?, 'standalone', 'normalisation', ?, ?, 'Notes p. 12', ?, 'r', now(), 'r', "
        "now())",
        [engagement_id, period, D(amount), description, status],
    )


def unconfirm(root: Path, period: str, where: str) -> None:
    """Take Kestrel's facts of `period` matching `where` out of every figure, as if not mapped."""
    execute(
        root,
        f"UPDATE facts SET confirmed = false WHERE period = ? AND ({where})",
        [period],
    )


def bridge_json(*args: str) -> dict[str, Any]:
    result = fin("engagement", "bridge", "kestrel", "--json", *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def walk(result: dict[str, Any], period: str) -> dict[str, str | None]:
    [row] = [w for w in result["walk"] if w["period"] == period]
    return {name: row[name] for name in WALK}


def squeezed(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines()]


@pytest.fixture
def engaged(root: Path) -> Path:
    """Engagement #1 on FY23-FY25. FY25: two accepted, one proposed, one rejected adjustment."""
    assert fin("engagement", "open", "kestrel", "--periods", "FY23,FY24,FY25").exit_code == 0
    add_adjustment(root, 1, "FY25", "accepted", "5000000.00", "Owner pay above market")
    add_adjustment(root, 1, "FY25", "proposed", "700000.00", "Consultancy, one-off")
    add_adjustment(root, 1, "FY25", "accepted", "-1250000.50", "Rent below market")
    add_adjustment(root, 1, "FY25", "rejected", "900000.00", "Bonus")
    return root


def test_no_engagement_is_e803(root: Path) -> None:
    result = fin("engagement", "bridge", "kestrel")

    assert result.exit_code == 2
    assert result.stderr.startswith("E803: kestrel has no engagement; nothing to show.")


def test_bridge_applies_only_accepted_adjustments(engaged: Path) -> None:
    result = bridge_json("--period", "FY25")

    steps = [(s["step"], s["kind"], s["adj_id"], s["amount"]) for s in result["bridge"]]
    assert steps == [
        (0, "reported", None, "241367000.00"),
        (1, "adjustment", 1, "5000000.00"),
        (2, "adjustment", 3, "-1250000.50"),
        (3, "adjusted", None, "245116999.50"),
    ]
    [summary] = result["adjusted_ebitda"]
    assert {k: summary[k] for k in list(summary)[4:]} == {
        "revenue": "1340929000.00",
        "reported_ebitda": "241367000.00",
        "accepted_adjustments": "3749999.50",
        "adjusted_ebitda": "245116999.50",
        "reported_ebitda_margin": "0.180000",
        "adjusted_ebitda_margin": "0.182796",
        "proposed_adjustments": "700000.00",
        "accepted_count": 2,
        "proposed_count": 1,
        "rejected_count": 1,
    }


def test_walk_lands_on_cfo(engaged: Path) -> None:
    result = bridge_json()

    assert walk(result, "FY25") == {
        "ebitda": "241367000.00",
        "non_cash_and_other": "0.00",
        "working_capital_change": "-94955000.00",
        "taxes_paid": "-43406000.00",
        "other_operating": "0.00",
        "cfo": "103006000.00",
        "capex": "-67046000.00",
        "free_cash_flow": "35960000.00",
        "cash_conversion": "0.426761",
        "adjusted_cash_conversion": "0.420232",
    }
    assert walk(result, "FY24") == {
        "ebitda": "227479000.00",
        "non_cash_and_other": "0.00",
        "working_capital_change": "-81030000.00",
        "taxes_paid": "-42099000.00",
        "other_operating": "0.00",
        "cfo": "104350000.00",
        "capex": "-107753000.00",
        "free_cash_flow": "-3403000.00",
        "cash_conversion": "0.458724",
        "adjusted_cash_conversion": "0.458724",
    }
    for row in result["walk"]:
        lines = ("ebitda", "non_cash_and_other", "working_capital_change", "taxes_paid")
        total = sum(D(row[name]) for name in (*lines, "other_operating"))
        assert total == D(row["cfo"]), row["period"]


def test_bridge_text_in_the_document_unit(engaged: Path) -> None:
    result = fin("engagement", "bridge", "kestrel", "--period", "FY25")

    assert result.exit_code == 0, result.output
    assert squeezed(result.stdout)[1:] == [
        "",
        "kestrel standalone FY25: EBITDA bridge (₹ in lakh)",
        "Reported EBITDA 2,413.67",
        "#1 normalisation: Owner pay above market 50.00",
        "#3 normalisation: Rent below market -12.50",
        "Adjusted EBITDA 2,451.17",
        "not applied: 1 proposed, 7.00",
        "kestrel standalone FY25: EBITDA to cash (₹ in lakh)",
        "EBITDA 2,413.67",
        "Non-cash and other items 0.00",
        "Working capital change -949.55",
        "Taxes paid -434.06",
        "Other operating items 0.00",
        "Cash from operations 1,030.06",
        "Capex -670.46",
        "Free cash flow 359.60",
        "Cash conversion (CFO / EBITDA) 42.68%",
        "Adjusted cash conversion (CFO / adjusted EBITDA) 42.02%",
    ]


def test_views_are_in_fin_sql(engaged: Path) -> None:
    result = fin(
        "sql",
        "kestrel",
        "--sql",
        "SELECT period, adjusted_ebitda FROM adjusted_ebitda ORDER BY period",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["rows"] == [
        {"period": "FY23", "adjusted_ebitda": "211903000.00"},
        {"period": "FY24", "adjusted_ebitda": "227479000.00"},
        {"period": "FY25", "adjusted_ebitda": "245116999.50"},
    ]


def test_missing_cash_flow_gives_null_lines_not_zeros(engaged: Path) -> None:
    # FY23: no cash flow at all. FY24: cash generated from operations not mapped.
    unconfirm(
        engaged,
        "FY23",
        "raw_id IN (SELECT raw_id FROM raw_values WHERE statement = 'cash_flow')",
    )
    unconfirm(engaged, "FY24", "account = 'cf_cash_generated'")

    result = bridge_json()

    assert walk(result, "FY23") == {
        "ebitda": "211903000.00",
        "non_cash_and_other": None,
        "working_capital_change": None,
        "taxes_paid": None,
        "other_operating": None,
        "cfo": None,
        "capex": None,
        "free_cash_flow": None,
        "cash_conversion": None,
        "adjusted_cash_conversion": None,
    }
    assert walk(result, "FY24") == {
        "ebitda": "227479000.00",
        "non_cash_and_other": "0.00",
        "working_capital_change": None,
        "taxes_paid": "-42099000.00",
        "other_operating": None,
        "cfo": "104350000.00",
        "capex": "-107753000.00",
        "free_cash_flow": "-3403000.00",
        "cash_conversion": "0.458724",
        "adjusted_cash_conversion": "0.458724",
    }
    text = squeezed(fin("engagement", "bridge", "kestrel", "--period", "FY24").stdout)
    assert "Working capital change - (missing: cf_cash_generated)" in text
    assert "Other operating items - (missing: cf_cash_generated)" in text


def test_taxes_paid_not_mapped(engaged: Path) -> None:
    # FY25: taxes paid not mapped while CFO differs from cash generated: not guessed, NULL.
    # FY24: no taxes line and CFO equal to cash generated: nothing between them, so 0.
    unconfirm(engaged, "FY25", "account = 'cf_taxes_paid'")
    unconfirm(engaged, "FY24", "account = 'cf_taxes_paid'")
    execute(
        engaged,
        "UPDATE facts SET value = 146449000 WHERE period = 'FY24' AND account = 'cfo'",
    )

    result = bridge_json()

    fy25, fy24 = walk(result, "FY25"), walk(result, "FY24")
    assert (fy25["taxes_paid"], fy25["other_operating"], fy25["cfo"]) == (
        None,
        None,
        "103006000.00",
    )
    assert (fy24["taxes_paid"], fy24["other_operating"], fy24["cfo"]) == (
        "0.00",
        "0.00",
        "146449000.00",
    )


def test_the_latest_engagement_is_shown(engaged: Path) -> None:
    # Closing needs gate G4: no proposed adjustment left, and G1 and G2 approved before it.
    execute(engaged, "UPDATE adjustments SET status = 'rejected' WHERE status = 'proposed'")
    for gate in ("G1", "G2", "G4"):
        assert fin("gate", "approve", "kestrel", gate).exit_code == 0, gate
    assert fin("engagement", "close", "kestrel").exit_code == 0
    closed = bridge_json("--period", "FY25")
    assert closed["engagement"]["status"] == "closed"
    assert closed["adjusted_ebitda"][0]["adjusted_ebitda"] == "245116999.50"

    assert fin("engagement", "open", "kestrel", "--periods", "FY24").exit_code == 0
    result = bridge_json()

    assert [(r["engagement_id"], r["period"], r["adjusted_ebitda"]) for r in result["walk"]] == [
        (2, "FY24", "227479000.00")
    ]
