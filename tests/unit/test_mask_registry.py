"""The masking registry (fin.diligence.mask_columns) lists every text column of a company
database, so a new column that may hold a name is decided on: masked or as filed."""

from pathlib import Path

import pytest

from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence.mask_columns import AS_FILED, MASK_STORE, MASKED, category

TEXT_COLUMNS = """
SELECT c.table_name, c.column_name
FROM information_schema.columns c
JOIN information_schema.tables t USING (table_schema, table_name)
WHERE t.table_type = 'BASE TABLE' AND c.data_type = 'VARCHAR'
ORDER BY 1, 2
"""


@pytest.fixture(scope="module")
def text_columns(tmp_path_factory: pytest.TempPathFactory) -> set[tuple[str, str]]:
    path: Path = tmp_path_factory.mktemp("mask") / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path) as con:
        return {(table, column) for table, column in con.execute(TEXT_COLUMNS).fetchall()}


def listed() -> list[tuple[str, str]]:
    return [
        (table, column)
        for registry in (MASKED, MASK_STORE, AS_FILED)
        for table, columns in registry.items()
        for column in columns
    ]


def test_every_text_column_is_in_the_registry(text_columns: set[tuple[str, str]]) -> None:
    missing = sorted(c for c in text_columns if category(*c) is None)
    assert missing == [], f"decide masked or as filed in fin.diligence.mask_columns: {missing}"


def test_every_registry_entry_is_a_text_column(text_columns: set[tuple[str, str]]) -> None:
    assert sorted(set(listed()) - text_columns) == []


def test_no_column_is_in_two_categories() -> None:
    entries = listed()
    assert len(entries) == len(set(entries))


def test_a_new_text_column_fails_the_check(tmp_path: Path) -> None:
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute("ALTER TABLE customers ADD COLUMN contact_person VARCHAR")
        found = {(t, c) for t, c in con.execute(TEXT_COLUMNS).fetchall()}
    assert [c for c in found if category(*c) is None] == [("customers", "contact_person")]
