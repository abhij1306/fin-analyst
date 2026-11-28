"""`fin sql --write` refuses, before any statement runs, statements that write to a diligence table
(E806) or that would end its transaction or reach past the company database (E504)."""

from pathlib import Path

import pytest

from fin.audit.errors import DiligenceTableWrite, FigureTableWrite, MaskedTableRead, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.query.run import run_select
from fin.query.write import check_write_sql, write_sql


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        ("INSERT INTO gate_approvals SELECT * FROM gate_approvals", "gate_approvals"),
        ("UPDATE adjustments SET status = 'accepted'", "adjustments"),
        ('DELETE FROM main."Accountant_Queries"', "accountant_queries"),
        ("UPDATE facts SET value = 1; UPDATE engagement SET status = 'closed'", "engagement"),
        ("DROP TABLE gate_approvals", "gate_approvals"),
        ("UPDATE facts SET value = 1 WHERE doc_id IN (SELECT 1 FROM adjustments)", "adjustments"),
        ("DELETE FROM trial_balance", "trial_balance"),
        ("UPDATE ledger_entries SET debit = 0", "ledger_entries"),
        ("DELETE FROM bank_lines", "bank_lines"),
        ("UPDATE bank_statements SET ties = true", "bank_statements"),
        ("DELETE FROM source_files", "source_files"),
        ("UPDATE register_lines SET amount_inr = 0", "register_lines"),
        ("DELETE FROM passthrough_customers", "passthrough_customers"),
        ("UPDATE walkthroughs SET result = 'pass'", "walkthroughs"),
        ("DELETE FROM samples", "samples"),
        ("DELETE FROM sample_lines", "sample_lines"),
        ("UPDATE contracts SET value = 0", "contracts"),
        ("UPDATE netdebt_classes SET status = 'confirmed'", "netdebt_classes"),
        ("DELETE FROM netdebt_items", "netdebt_items"),
        ("UPDATE fdd_findings SET status = 'final'", "fdd_findings"),
    ],
)
def test_writes_naming_a_diligence_table_are_refused(tmp_path: Path, sql: str, found: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(DiligenceTableWrite) as e:
        check_write_sql(con, sql)

    assert str(e.value) == (
        f"E806: `fin sql --write` does not change the diligence tables (found: {found}); use "
        "`fin engagement`, `fin query`, `fin adjust`, `fin gate` or `fin ingest --kind`. Nothing "
        "was run"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE documents SET status = 'loaded' WHERE engagement_id = 2",
        "SELECT * FROM gate_approvals; UPDATE documents SET status = 'held'",
        "DELETE FROM findings WHERE label = 'adjusted_ebitda'",
        "UPDATE findings SET message = 'x' WHERE label = 'current_facts'",
    ],
)
def test_other_writes_pass(tmp_path: Path, sql: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con:
        assert check_write_sql(con, sql) is None


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        ("INSERT INTO facts SELECT * FROM facts", "facts"),
        ("UPDATE main.Facts SET value = 0 WHERE account = 'finance_cost'", "facts"),
        ("DELETE FROM raw_values", "raw_values"),
        ("UPDATE stated_figures SET status = 'withdrawn'", "stated_figures"),
        ("DELETE FROM fact_corrections", "fact_corrections"),
        ("UPDATE documents SET status = 'held'; DELETE FROM facts", "facts"),
        ("INSERT INTO raw_values SELECT * FROM raw_values "
         "WHERE raw_id IN (SELECT raw_id FROM facts)", "facts, raw_values"),
    ],
)  # fmt: skip
def test_writes_naming_a_figure_table_are_e505(tmp_path: Path, sql: str, found: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(FigureTableWrite) as e:
        check_write_sql(con, sql)

    assert str(e.value) == (
        f"E505: `fin sql --write` does not change the figures (found: {found}). Load a document "
        "(`fin ingest`, `--reading` for pages fin could not read), map its lines (`fin review`), "
        "state a figure the documents do not give (`fin fact state`) or correct one fin misread "
        "(`fin fact correct`). Nothing was run"
    )


def test_a_write_into_facts_is_refused_before_the_backup_and_logged(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    run = Run("sql", {}, tmp_path)
    sql = "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, value, "
    sql += "confirmed, run_id, read_by) VALUES (1, 1, 'finance_cost', 'FY24', DATE '2024-03-31', "
    sql += "'standalone', 0, true, 'invented', 'claude')"

    with pytest.raises(FigureTableWrite):
        write_sql(run, "acme", sql)

    with connect(path) as con:
        assert con.execute("SELECT count(*) FROM facts").fetchone() == (0,)
    assert not (tmp_path / "backups").exists()
    assert [(table, row["sql"], row["mode"]) for table, row in run._records] == [
        ("queries", sql, "write")
    ]


@pytest.mark.parametrize(
    "sql", ["SELECT * FROM mask_values", "UPDATE mask_values SET real_value = 'x'"]
)
def test_sql_naming_mask_values_is_e810(tmp_path: Path, sql: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(MaskedTableRead):
        check_write_sql(con, sql)


def test_sql_that_does_not_parse_is_e504(tmp_path: Path) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(SqlFailed) as e:
        check_write_sql(con, "UPDATE facts SET")

    assert str(e.value).startswith("E504: cannot parse the SQL: Parser Error")


@pytest.mark.parametrize(
    ("sql", "refused"),
    [
        ("UPDATE facts SET value = 1; COMMIT", "BEGIN, COMMIT and ROLLBACK"),
        ("ROLLBACK", "BEGIN, COMMIT and ROLLBACK"),
        ("ATTACH 'other.duckdb' AS other", "ATTACH"),
        ("DETACH other", "DETACH"),
        ("LOAD httpfs", "LOAD and INSTALL"),
    ],
)
def test_statements_leaving_the_transaction_are_refused(
    tmp_path: Path, sql: str, refused: str
) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(SqlFailed) as e:
        check_write_sql(con, sql)

    assert str(e.value) == (
        "E504: `fin sql --write` runs the SQL in its own transaction on the company database; "
        f"{refused} are not allowed. Nothing was run"
    )


def test_a_commit_in_the_batch_runs_nothing(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    run = Run("sql", {}, tmp_path)

    with pytest.raises(SqlFailed):
        write_sql(run, "acme", "CREATE TABLE early (x INT); COMMIT; SELECT * FROM missing")

    with connect(path) as con:
        assert con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'early'"
        ).fetchone() == (0,)
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize(
    "sql",
    [
        "DROP VIEW current_facts",
        "CREATE TABLE early (x INT)",
        "ALTER TABLE facts ADD COLUMN x INT",
        "PRAGMA threads=1",
        "SET threads=1",
        "VACUUM",
    ],
)
def test_schema_changes_are_refused_and_the_database_is_unchanged(tmp_path: Path, sql: str) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    run = Run("sql", {}, tmp_path)

    with pytest.raises(SqlFailed, match="schema changes are not allowed"):
        write_sql(run, "acme", sql)

    with connect(path) as con:
        assert con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'current_facts'"
        ).fetchone() == (1,)
    assert not (tmp_path / "backups").exists()


def test_the_written_sql_cannot_read_files(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")

    with pytest.raises(SqlFailed):
        write_sql(
            Run("sql", {}, tmp_path),
            "acme",
            "UPDATE documents SET file_name = "
            f"(SELECT count(*) FROM read_csv('{secret.as_posix()}'))",
        )


@pytest.mark.parametrize("function", ["read_csv", "read_text", "read_blob"])
def test_a_select_cannot_read_files_but_a_plain_one_works(tmp_path: Path, function: str) -> None:
    ensure_database(company_db_path(tmp_path, "acme"), "company")
    ensure_database(dictionary_db_path(tmp_path), "dictionary")
    load_seed(tmp_path, Run("init", {}, tmp_path))
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")

    with pytest.raises(SqlFailed):
        run_select(tmp_path, "acme", f"SELECT * FROM {function}('{secret.as_posix()}')")

    assert run_select(tmp_path, "acme", "SELECT count(*) AS n FROM facts") == [{"n": 0}]
