"""A failed validation check as a finding row (`fin.findings`): this document's figure (fin_*),
what it was compared with (other_*), each with its page or cell, and the mappings behind it.

A finding whose figures come from a line mapped in review (`fin review confirm`, `fin map set`)
rather than from the seed dictionary names that mapping in `mappings` (JSON: mapping_id,
statement, section, label as written, account, source), so the user can tell a company's error
from a mapping's and change the mapping (`fin map set`, the UI's "change the mapping") instead of
saving the finding as is.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from fin.db.conn import connect
from fin.paths import dictionary_db_path

if TYPE_CHECKING:
    from fin.validate.gates import Result

# The same order as fin.validate.gates.CODES.
CODES = {
    "balance_sheet": "E401",
    "subtotal": "E402",
    "cash_flow": "E403",
    "unit_sanity": "E405",
    "cross_document": "E404",
}


def reviewed_mappings(root: Path) -> dict[int, tuple[str, str]]:
    """{mapping_id: (section, source)} of every label mapping not from the seed dictionary."""
    with connect(dictionary_db_path(root)) as con:
        rows = con.execute(
            "SELECT mapping_id, section, source FROM label_map WHERE source <> 'seed'"
        ).fetchall()
    return {mapping_id: (section, source) for mapping_id, section, source in rows}


def _printed(
    con: duckdb.DuckDBPyConnection, doc_id: int, period: str, scope: str, account: str
) -> tuple[str | None, str | None, str | None]:
    """(statement, value as printed, page or sheet!cell) of the document's lines behind an account
    in a period and scope; lines summed into one figure are joined with ' + ' and ', '."""
    rows = con.execute(
        "SELECT r.statement, r.raw_value_text, "
        "coalesce(r.sheet || '!' || r.cell, 'page ' || CAST(r.page AS VARCHAR)) "
        "FROM document_facts x JOIN facts f ON list_contains(x.fact_ids, f.fact_id) "
        "JOIN raw_values r ON r.raw_id = f.raw_id "
        "WHERE x.doc_id = ? AND x.period = ? AND x.scope = ? AND x.account = ? ORDER BY f.raw_id",
        [doc_id, period, scope, account],
    ).fetchall()
    if not rows:
        return None, None, None
    return rows[0][0], " + ".join(r[1] for r in rows), ", ".join(r[2] for r in rows)


def _sum_of_lines(con: duckdb.DuckDBPyConnection, doc_id: int, r: "Result") -> str:
    """The lines a figure was compared with the sum of (a subtotal's lines; balance-sheet cash and
    the overdrafts), each with its signed value in rupees and where the document prints it, so
    the computed side can be rebuilt: "sum of its lines: revenue +1,000.00 at Sheet!B5; ..."."""
    parts = [
        f"{account} {value:+,} at {_printed(con, doc_id, r.period, r.scope, account)[2]}"
        for account, value in r.lines
    ]
    return "sum of its lines: " + "; ".join(parts)


def _mappings(
    con: duckdb.DuckDBPyConnection, doc_id: int, r: "Result", reviewed: dict[int, tuple[str, str]]
) -> str | None:
    """The reviewed mappings of this document's lines in the statements of the check's figures, for
    its period and scope, as JSON; None when the seed dictionary maps every one. The whole
    statement, not only the compared lines: a line mapped to the wrong account has left the
    subtotal it belongs to."""
    rows = con.execute(
        "SELECT DISTINCT f.mapping_id, r.statement, r.raw_label, f.account "
        "FROM facts f JOIN raw_values r ON r.raw_id = f.raw_id "
        "WHERE f.doc_id = ? AND f.period = ? AND f.scope = ? AND r.statement IN ("
        "SELECT r2.statement FROM document_facts x "
        "JOIN facts f2 ON list_contains(x.fact_ids, f2.fact_id) "
        "JOIN raw_values r2 ON r2.raw_id = f2.raw_id "
        "WHERE x.doc_id = ? AND x.period = ? AND x.scope = ? AND list_contains(?, x.account)) "
        "ORDER BY f.mapping_id",
        [doc_id, r.period, r.scope, doc_id, r.period, r.scope, list(r.accounts)],
    ).fetchall()
    named = [
        {
            "mapping_id": mapping_id,
            "statement": statement,
            "section": reviewed[mapping_id][0],
            "label": label,
            "account": account,
            "source": reviewed[mapping_id][1],
        }
        for mapping_id, statement, label, account in rows
        if mapping_id in reviewed
    ]
    return json.dumps(named) if named else None


def finding(
    con: duckdb.DuckDBPyConnection,
    doc_id: int,
    file_name: str,
    r: "Result",
    reviewed: dict[int, tuple[str, str]],
) -> dict[str, Any]:
    """A failed check as a finding (columns of the `findings` tables). fin_* is the figure in this
    document; other_* what it was compared with: the other side of the balance sheet, the
    balance-sheet cash (less overdrafts), the sum of a subtotal's lines, or another document's
    figure."""
    if r.check_name in ("balance_sheet", "cash_flow"):
        other_account, account = r.accounts
        other_doc, other_file = doc_id, None
    else:
        account, other_account = r.accounts[0], r.accounts[0]
        other_doc, other_file = r.other_doc_id, r.other_file
    statement, fin_printed, fin_source = _printed(con, doc_id, r.period, r.scope, account)
    other_printed, other_source = None, None
    if r.lines:  # a subtotal's lines, or balance-sheet cash less overdrafts
        other_source = _sum_of_lines(con, doc_id, r)
    elif other_doc is not None:
        _, other_printed, other_source = _printed(con, other_doc, r.period, r.scope, other_account)
    return {
        "doc_id": doc_id,
        "file_name": file_name,
        "check_name": r.check_name,
        "code": CODES[r.check_name],
        "finding": "differ",
        "statement": statement,
        "scope": r.scope,
        "period": r.period,
        "label": account if account == other_account else f"{account} vs {other_account}",
        "fin_value": r.actual,
        "other_value": r.expected,
        "difference": r.expected - r.actual,
        "fin_printed": fin_printed,
        "other_printed": other_printed,
        "fin_source": fin_source,
        "other_source": other_source,
        "other_file": other_file,
        "message": r.message,
        "mappings": _mappings(con, doc_id, r, reviewed),
    }
