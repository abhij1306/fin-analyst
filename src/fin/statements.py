"""Standard statements: `fin statements`, and the `standard_statements` view (in `fin sql` too).

For each period and scope, the profit and loss, balance sheet and cash flow in the dictionary's
account tree (`shared/dictionary.duckdb` accounts), whatever labels the filing used:

- every account with a value in `current_facts` (confirmed, from loaded documents, or a figure the
  user stated where no document gives it: `stated_id`, marked "stated" in the text; a figure
  corrected with `fin fact correct` has `corrected_id`, marked "corrected");
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
- lineage: `fact_ids`, every fact behind the value (a document that prints an account on two lines
  has two, and the value is their sum); `fact_id` is the first of them.

Amounts: the view is in rupees. `statement_rows` adds, in SQL, each value in the display unit
(`shown`, `shown_explained`: config.toml [display], `fin.display`), the source of each figure
(file and sheet!cell or page, from `fact_sources`) and its marks (an unconfirmed mapping, a figure
read by the assistant), the share of revenue and the growth on the same period a year before (as
percentages, one decimal), and `rounding`: a subtotal whose shown lines do not add up to it only
because each figure is rounded on its own. Each row has `unit`: `rupees`, or `rupees_per_share`
for per-share lines (shown as printed, two decimals).

The account tree lives in the shared dictionary and the facts in the company database, so the view
is made per connection (`add_view`): two temporary tables hold the tree, and the SQL below does the
rest.
"""

import re
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DependencyMissing, ReportRenderFailed
from fin.config import Display
from fin.db.conn import connect, no_company_message
from fin.db.rows import fetch_dicts
from fin.display import PER_SHARE, add_macro, display_settings, number, sql_params
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
ROUNDING_NOTE = (
    "Each figure is rounded on its own: a total marked so is the rounded total, and its rounded "
    "lines do not add up to it exactly."
)

VIEW_SQL = """
-- about: The P&L, balance sheet and cash flow in standard order, with Unmapped / other lines.
-- example: SELECT statement, label, value FROM standard_statements WHERE period = 'FY25'
CREATE OR REPLACE TEMP VIEW standard_statements AS
WITH vals AS (
    SELECT
        f.period, f.period_months, f.fiscal_year, f.period_end, f.scope, f.account, f.value,
        f.fact_id, f.fact_ids, f.stated_id,
        (
            SELECT max(c.correction_id) FROM corrected_now c
            WHERE list_contains(f.fact_ids, c.fact_id)
        ) AS corrected_id
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
        a.account, a.label, v.value, v.fact_id, v.fact_ids, v.stated_id, v.corrected_id,
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
    div6(CAST(explained * 100 AS HUGEINT), CAST(value * 100 AS HUGEINT)) AS coverage, fact_id,
    fact_ids, stated_id, corrected_id
FROM lines
UNION ALL
SELECT
    period, period_months, fiscal_year, period_end, scope, statement, 2 * position - 1, lines_depth,
    'other', NULL, '{other}', value - explained, NULL, NULL, NULL, NULL, NULL, NULL
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
    add_macro(con)


# The rows of `statement_rows`: the view with the display values ($per_unit, $decimals; per-share
# lines two decimals, unscaled), sources and marks, share of revenue and growth, and the rounding
# flag. Growth and share use the unfiltered rows, so a period's year before need not be asked for.
_ROWS_SQL = """
WITH base AS (
    SELECT
        s.*, CASE WHEN a.unit = 'rupees_per_share' THEN a.unit ELSE 'rupees' END AS unit,
        s.line_type = 'line' AND s.explained IS NOT NULL AS subtotal
    FROM standard_statements s
    LEFT JOIN statement_accounts a USING (account)
    WHERE $scope IS NULL OR s.scope = $scope
),
shown AS (
    SELECT
        b.*,
        display_value(b.value, CASE WHEN b.unit = 'rupees' THEN $per_unit ELSE 1 END,
                      CASE WHEN b.unit = 'rupees' THEN $decimals ELSE 2 END) AS shown,
        display_value(b.explained, $per_unit, $decimals) AS shown_explained
    FROM base b
),
sources AS (
    SELECT
        u.scope, u.period, u.line_no,
        string_agg(fs.source_file || ' ' || fs.source_ref, '; ' ORDER BY fs.fact_id) AS source,
        bool_or(NOT fs.source_confirmed) AS unconfirmed,
        bool_or(fs.source_read_by = 'claude') AS read_by_ai
    FROM (SELECT scope, period, line_no, unnest(fact_ids) AS fact_id FROM base) u
    JOIN fact_sources fs USING (fact_id)
    GROUP BY ALL
),
rounding AS (
    SELECT t.scope, t.period, t.line_no
    FROM shown t
    JOIN statement_paths p ON p.ancestor = t.account
    JOIN shown c ON c.scope = t.scope AND c.period = t.period AND c.account = p.descendant
    WHERE t.explained = t.value AND NOT EXISTS (
        SELECT 1 FROM shown w
        WHERE w.scope = c.scope AND w.period = c.period AND list_contains(p.between, w.account)
    )
    GROUP BY t.scope, t.period, t.line_no, t.shown
    HAVING sum(p.sign * c.shown) <> t.shown
        AND sum(CASE WHEN p.either_sign THEN -p.sign ELSE p.sign END * c.shown) <> t.shown
),
ratios AS (
    SELECT
        t.scope, t.period, t.line_no,
        CASE WHEN t.statement = 'profit_and_loss' AND t.unit = 'rupees' THEN round(100 * div6(
            CAST(t.value * 100 AS HUGEINT), CAST(r.value * 100 AS HUGEINT)), 1) END AS of_revenue,
        CASE WHEN t.unit = 'rupees' THEN round(100 * growth6(t.value, y.value), 1) END AS yoy
    FROM shown t
    LEFT JOIN shown r ON r.scope = t.scope AND r.period = t.period AND r.account = 'revenue'
    LEFT JOIN shown y ON y.scope = t.scope AND y.line_no = t.line_no
        AND y.period = prior_period(t.period) AND y.period_months = t.period_months
)
SELECT
    s.*, src.source, coalesce(src.unconfirmed, false) AS unconfirmed,
    coalesce(src.read_by_ai, false) AS read_by_ai, rd.line_no IS NOT NULL AS rounding,
    ra.of_revenue, ra.yoy
FROM shown s
LEFT JOIN sources src USING (scope, period, line_no)
LEFT JOIN rounding rd USING (scope, period, line_no)
LEFT JOIN ratios ra USING (scope, period, line_no)
WHERE $period IS NULL OR s.period = $period
ORDER BY s.scope, s.period_months <> 12, s.period_end, s.period_months DESC,
    array_position($statements, s.statement), s.line_no
"""


def statement_rows(
    root: Path,
    company: str,
    period: str | None = None,
    scope: str | None = None,
    display: Display | None = None,
) -> list[dict[str, Any]]:
    """The standard statements of a company (optionally one period and one scope) as rows of the
    view, in order: scope, fiscal years then other periods (oldest first), statement, line, each
    with the columns of _ROWS_SQL (see the module docstring), shown in `display` (default: the
    company's display settings). Raises E601 when there is nothing to show."""
    db = company_db_path(root, company)
    if not db.is_file():
        raise DependencyMissing(no_company_message(company))
    display = display or display_settings(root, company)
    with connect(db) as con:
        add_view(con, root)
        params = {"period": period, "scope": scope, "statements": list(STATEMENTS)}
        rows = fetch_dicts(con, _ROWS_SQL, {**params, **sql_params(display)})
    if not rows:
        wanted = " ".join(x for x in (scope, period) if x)
        raise ReportRenderFailed(
            f"{company} has no {wanted + ' ' if wanted else ''}figures to show; "
            "see `uv run fin status`"
        )
    return rows


def marks(row: dict[str, Any]) -> list[str]:
    """What a reader must know about a figure: stated, corrected, unconfirmed, read by AI."""
    out = []
    if row.get("stated_id") is not None:
        out.append(f"stated by the user, #{row['stated_id']}")
    if row.get("corrected_id") is not None:
        out.append(f"corrected, correction #{row['corrected_id']}")
    if row.get("unconfirmed"):
        out.append("unconfirmed mapping")
    if row.get("read_by_ai"):
        out.append("read by AI")
    return out


def text_marks(found: list[str]) -> list[str]:
    """Of a figure's `marks`, those the text outputs print: all but "unconfirmed mapping", which
    the screen and the export comment show (docs/decisions/spec-06.md)."""
    return [mark for mark in found if "mapping" not in mark]


def latest_annual_period(rows: list[dict[str, Any]]) -> str:
    """The latest fiscal year in `rows`, else the latest period."""
    annual = [r for r in rows if r["period_months"] == 12] or rows
    return max(annual, key=lambda r: (r["period_end"], r["period_months"]))["period"]


def format_statements(company: str, rows: list[dict[str, Any]], display: Display) -> str:
    """Plain text: one block per scope, period and statement; amounts as SQL converted them to
    `display` (its caption in each header), per-share lines as printed. A subtotal its lines do
    not fully explain says how much they explain; a subtotal its rounded lines do not add up to is
    marked *, with ROUNDING_NOTE under the block. A figure's marks follow it (`text_marks`)."""
    out: list[str] = []
    block = None
    rounded = False
    for row in rows:
        if (row["scope"], row["period"], row["statement"]) != block:
            if rounded:
                out.append(f"* {ROUNDING_NOTE}")
            block, rounded = (row["scope"], row["period"], row["statement"]), False
            if out:
                out.append("")
            out.append(
                f"{company} {row['scope']} {row['period']}: "
                f"{TITLES[row['statement']].lower()} ({display.unit_label})"
            )
        label = "  " * row["depth"] + row["label"]
        value = number(row["shown"], display, row["unit"] == PER_SHARE)
        line = f"{label:<60} {value:>20}"
        if row["rounding"]:
            line, rounded = line + " *", True
        if row["coverage"] is not None and row["coverage"] != 1:
            explained = number(row["shown_explained"], display)
            line += f"  (lines explain {explained}, {row['coverage']:.2%})"
        line += "".join(f"  ({mark})" for mark in text_marks(marks(row)))
        out.append(line.rstrip())
    if rounded:
        out.append(f"* {ROUNDING_NOTE}")
    return "\n".join(out)
