"""`fin schema` (fin.query.guide) and the column hint of E504 (SPEC-07 criteria 1 and 2)."""

from pathlib import Path

import pytest

from fin.audit.errors import SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.paths import company_db_path
from fin.query.guide import QUESTION_VIEWS, describe, format_schema
from fin.query.run import run_select
from fin.query.write import write_sql
from fin.statements import add_view
from tests.unit.question_fixtures import copy_root, make_template


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_template(tmp_path_factory)


@pytest.fixture
def root(data_root: Path, template: Path) -> Path:
    return copy_root(template, data_root)


def test_every_column_of_every_question_view_is_in_the_output(root: Path) -> None:
    views = describe(root, "acme")

    with connect(company_db_path(root, "acme")) as con:
        add_view(con, root)
        for view in views:
            described = [(c["name"], c["type"]) for c in view["columns"]]
            actual = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {view['view']}").fetchall()]
            assert described == actual, view["view"]
    assert [v["view"] for v in views] == list(QUESTION_VIEWS)


def test_each_view_has_a_meaning_and_an_example_that_runs(root: Path) -> None:
    for view in describe(root):  # an empty database made from the schema files
        assert view["about"], view["view"]
        assert view["view"] in view["example"]
        run_select(root, "acme", view["example"])


def test_one_view_and_the_text_form(root: Path) -> None:
    [view] = describe(root, "acme", "fact_sources")

    text = format_schema([view])

    assert text.startswith("fact_sources: Each fact with its file, sheet!cell or page")
    assert "source_file VARCHAR, source_ref VARCHAR" in text
    assert "\n  e.g. SELECT c.account" in text


def test_fact_sources_gives_a_facts_source_in_one_join(root: Path) -> None:
    rows = run_select(
        root,
        "acme",
        "SELECT c.account, s.source_file, s.source_ref, s.label, s.source_confirmed, "
        "s.source_read_by, s.document_status FROM current_facts c JOIN fact_sources s "
        "USING (fact_id) WHERE c.account = 'cfo'",
    )

    assert rows[0]["source_ref"] == "FS!B3"
    assert rows[0]["label"] == "Net cash from operating activities"
    assert (rows[0]["source_file"], rows[0]["source_confirmed"], rows[0]["source_read_by"]) == (
        "fy25.xlsx",
        True,
        "fin",
    )
    assert rows[0]["document_status"] == "loaded"


# The failed queries of the client run (obs. 12), rewritten against acme: each either runs, or
# fails naming the column to use.
RUNS = [
    "SELECT c.value, s.source_file FROM current_facts c JOIN fact_sources s USING (fact_id)",
    "SELECT s.source_confirmed FROM current_facts c JOIN fact_sources s USING (fact_id)",
    "SELECT s.label, c.value FROM current_facts c JOIN fact_sources s USING (fact_id)",
    "SELECT source_ref, label FROM fact_sources WHERE account = 'revenue'",
]
FAILS = [  # (sql, what the message names)
    ("SELECT document_id FROM current_facts", "current_facts (fact_id, fact_ids, doc_id,"),
    ("SELECT input_account FROM metric_inputs", "metric_inputs (fiscal_year, period, scope,"),
    ("SELECT label FROM current_facts", "label is a column of fact_sources, metric_definitions"),
    ("SELECT f.source_file FROM facts f", "source_file is a column of fact_sources"),
]  # fmt: skip


@pytest.mark.parametrize("sql", RUNS)
def test_rewritten_queries_run(root: Path, sql: str) -> None:
    assert run_select(root, "acme", sql)


@pytest.mark.parametrize(("sql", "names"), FAILS)
def test_a_missing_column_names_the_columns_to_use(root: Path, sql: str, names: str) -> None:
    with pytest.raises(SqlFailed) as error:
        run_select(root, "acme", sql)

    message = str(error.value)
    assert message.startswith("E504: the query failed: Binder Error:")
    assert names in message
    assert message.endswith(
        "`uv run fin schema acme` lists the views for questions and their columns"
    )


def test_other_errors_get_no_hint(root: Path) -> None:
    with pytest.raises(SqlFailed) as error:
        run_select(root, "acme", "SELECT 1 FROM no_such_table")

    assert "fin schema" not in str(error.value)


def test_a_write_naming_a_missing_column_gets_the_hint(root: Path) -> None:
    run = Run("sql", {}, root)

    with pytest.raises(SqlFailed) as error:
        write_sql(run, "acme", "UPDATE company_profile SET name = 'x' WHERE company_code = 1")

    assert "Columns of the tables in the query: company_profile (code," in str(error.value)
