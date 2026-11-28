"""Reading `discrepancies` for `fin discrepancies`, the export sheet and the Differences screen:
ranked by the size of the difference, every amount converted to the display unit in SQL
(`display_value`). The text and the tree only arrange what SQL returned."""

from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing
from fin.config import Display
from fin.db.conn import connect, no_company_message
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import require_open
from fin.discrepancies.view import add_view
from fin.display import display_settings, number, sql_params
from fin.paths import company_db_path
from fin.statements import add_view as add_statement_view

SHOWN = ("left_amount", "right_amount", "difference", "explained", "unexplained", "threshold")

_ROWS_SQL = """
SELECT d.*, {shown} FROM discrepancies d
WHERE ($level IS NULL OR d.level = $level) AND (NOT $over OR d.over_threshold)
ORDER BY abs(d.difference) DESC NULLS LAST, d.item_id NULLS LAST, d.value_id
""".format(
    shown=", ".join(
        # a foreign-currency side is not in rupees: it is shown as stored, in its currency
        (
            f"CASE WHEN d.currency IS NULL THEN display_value(d.{c}, $per_unit, $decimals) END"
            if c in ("left_amount", "right_amount")
            else f"display_value(d.{c}, $per_unit, $decimals)"
        )
        + f" AS {c}_shown"
        for c in SHOWN
    )
)


def discrepancy_rows(
    root: Path,
    company: str,
    level: str | None = None,
    over: bool = False,
    display: Display | None = None,
) -> dict[str, Any]:
    """{"rows": the differences of the open engagement (at `level`, only those over the
    threshold with `over`), ranked by amount, "display": the display settings}. E001 without a
    company database; E803 without an open engagement."""
    db = company_db_path(root, company)
    if not db.is_file():
        raise DependencyMissing(no_company_message(company))
    display = display or display_settings(root, company)
    with connect(db) as con:
        engagement = require_open(con, company)
        add_statement_view(con, root)
        add_view(con)
        params = {"level": level, "over": over, **sql_params(display)}
        rows = fetch_dicts(con, _ROWS_SQL, params)
    return {"rows": rows, "display": display, "engagement_id": engagement["engagement_id"]}


def amount(row: dict[str, Any], column: str, display: Display) -> str:
    """A shown amount as text; a foreign-currency side in its currency, as stored."""
    if column in ("left_amount", "right_amount") and row["currency"] and row[column] is not None:
        return f"{row['currency']} {row[column]:,.2f}"
    return number(row[f"{column}_shown"], display, blank="n/a")


def query_text(row: dict[str, Any]) -> str:
    """The row's query (#4 draft), whether one can be raised, or nothing."""
    if row["query_id"] is not None:
        return f"query #{row['query_id']} {row['query_status']}"
    return "can be queried" if row["queryable"] else ""


def describe(row: dict[str, Any], display: Display) -> str:
    """One row as a line of text."""
    under = f" (under #{row['parent_id']})" if row["parent_id"] is not None else ""
    what = row["label"] or row["kind"]
    text = (
        f"#{row['row_id']} {row['level']} {row['period']} {row['key']}{under}: {what}, "
        f"{row['left_name']} {amount(row, 'left_amount', display)}, {row['right_name']} "
        f"{amount(row, 'right_amount', display)}, difference {amount(row, 'difference', display)}"
    )
    if row["children"]:
        text += (
            f"; explained by {row['children']} lines below "
            f"{amount(row, 'explained', display)}, unexplained "
            f"{amount(row, 'unexplained', display)}"
        )
    marks = [
        "over the threshold" if row["over_threshold"] else "",
        "no rupee value" if row["difference"] is None else "",
        query_text(row),
    ]
    return text + "".join(f"; {m}" for m in marks if m)


def format_rows(company: str, result: dict[str, Any]) -> str:
    """Plain text: a heading, then each row with its two sources and note."""
    display, rows = result["display"], result["rows"]
    if not rows:
        return (
            f"{company}: no differences in the latest reconciliations (run `fin reconcile "
            f"{company}` first)"
        )
    out = [f"{company}: {len(rows)} differences, ranked by amount ({display.unit_label})"]
    for row in rows:
        out.append(describe(row, display))
        for side in ("left", "right"):
            if row[f"{side}_source"]:
                out.append(f"    {row[f'{side}_name']}: {row[f'{side}_source']}")
        if row["note"]:
            out.append(f"    {row['note']}")
    if any(row["queryable"] and row["item_id"] is not None for row in rows):
        out.append(
            f"Rows marked 'can be queried' go to one query per period: `fin query raise {company} "
            "--items <ids> --question ...` (the user approves it)."
        )
    if any(row["value_id"] is not None for row in rows):
        out.append(
            f"A ref-<n> row goes to a query with `fin reference query {company} <n> --question "
            "...` (the user approves it)."
        )
    return "\n".join(out)


def tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows as nodes ({"row", "children"}), roots first, each level in the rows' order. A
    row whose parent is not among the rows is a root."""
    nodes = {row["row_id"]: {"row": row, "children": []} for row in rows}
    roots = []
    for row in rows:
        parent = nodes.get(str(row["parent_id"])) if row["parent_id"] is not None else None
        (parent["children"] if parent else roots).append(nodes[row["row_id"]])
    return roots
