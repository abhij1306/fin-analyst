"""Audit fixes in storage: `fin sql` cannot reach mask_values through a comment, `fin delete`
removes the company's data room folder, and stored label keys are read as they are."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fin.audit.errors import MaskedTableRead
from fin.db.conn import connect
from fin.delete import company_paths
from fin.map.dictionary import load_label_map
from fin.paths import company_db_path, dataroom_dir, dictionary_db_path
from fin.query.run import refuse_masked
from tests.engagement_root import engagement_root


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM query_table/**/('mask_values')",
        "SELECT * FROM query_table /* x */ ('mask' || '_values')",
        "SELECT * FROM \"query\"/**/('SELECT 1')",
        "SELECT * FROM query -- note\n('SELECT 1')",
        "SELECT * FROM read_duckdb/**/('acme.duckdb')",
        'SELECT * FROM "mask_values"',
        "INSERT INTO adjustments SELECT * FROM query_table/**/('mask_values')",
    ],
)
def test_a_comment_before_the_bracket_does_not_reach_the_masked_names(sql: str) -> None:
    with pytest.raises(MaskedTableRead) as refused:
        refuse_masked(sql)
    assert refused.value.code == "E810"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT query_id, question FROM accountant_queries",
        "SELECT * FROM facts -- the query of the day",
        "SELECT 'mask' AS label",
    ],
)
def test_other_sql_still_runs(sql: str) -> None:
    refuse_masked(sql)


def test_delete_takes_the_companys_data_room_folder_too(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    room = dataroom_dir(root, "acme")
    room.mkdir(parents=True)
    (room / "index.json").write_text("{}", encoding="utf-8")

    paths = company_paths(root, "acme")

    assert room in paths
    assert company_db_path(root, "acme") in paths


def test_a_stored_label_key_is_used_as_it_is_stored(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    with connect(dictionary_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO label_map (label, statement, section, account, scope, company, source, "
            "created_at, run_id) VALUES ('III. Total Income', 'profit_and_loss', '', "
            "'total_income', 'company', 'acme', 'test', ?, 'r0')",
            [datetime.now(UTC).replace(tzinfo=None)],
        )

    keys = {key for key in load_label_map(root, "acme") if key[0]}

    assert keys == {(True, "profit_and_loss", "", "III. Total Income")}
