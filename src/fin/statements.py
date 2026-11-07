"""Standard statements: `fin statements`, and the `standard_statements` view (in `fin sql` too).

For each period and scope, the profit and loss, balance sheet and cash flow in the dictionary's
account tree (`shared/dictionary.duckdb` accounts), whatever labels the filing used:

- every account with a value in `current_facts` (confirmed, from loaded documents);
- in standard order: an account's lines (in `templates/dictionary/schedule_iii.csv` order), then
  the account itself, as printed statements do; memo lines (no parent and no lines) last;
- depth, for indentation: on the balance sheet, the number of shown subtotals above the line; on
  the profit and loss and cash flow, which run down from one subtotal to the next, 0 for a
  subtotal (or a memo line) and 1 for a line;
- for every subtotal, `explained`: what its mapped lines add up to (following parent and sign; a
  subtotal the document does not print is replaced by its own lines), and `coverage`: explained /
  the printed figure, 6 decimal places. When they differ, an "Unmapped / other" line (line_type
  'other', account NULL) carries the difference, just above the subtotal, so nothing is hidden.
  Exceptional and extraordinary items count with either sign, as in the subtotal check
  (`fin.validate.gates`).

Amounts: the view and `--json` are in rupees. The text output and the Excel export show money in
the unit the company's documents use (`document_unit`: lakh, crore, ...; the most recent document's
when they differ), two decimals; earnings per share as printed. Each row from `statement_rows` has
`unit`: that document unit for money lines, `rupees_per_share` for per-share lines.

The account tree lives in the shared dictionary and the facts in the company database, so the view
is made per connection (`add_view`): two temporary tables hold the tree, and the SQL below does the
rest.
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import ReportRenderFailed
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.extract.units import RUPEES_PER_UNIT
from fin.map.dictionary import read_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.validate.gates import EITHER_SIGN

STATEMENTS = ("profit_and_loss", "balance_sheet", "cash_flow")
TITLES = {
    "profit_and_loss": "Profit and loss",
    "balance_sheet": "Balance sheet",
    "cash_flow": "Cash flow",
}
OTHER_LABEL = "Unmapped / other"
UNIT_NAMES = {int(per): name for name, per in RUPEES_PER_UNIT.items() if name != "lac"}
PER_SHARE = "rupees_per_share"

# The unit of the most recent loaded document (latest period end, then latest ingest): the one most
# of its values were printed in.
_DOCUMENT_UNIT_SQL = """
SELECT r.rupees_per_unit
FROM raw_values r
JOIN documents d ON d.doc_id = r.doc_id
WHERE d.status = 'loaded'
GROUP BY d.doc_id, d.period_end, r.rupees_per_unit
ORDER BY d.period_end DESC NULLS LAST, d.doc_id DESC, count(*) DESC, r.rupees_per_unit
LIMIT 1
"""

VIEW_SQL = """
CREATE OR REPLACE TEMP VIEW standard_statements AS
WITH vals AS (
    SELECT
        f.period, f.period_months, f.fiscal_year, f.period_end, f.scope, f.account, f.value,
        f.fact_id
    FROM current_facts f
    JOIN statement_accounts a USING (account)
),
-- What each subtotal's lines add up to: the descendants with a value and no value between.
sums AS (
    SELECT
        v.period,
        v.scope,
        p.ancestor AS account,
        sum(p.sign * v.value) AS explained,
        sum(CASE WHEN p.either_sign THEN -2 * p.sign * v.value ELSE 0 END) AS flipped
    FROM statement_paths p
    JOIN vals v ON v.account = p.descendant
    WHERE NOT EXISTS (
        SELECT 1 FROM vals w
        WHERE w.period = v.period AND w.scope = v.scope AND list_contains(p.between, w.account)
    )
    GROUP BY ALL
),
-- Shown subtotals above each line.
above AS (
    SELECT v.period, v.scope, v.account, count(*) AS n
    FROM vals v
    JOIN statement_paths p ON p.descendant = v.account
    JOIN vals w ON w.period = v.period AND w.scope = v.scope AND w.account = p.ancestor
    GROUP BY ALL
),
lines AS (
    SELECT
        v.period, v.period_months, v.fiscal_year, v.period_end, v.scope, a.statement, a.position,
        a.account, a.label, v.value, v.fact_id,
        CASE
            WHEN NOT a.running THEN coalesce(n.n, 0)
            WHEN s.explained IS NOT NULL OR a.is_root THEN 0
            ELSE 1
        END AS depth,
        CASE WHEN a.running THEN 1 ELSE coalesce(n.n, 0) + 1 END AS lines_depth,
        CASE
            WHEN s.explained <> v.value AND s.explained + s.flipped = v.value
                THEN s.explained + s.flipped
            ELSE s.explained
        END AS explained
    FROM vals v
    JOIN statement_accounts a USING (account)
    LEFT JOIN sums s ON s.account = v.account AND s.period = v.period AND s.scope = v.scope
    LEFT JOIN above n ON n.account = v.account AND n.period = v.period AND n.scope = v.scope
)
SELECT
    period, period_months, fiscal_year, period_end, scope, statement, 2 * position AS line_no,
    depth, 'line' AS line_type, account, label, value, explained,
    div6(CAST(explained * 100 AS HUGEINT), CAST(value * 100 AS HUGEINT)) AS coverage, fact_id
FROM lines
UNION ALL
SELECT
    period, period_months, fiscal_year, period_end, scope, statement, 2 * position - 1, lines_depth,
    'other', NULL, '{other}', value - explained, NULL, NULL, NULL
FROM lines
WHERE explained IS NOT NULL AND explained <> value
"""


def _label(description: str) -> str:
    """A short line label from an account description: up to the first bracket, ';', or comma
    before "as", "or", "in", "including" or "net of"."""
    return re.split(r" \(|; |, (?:as|or|in|including|net of) ", description, maxsplit=1)[0]


def _tree(root: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Rows for the two temporary tables: statement_accounts (statement, position, running,
    is_root, account, label, unit) and statement_paths (ancestor, descendant, sign, between,
    either_sign): one row per account and each account above it."""
    with connect(dictionary_db_path(root)) as con:
        rows = con.execute(
            "SELECT account, statement, parent, sign, description, unit FROM accounts"
        ).fetchall()
    # account -> (statement, parent, sign, description, unit)
    accounts = {row[0]: row[1:] for row in rows}
    seed_order = {row["account"]: n for n, row in enumerate(read_seed()[0])}
    # Accounts added in review come after the seed ones, by name.
    order = sorted(accounts, key=lambda a: (seed_order.get(a, len(seed_order)), a))
    children: dict[str | None, list[str]] = {}
    for name in order:
        children.setdefault(accounts[name][1], []).append(name)

    account_rows: list[tuple[Any, ...]] = []

    def walk(name: str) -> None:
        for child in children.get(name, []):
            walk(child)
        statement, parent, _, description, unit = accounts[name]
        running = statement != "balance_sheet"
        position = len(account_rows) + 1
        account_rows.append(
            (statement, position, running, parent is None, name, _label(description), unit)
        )

    for statement in STATEMENTS:
        roots = [a for a in children.get(None, []) if accounts[a][0] == statement]
        for name in sorted(roots, key=lambda a: a not in children):  # trees, then memo lines
            walk(name)

    path_rows: list[tuple[Any, ...]] = []
    for name in accounts:
        sign, between, current = 1, [], name
        while (parent := accounts[current][1]) is not None:
            sign *= accounts[current][2] or 1
            path_rows.append((parent, name, sign, list(between), name in EITHER_SIGN))
            between.append(parent)
            current = parent
    return account_rows, path_rows


def add_view(con: duckdb.DuckDBPyConnection, root: Path) -> None:
    """Create the temporary `standard_statements` view on a company database connection."""
    account_rows, path_rows = _tree(root)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE statement_accounts (statement VARCHAR, position INTEGER, "
        "running BOOLEAN, is_root BOOLEAN, account VARCHAR, label VARCHAR, unit VARCHAR)"
    )
    con.executemany("INSERT INTO statement_accounts VALUES (?, ?, ?, ?, ?, ?, ?)", account_rows)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE statement_paths (ancestor VARCHAR, descendant VARCHAR, "
        "sign INTEGER, between VARCHAR[], either_sign BOOLEAN)"
    )
    con.executemany("INSERT INTO statement_paths VALUES (?, ?, ?, ?, ?)", path_rows)
    con.execute(VIEW_SQL.format(other=OTHER_LABEL))


def statement_rows(
    root: Path, company: str, period: str | None = None, scope: str | None = None
) -> list[dict[str, Any]]:
    """The standard statements of a company (optionally one period and one scope) as rows of the
    view, in order: scope, fiscal years then other periods (oldest first), statement, line, each
    with its `unit` (see the module docstring). Raises E601 when there is nothing to show."""
    db = company_db_path(root, company)
    if not db.is_file():
        raise ReportRenderFailed(f"no database for {company}; ingest its documents first")
    with connect(db) as con:
        add_view(con, root)
        unit = document_unit(con)
        rows = fetch_dicts(
            con,
            "SELECT s.*, CASE WHEN a.unit = ? THEN a.unit ELSE ? END AS unit "
            "FROM standard_statements s LEFT JOIN statement_accounts a USING (account) "
            "WHERE (? IS NULL OR s.period = ?) AND (? IS NULL OR s.scope = ?) "
            "ORDER BY s.scope, s.period_months <> 12, s.period_end, s.period_months DESC, "
            "array_position(?, s.statement), s.line_no",
            [PER_SHARE, unit, period, period, scope, scope, list(STATEMENTS)],
        )
    if not rows:
        wanted = " ".join(x for x in (scope, period) if x)
        raise ReportRenderFailed(
            f"{company} has no {wanted + ' ' if wanted else ''}figures to show; "
            "see `uv run fin status`"
        )
    return rows


def document_unit(con: duckdb.DuckDBPyConnection) -> str:
    """The unit the company's documents print money in ("lakh", "crore", ...): the most recent
    loaded document's. "rupees" when nothing is loaded."""
    row = con.execute(_DOCUMENT_UNIT_SQL).fetchone()
    return UNIT_NAMES[row[0]] if row else "rupees"


def in_unit(value: Decimal, unit: str) -> Decimal:
    """A rupee value in `unit` (a document unit), two decimals; a per-share value as it is."""
    if unit == PER_SHARE:
        return value
    return (value / RUPEES_PER_UNIT[unit]).quantize(Decimal("0.01"), ROUND_HALF_UP)


def unit_title(unit: str) -> str:
    """How a header names the unit: "₹ in lakh", or "₹" for rupees."""
    return "₹" if unit == "rupees" else f"₹ in {unit}"


def latest_annual_period(rows: list[dict[str, Any]]) -> str:
    """The latest fiscal year in `rows`, else the latest period."""
    annual = [r for r in rows if r["period_months"] == 12] or rows
    return max(annual, key=lambda r: (r["period_end"], r["period_months"]))["period"]


def amount(value: Decimal, symbol: str = "") -> str:
    """An amount with thousands separators and two decimals, the sign before `symbol`: "-₹1,234.50".
    The statements and the report format money with it."""
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def format_statements(company: str, rows: list[dict[str, Any]]) -> str:
    """Plain text: one block per scope, period and statement; amounts in the document unit (named
    in each header), per-share lines as printed. A subtotal its lines do not fully explain says
    how much they explain."""
    out: list[str] = []
    block = None
    money_unit = next((r["unit"] for r in rows if r["unit"] != PER_SHARE), "rupees")
    for row in rows:
        if (row["scope"], row["period"], row["statement"]) != block:
            block = (row["scope"], row["period"], row["statement"])
            if out:
                out.append("")
            out.append(
                f"{company} {row['scope']} {row['period']}: "
                f"{TITLES[row['statement']].lower()} ({unit_title(money_unit)})"
            )
        label = "  " * row["depth"] + row["label"]
        line = f"{label:<60} {amount(in_unit(row['value'], row['unit'])):>20}"
        if row["coverage"] is not None and row["coverage"] != 1:
            explained = amount(in_unit(row["explained"], row["unit"]))
            line += f"  (lines explain {explained}, {row['coverage']:.2%})"
        out.append(line.rstrip())
    return "\n".join(out)
