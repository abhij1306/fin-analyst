"""Figures the user states (fin.facts): stored as the user's, used by the metrics while no
loaded document gives them, listed in the report's data coverage, and never replacing a
document's figure."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from fin.actor import CLAUDE, USER
from fin.audit.errors import FigureCannotBeCorrected, FigureCannotBeStated, StatedFigureUnknown
from fin.audit.logger import Run
from fin.audit.trace import fact_trace, format_fact_trace, format_metric_trace, metric_trace
from fin.config import write_default_config
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence.gates import Context, evaluate
from fin.display import display_settings
from fin.facts import (
    correct,
    list_corrected,
    list_stated,
    parse_value,
    period_dates,
    state,
    unstate,
)
from fin.findings import format_finding, list_findings
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from fin.query.evidence import write_evidence
from fin.query.run import run_select, to_json_rows
from fin.report.data import _CORRECTED_SQL, _FACTS_SQL, _FINDINGS_SQL, _STATED_SQL
from fin.report.tables import corrected_table, stated_table
from fin.statements import format_statements, statement_rows
from fin.status import company_status
from fin.workbook import export_workbook

# FY24 P&L of a document that prints no finance cost line.
PNL = {"revenue": "1000.00", "pbt": "200.00", "depreciation": "50.00", "other_income": "30.00"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    add_document(path, 1, PNL)
    return root


def add_document(path: Path, doc_id: int, lines: dict[str, str]) -> None:
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at, period_end) VALUES (?, ?, ?, 1, 'FY24', "
            "'standalone', 'loaded', 'a', 'r', now(), DATE '2024-03-31')",
            [doc_id, f"ar{doc_id}.xlsx", f"sha{doc_id}"],
        )
        for row, (account, value) in enumerate(lines.items(), start=doc_id * 100):
            [(raw_id,)] = con.execute(
                "INSERT INTO raw_values (doc_id, sheet, cell, statement, section, raw_label, "
                "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (?, 'P&L', ?, "
                "'profit_and_loss', '', ?, ?, '(Rs.)', 1, 'r') RETURNING raw_id",
                [doc_id, f"C{row}", account, value],
            ).fetchall()
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, ?, ?, 'FY24', "
                "DATE '2024-03-31', 'standalone', ?, true, 'r', 'FY24', 12)",
                [raw_id, doc_id, account, value],
            )


def query(root: Path, sql: str) -> list[Any]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def ebitda(root: Path) -> Any:
    return query(root, "SELECT value FROM metrics WHERE metric = 'ebitda' AND period = 'FY24'")


def nil_finance_cost(root: Path) -> dict[str, Any]:
    return state(
        Run("fact state", {}, root),
        "acme",
        "finance_cost",
        "FY24",
        "standalone",
        "0",
        "The P&L prints no finance cost line; the company has no borrowings",
        actor=USER,
    )


def test_a_stated_nil_lets_the_metric_compute_and_is_in_the_data_coverage(root: Path) -> None:
    assert ebitda(root) == [(None,)]

    result = nil_finance_cost(root)

    assert (result["outcome"], result["stated_id"]) == ("stated", 1)
    assert ebitda(root) == [(Decimal("220.000000"),)]
    assert query(
        root,
        "SELECT fact_id, doc_id, value, stated_id FROM current_facts "
        "WHERE account = 'finance_cost'",
    ) == [(None, None, Decimal("0.00"), 1)]
    reason = "The P&L prints no finance cost line; the company has no borrowings"
    with connect(company_db_path(root, "acme")) as con:
        stated = fetch_dicts(con, _STATED_SQL, ["standalone"])
    assert stated_table({"stated": stated}) == (
        "| Account | Period | Value (rupees; per share for EPS) | In use | Reason |\n"
        "|---|---|---|---|---|\n"
        "| `finance_cost` | FY24 | 0.00 | yes | The P&amp;L prints no finance cost line; the "
        "company has no borrowings |"
    )
    [row] = list_stated(root, "acme")
    assert (row["read_by"], row["reason"], row["in_use"]) == ("user", reason, True)
    assert (root / result["backup_path"]).is_file()
    with connect(audit_db_path(root)) as con:
        assert con.execute(
            "SELECT action, stated_id, account, value, reason FROM stated_figures"
        ).fetchall() == [("stated", 1, "finance_cost", Decimal("0.00"), reason)]


def test_the_metric_trace_names_the_stated_input(root: Path) -> None:
    nil_finance_cost(root)

    text = format_metric_trace(metric_trace(root, "acme", "ebitda", "FY24"))

    assert (
        "  finance_cost FY24: 0.00 = stated by the user (#1, `fin fact list`), not from a "
        "document" in text.splitlines()
    )


def test_stating_a_figure_a_document_gives_records_a_finding_and_changes_nothing(
    root: Path,
) -> None:
    run = Run("fact state", {}, root)

    result = state(
        run, "acme", "revenue", "FY24", "standalone", "1200", "From the MIS pack", actor=USER
    )

    assert result["outcome"] == "finding"
    assert result["document"] == {
        "file_name": "ar1.xlsx",
        "source": "P&L!C100",
        "value": Decimal("1000.00"),
        "fact_id": 1,
    }
    assert query(root, "SELECT count(*) FROM stated_figures") == [(0,)]
    assert query(root, "SELECT value FROM current_facts WHERE account = 'revenue'") == [
        (Decimal("1000.00"),)
    ]
    [finding] = list_findings(run, "acme")
    assert (finding["finding_id"], finding["status"], finding["check_name"]) == (
        result["finding_id"],
        "open",
        "stated_figure",
    )
    assert (finding["fin_value"], finding["other_value"], finding["difference"]) == (
        Decimal("1000.00"),
        Decimal("1200.00"),
        Decimal("200.00"),
    )
    assert format_finding(finding).splitlines() == [
        f"#{result['finding_id']} open stated_figure acme/ar1.xlsx (stated figure): revenue FY24 "
        "standalone was stated by the user, but ar1.xlsx gives it: a stated figure never "
        "replaces a document's, so nothing was stated",
        "    this document: ₹1,000.00 at P&L!C100; compared with: ₹1,200.00 at stated by the "
        "user: From the MIS pack",
    ]


def test_a_document_loaded_later_takes_the_place_of_a_stated_figure(root: Path) -> None:
    nil_finance_cost(root)

    add_document(company_db_path(root, "acme"), 2, {"finance_cost": "40.00"})

    assert query(
        root, "SELECT value, stated_id FROM current_facts WHERE account = 'finance_cost'"
    ) == [(Decimal("40.00"), None)]
    assert [r["in_use"] for r in list_stated(root, "acme")] == [False]


def test_unstate_withdraws_the_figure_once(root: Path) -> None:
    nil_finance_cost(root)
    run = Run("fact unstate", {}, root)

    result = unstate(run, "acme", 1, actor=USER)

    assert (result["outcome"], result["account"]) == ("withdrawn", "finance_cost")
    assert ebitda(root) == [(None,)]
    assert list_stated(root, "acme") == []
    assert query(root, "SELECT status, withdrawn_run_id FROM stated_figures") == [
        ("withdrawn", run.run_id)
    ]
    with pytest.raises(StatedFigureUnknown, match="E408: acme has no stated figure #1"):
        unstate(run, "acme", 1, actor=USER)


@pytest.mark.parametrize(
    ("account", "period", "value", "reason", "message"),
    [
        ("finance_costs", "FY24", "0", "r", "account 'finance_costs' is not in the chart"),
        ("finance_cost", "2024", "0", "r", "period '2024': use FY25"),
        ("finance_cost", "FY24-Q5", "0", "r", "period 'FY24-Q5': use FY25"),
        ("finance_cost", "FY24", "1.005", "r", "value '1.005' has more than two decimals"),
        ("finance_cost", "FY24", "ten", "r", "value 'ten' is not a number"),
        ("finance_cost", "FY24", "NaN", "r", "value 'NaN' is not a number"),
        ("finance_cost", "FY24", "1e16", "r", "value '1e16' is 10^16 rupees or more"),
        ("finance_cost", "FY24", "1,2,3", "r", "value '1,2,3': commas only between digit groups"),
        ("finance_cost", "FY24", "0", "  ", "give the reason"),
    ],
)
def test_a_figure_that_cannot_be_stated_is_e407_and_stores_nothing(
    root: Path, account: str, period: str, value: str, reason: str, message: str
) -> None:
    run = Run("fact state", {}, root)

    with pytest.raises(FigureCannotBeStated, match=re.escape(message)) as e:
        state(run, "acme", account, period, "standalone", value, reason, actor=USER)

    assert str(e.value).startswith("E407: ")
    assert query(root, "SELECT count(*) FROM stated_figures") == [(0,)]
    assert not (root / "backups").exists()


def test_a_figure_stated_twice_is_e407(root: Path) -> None:
    nil_finance_cost(root)

    with pytest.raises(FigureCannotBeStated, match=r"already stated \(#1, 0\.00\)"):
        nil_finance_cost(root)

    assert query(root, "SELECT count(*) FROM stated_figures") == [(1,)]


@pytest.mark.parametrize(
    ("period", "start", "expected"),
    [
        ("FY25", 4, ("FY25", 12, date(2025, 3, 31))),
        ("FY26-Q1", 4, ("FY26", 3, date(2025, 6, 30))),
        ("FY26-Q4", 4, ("FY26", 3, date(2026, 3, 31))),
        ("FY26-H1", 4, ("FY26", 6, date(2025, 9, 30))),
        ("FY26-H2", 4, ("FY26", 6, date(2026, 3, 31))),
        ("FY26-9M", 4, ("FY26", 9, date(2025, 12, 31))),
        ("FY25", 1, ("FY25", 12, date(2025, 12, 31))),
        ("FY25-Q1", 1, ("FY25", 3, date(2025, 3, 31))),
    ],
)
def test_period_dates(period: str, start: int, expected: tuple[str, int, date]) -> None:
    assert period_dates(period, start) == expected


def test_a_metric_without_an_input_names_it_and_how_to_give_it(root: Path) -> None:
    text = format_metric_trace(metric_trace(root, "acme", "ebitda", "FY24"))

    assert text.splitlines()[1] == (
        "  EBITDA not computed for FY24 standalone: no finance_cost for FY24. Load a document "
        "that has it, or state it (`fin fact state acme --account finance_cost --period FY24 "
        "--scope standalone --value 0 --reason ...` for a nil)"
    )
    nil_finance_cost(root)
    assert metric_trace(root, "acme", "ebitda", "FY24")["scopes"][0]["not_computed"] is None


def test_a_stated_figure_is_marked_in_the_statements_the_workbook_and_status(root: Path) -> None:
    nil_finance_cost(root)

    rows = statement_rows(root, "acme", "FY24", "standalone")
    [line] = [r for r in rows if r["account"] == "finance_cost"]
    assert (line["stated_id"], line["fact_ids"]) == (1, [])
    assert any(
        text.endswith("(stated by the user, #1)") and "Finance" in text
        for text in format_statements("acme", rows, display_settings(root, "acme")).splitlines()
    )
    book = load_workbook(export_workbook(root, "acme"))
    comments = [c.comment.text for ws in book for r in ws.iter_rows() for c in r if c.comment]
    assert [c for c in comments if "stated by the user, #1" in c] != []
    assert company_status(Run("status", {}, root), "acme")["stated_figures"] == 1


def test_a_stated_figure_has_its_own_provenance_and_evidence_section(root: Path) -> None:
    nil_finance_cost(root)
    sql = "SELECT account, value, fact_ids, stated_id FROM current_facts ORDER BY account"
    run = Run("sql", {}, root)

    rows = run_select(root, "acme", sql)
    page = (root / write_evidence(run, "acme", None, sql, to_json_rows(rows))[0]).read_text("utf-8")

    [stated] = [r for r in rows if r["account"] == "finance_cost"]
    assert {k: stated[k] for k in ("source_file", "source_ref", "source_read_by")} == {
        "source_file": "stated by the user",
        "source_ref": "stated #1: The P&L prints no finance cost line; the company has no "
        "borrowings",
        "source_read_by": "user",
    }
    assert [r["source_file"] for r in rows if r["account"] == "revenue"] == ["ar1.xlsx"]
    assert (
        "<h3>stated figure 1: finance_cost FY24 standalone = 0.00 rupees = – INR Mn (stated by "
        "the user, not from a document)</h3><p>Reason given: The P&amp;L prints no finance cost "
        "line" in page
    )
    assert "1 figure(s) were stated by the user, not read from a document" in page


def test_the_report_appendix_lists_a_stated_figure_by_its_id(root: Path) -> None:
    nil_finance_cost(root)

    with connect(company_db_path(root, "acme")) as con:
        facts = fetch_dicts(con, _FACTS_SQL, ["standalone", "standalone"])

    [row] = [f for f in facts if f["account"] == "finance_cost"]
    assert (row["file_name"], row["source_ref"]) == (
        "stated by the user",
        "stated #1, reason under Figures the user stated",
    )
    assert [f["file_name"] for f in facts if f["account"] == "revenue"] == ["ar1.xlsx"]


def misread_revenue(root: Path, value: str = "1100", actor: str = USER) -> dict[str, Any]:
    run = Run("fact correct", {}, root)
    return correct(run, "acme", 1, value, "C100 reads 1,100.00", actor=actor)


def test_a_correction_sets_the_value_keeps_the_printed_cell_and_records_who_and_why(
    root: Path,
) -> None:
    result = misread_revenue(root, actor=CLAUDE)

    assert (result["outcome"], result["correction_id"], result["original_value"]) == (
        "corrected",
        1,
        Decimal("1000.00"),
    )
    assert query(root, "SELECT value FROM current_facts WHERE account = 'revenue'") == [
        (Decimal("1100.00"),)
    ]
    assert query(root, "SELECT raw_value_text, cell FROM raw_values WHERE raw_id = 1") == [
        ("1000.00", "C100")
    ]
    assert query(
        root,
        "SELECT fact_id, original_value, value, reason, actor, on_request FROM fact_corrections",
    ) == [(1, Decimal("1000.00"), Decimal("1100.00"), "C100 reads 1,100.00", "ai:claude", True)]
    assert (root / result["backup_path"]).is_file()
    with connect(audit_db_path(root)) as con:
        assert con.execute(
            "SELECT correction_id, fact_id, original_value, value, actor FROM fact_corrections"
        ).fetchall() == [(1, 1, Decimal("1000.00"), Decimal("1100.00"), "ai:claude")]
    [row] = list_corrected(root, "acme")
    assert (row["source"], row["file_name"]) == ("P&L!C100", "ar1.xlsx")
    assert (
        format_fact_trace(fact_trace(root, "acme", 1))
        .splitlines()[-1]
        .startswith("corrected #1: 1000.00 -> 1100.00 by ai:claude (run ")
    )


def test_a_corrected_figure_is_marked_in_statements_provenance_report_status_and_g1(
    root: Path,
) -> None:
    misread_revenue(root)

    rows = statement_rows(root, "acme", "FY24", "standalone")
    assert any(
        text.endswith("(corrected, correction #1)") and "Revenue" in text
        for text in format_statements("acme", rows, display_settings(root, "acme")).splitlines()
    )
    [revenue] = [
        r
        for r in run_select(root, "acme", "SELECT * FROM current_facts")
        if r["account"] == "revenue"
    ]
    assert revenue["source_ref"] == "P&L!C100, corrected #1 from 1000.00"
    with connect(company_db_path(root, "acme"), write=True) as con:
        facts = fetch_dicts(con, _FACTS_SQL, ["standalone", "standalone"])
        corrected = fetch_dicts(con, _CORRECTED_SQL, ["standalone"])
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24', 100000, 'open', 'r0', now())"
        )
        [g1] = [g for g in evaluate(Context(con, "acme", {"engagement_id": 1,
                "periods": "FY24"}, [])) if g["gate"] == "G1"]  # fmt: skip
    assert [f["source_ref"] for f in facts if f["account"] == "revenue"] == [
        "P&L!C100, corrected #1"
    ]
    assert corrected_table({"corrected": corrected}).splitlines()[2] == (
        "| `revenue` | FY24 | ar1.xlsx P&amp;L!C100 | 1,000.00 | 1,100.00 | C100 reads 1,100.00 |"
    )
    assert (True, "figures corrected after fin misread them: #1 fact 1 revenue FY24 standalone "
            "1000.00 -> 1100.00: C100 reads 1,100.00 (`fin fact list acme`)") in [
        (line["ok"], line["text"]) for line in g1["checklist"]
    ]  # fmt: skip
    assert company_status(Run("status", {}, root), "acme")["corrected_figures"] == 1
    assert query(root, "SELECT actor, on_request FROM fact_corrections") == [("user", False)]


def test_a_figure_under_a_diligence_adjustment_is_not_corrected(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24', 100000, 'open', 'r0', now())"
        )
        con.execute(
            "INSERT INTO adjustments (engagement_id, period, scope, category, account, amount, "
            "revenue_effect, cost_effect, description, source_ref, status, created_run_id, "
            "created_at, status_run_id, status_at) VALUES (1, 'FY24', 'standalone', 'other', "
            "'revenue', 100, 100, 0, 'cut-off', 'P&L!C100', 'proposed', 'r0', now(), 'r0', now())"
        )

    with pytest.raises(FigureCannotBeCorrected, match="under adjustment #1 of the open engagement"):
        misread_revenue(root)

    assert query(root, "SELECT value FROM facts WHERE fact_id = 1") == [(Decimal("1000.00"),)]
    assert query(root, "SELECT count(*) FROM fact_corrections") == [(0,)]
    assert not (root / "backups").exists()


@pytest.mark.parametrize(
    ("fact_id", "value", "reason", "message"),
    [
        (99, "1", "r", "acme has no fact #99"),
        (1, "1000", "r", "fact #1 already has the value 1000"),
        (1, "ten", "r", "value 'ten' is not a number. Nothing corrected"),
        (1, "1", " ", "give the reason"),
    ],
)
def test_a_figure_that_cannot_be_corrected_is_e409_and_changes_nothing(
    root: Path, fact_id: int, value: str, reason: str, message: str
) -> None:
    with pytest.raises(FigureCannotBeCorrected, match=re.escape(message)) as e:
        correct(Run("fact correct", {}, root), "acme", fact_id, value, reason, actor=USER)

    assert str(e.value).startswith("E409: ")
    assert query(root, "SELECT value FROM facts WHERE fact_id = 1") == [(Decimal("1000.00"),)]
    assert not (root / "backups").exists()


@pytest.mark.parametrize(
    ("text", "value"),
    [("12,34,567.50", "1234567.50"), ("1,234,567", "1234567"), ("-1,000", "-1000"), ("0", "0")],
)
def test_a_value_may_group_digits_with_commas(text: str, value: str) -> None:
    assert parse_value(text) == Decimal(value)


def test_the_report_lists_a_stated_figure_conflict_and_keeps_its_reason_on_one_line(
    root: Path,
) -> None:
    run = Run("fact state", {}, root)
    state(run, "acme", "revenue", "FY24", "standalone", "1200", "MIS\npack", actor=USER)

    with connect(company_db_path(root, "acme")) as con:
        findings = fetch_dicts(con, _FINDINGS_SQL, ["standalone"])
    stated = [{"account": "x", "period": "FY24", "value": Decimal(0), "in_use": True,
               "reason": "two\nlines | here"}]  # fmt: skip

    assert [(f["document_status"], f["code"]) for f in findings] == [
        ("stated figure", "stated_figure")
    ]
    assert stated_table({"stated": stated}).splitlines()[2].endswith("| two lines \\| here |")
