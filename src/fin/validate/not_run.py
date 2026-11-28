"""Checks that did not run on a document, and what would make them run (docs/product.md).

A check whose inputs are missing does not run (`fin.validate.gates`). Each such check is kept in
the company table `checks_not_run`, replaced every time the document is checked (ingest, a
mapping change, `fin review confirm`), and listed by `fin status` and the UI's Home screen with
what the user can do: confirm a figure, review unmapped lines, or load another document.
"""

from dataclasses import dataclass
from typing import Any

import duckdb

from fin.db.rows import fetch_dicts

Key = tuple[str, str]  # (period, scope)
OTHER_DOCUMENT = "load another document with figures for the same period, scope and account"


@dataclass(frozen=True)
class NotRun:
    check_name: str
    period: str | None  # None for a check over the whole document (cross_document)
    scope: str | None
    reason: str
    needs: str


def needs_figure(missing: list[str], key: Key) -> str:
    """What makes a check of two figures run: "confirm a cash figure for FY24 standalone ..."."""
    figures = " and a ".join(missing)
    return (
        f"confirm a {figures} figure for {key[0]} {key[1]}: map its line (Mappings), or state "
        "it with `fin fact state`"
    )


def needs_lines(lines: int, statement: str, key: Key) -> str:
    return (
        f"confirm an account for the {lines} {statement} line(s) of {key[0]} {key[1]} "
        "(Mappings, `fin review list`)"
    )


def store(con: duckdb.DuckDBPyConnection, run_id: str, doc_id: int, rows: list[NotRun]) -> None:
    """Replace the document's checks that did not run with `rows`."""
    con.execute("DELETE FROM checks_not_run WHERE doc_id = ?", [doc_id])
    for row in rows:
        con.execute(
            "INSERT INTO checks_not_run (doc_id, check_name, period, scope, reason, needs, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [doc_id, row.check_name, row.period, row.scope, row.reason, row.needs, run_id],
        )


def list_not_run(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """The checks that did not run on loaded and held documents, by document."""
    return fetch_dicts(
        con,
        "SELECT n.doc_id, d.file_name, n.check_name, n.period, n.scope, n.reason, n.needs "
        "FROM checks_not_run n JOIN documents d USING (doc_id) "
        "WHERE d.status IN ('loaded', 'held') ORDER BY n.doc_id, n.check_name, n.period, n.scope",
    )


def format_not_run(row: dict[str, Any]) -> str:
    where = " ".join(x for x in (row["period"], row["scope"]) if x)
    where = f" {where}" if where else ""
    return (
        f"{row['file_name']} (doc_id {row['doc_id']}): {row['check_name']}{where} did not run: "
        f"{row['reason']}; to run it, {row['needs']}"
    )
