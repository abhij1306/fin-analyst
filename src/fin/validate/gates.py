"""Validation gates (PLAN.md Phase 6). `fin.ingest` runs `validate_document` on every staged
document, inside its transaction, before COMMIT.

Checks, for each period and scope in the document (tolerances from config.toml). A period is a
fiscal year, quarter, half year or nine months ("FY25", "FY26-Q1", "FY26-H1"), so only like periods
are ever compared. An account a document prints on several lines counts once, as their sum
(`document_facts` in the company database).

- balance_sheet (E401): total assets = total equity and liabilities (a half-year balance sheet
  too).
- subtotal (E402): every balance-sheet and P&L subtotal the document reports = the sum of its lines,
  following `dictionary.accounts` (parent, sign). A subtotal the document does not show is
  replaced by the sum of its own lines.
- cash_flow (E403): cash flow closing cash = balance-sheet cash, or the cash flow statement itself
  reconciles the two: one of its lines naming cash shows the balance-sheet figure (Ind AS 7 lets
  closing cash net off bank overdrafts, shown as "Cash and cash equivalents as above / Bank
  overdraft / Total").
  Runs only when the document has both, so a results statement without a cash flow skips it.
- cross_document (E404): every figure equals the same company / period / scope / account in
  each other loaded document (for example a comparative column vs that year's own document, or a
  half-year filing's Q1 column vs the Q1 filing).
- unit_sanity (E405): a figure that differs from another document by exactly a unit factor (the
  ratio of two units fin reads, such as 100 for crores vs lakhs, or 1000 for lakhs vs thousand
  rupees) is a unit mistake, reported as E405 instead of E404. A single document with a wrong unit
  and no overlap with another document cannot be caught this way.

Only facts with a confirmed account take part. A check whose inputs are missing (no total line, or
unmapped lines in the statement) does not run and says so in a warning event. Unmapped lines that
cannot be part of a subtotal do not stop the subtotal check: a nil line, a per-share line (EPS,
face value), and on the P&L the split of profit between owners and non-controlling interests.

Sign of exceptional and extraordinary items: they are stored as printed. Schedule III prints a
charge as positive and subtracts it ("V - VI"), but some companies print a gain as positive and add
it ("iii + iv"). The subtotal check tries the dictionary sign (-1) first, then +1, and the message
says when the document adds them.

Every check that runs is appended to the audit table `validation_results`, pass or fail. If any
fails, `validate_document` raises the first failure's error (in the order above, unit_sanity before
cross_document), so the document is rolled back and sent to review/ with expected vs actual and the
lines each failed check used (label, section, page or cell, account).
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

import duckdb

from fin.audit.errors import (
    BalanceSheetMismatch,
    CashFlowMismatch,
    CrossDocumentMismatch,
    DocumentFailure,
    SubtotalMismatch,
    UnitSanityFailed,
)
from fin.config import load_config
from fin.db.conn import connect
from fin.extract.units import RUPEES_PER_UNIT
from fin.map.normalize import normalize_label
from fin.paths import dictionary_db_path

if TYPE_CHECKING:
    from fin.audit.logger import Run

ERRORS: dict[str, type[DocumentFailure]] = {
    "balance_sheet": BalanceSheetMismatch,
    "subtotal": SubtotalMismatch,
    "cash_flow": CashFlowMismatch,
    "unit_sanity": UnitSanityFailed,
    "cross_document": CrossDocumentMismatch,
}
# Which config.toml tolerance each check uses.
TOLERANCE = {
    "balance_sheet": "balance_sheet",
    "subtotal": "subtotal",
    "cash_flow": "cash_flow",
    "unit_sanity": "cross_document",
    "cross_document": "cross_document",
}
UNIT_FACTORS = sorted(
    {a / b for a in RUPEES_PER_UNIT.values() for b in RUPEES_PER_UNIT.values() if a > b}
)
SUBTOTAL_STATEMENTS = ("balance_sheet", "profit_and_loss")
EITHER_SIGN = frozenset({"exceptional_items", "extraordinary_items"})
# An unmapped P&L line whose label or heading matches this is never part of a subtotal.
OUTSIDE_SUBTOTALS = re.compile(
    r"\bper (?:equity )?share\b|\bbasic\b|\bdiluted\b|\bface value\b|\bnominal value\b"
    r"|\bnumber of (?:equity )?shares\b|\battributable to\b"
)

Key = tuple[str, str]  # (period, scope)


@dataclass(frozen=True)
class Result:
    check_name: str
    period: str  # "FY25", "FY26-Q1", ...; stored in the audit column fiscal_year
    scope: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal
    passed: bool
    message: str
    accounts: tuple[str, ...] = ()  # the accounts whose lines the check used


@dataclass(frozen=True)
class Account:
    statement: str
    parent: str | None
    sign: int | None


def _accounts(run: "Run") -> dict[str, Account]:
    with connect(dictionary_db_path(run.root)) as con:
        rows = con.execute("SELECT account, statement, parent, sign FROM accounts").fetchall()
    return {account: Account(statement, parent, sign) for account, statement, parent, sign in rows}


def _figures(con: duckdb.DuckDBPyConnection, doc_id: int) -> dict[Key, dict[str, Decimal]]:
    """{(period, scope): {account: value}} for the confirmed facts of one document."""
    figures: dict[Key, dict[str, Decimal]] = {}
    rows = con.execute(
        "SELECT period, scope, account, value FROM document_facts WHERE doc_id = ?", [doc_id]
    ).fetchall()
    for year, scope, account, value in rows:
        figures.setdefault((year, scope), {})[account] = value
    return figures


def _unmapped(con: duckdb.DuckDBPyConnection, doc_id: int) -> dict[tuple[str, str, str], int]:
    """{(period, scope, statement): number of facts without a confirmed account that could be
    part of a subtotal} (see OUTSIDE_SUBTOTALS; nil lines are left out too)."""
    rows = con.execute(
        "SELECT f.period, f.scope, r.statement, r.section, r.raw_label FROM facts f "
        "JOIN raw_values r USING (raw_id) "
        "WHERE f.doc_id = ? AND (f.account IS NULL OR NOT f.confirmed) AND f.value <> 0",
        [doc_id],
    ).fetchall()
    counts: dict[tuple[str, str, str], int] = {}
    for year, scope, statement, section, label in rows:
        text = f"{normalize_label(label)} / {normalize_label(section)}"
        if statement == "profit_and_loss" and OUTSIDE_SUBTOTALS.search(text):
            continue
        counts[(year, scope, statement)] = counts.get((year, scope, statement), 0) + 1
    return counts


def _compare(
    check: str,
    key: Key,
    expected: Decimal,
    actual: Decimal,
    tolerances: dict[str, Decimal],
    what: str,
    accounts: tuple[str, ...],
) -> Result:
    tolerance = tolerances[TOLERANCE[check]]
    difference = actual - expected
    passed = abs(difference) <= tolerance
    period, scope = key
    message = (
        f"{period} {scope}: {what}: expected {expected}, actual {actual}, difference {difference}, "
        f"tolerance {tolerance}"
    )
    return Result(check, period, scope, expected, actual, tolerance, passed, message, accounts)


def _line_sum(
    account: str,
    values: dict[str, Decimal],
    children: dict[str, list[str]],
    tree: dict[str, Account],
    flip: frozenset[str] = frozenset(),
) -> Decimal | None:
    """The sum of `account`'s lines: each child as reported, or, when the document does not show
    it, the sum of its own lines. None when the document shows none of them. The accounts in
    `flip` are added with the opposite of their dictionary sign."""
    total, found = Decimal(0), False
    for child in children.get(account, []):
        value = values.get(child)
        if value is None:
            value = _line_sum(child, values, children, tree, flip)
        if value is not None:
            sign = -tree[child].sign if child in flip else tree[child].sign
            total += sign * value
            found = True
    return total if found else None


def _subtotal(
    key: Key,
    account: str,
    values: dict[str, Decimal],
    children: dict[str, list[str]],
    tree: dict[str, Account],
    tolerances: dict[str, Decimal],
) -> Result | None:
    """Check one reported subtotal against the sum of its lines; None when it has none. When that
    fails and the lines include exceptional or extraordinary items, try them added instead."""
    total = _line_sum(account, values, children, tree)
    if total is None:
        return None
    lines, i = [account], 0
    while i < len(lines):  # the account and everything below it
        lines += children.get(lines[i], [])
        i += 1
    what = f"{account} vs the sum of its lines"
    used = tuple(lines)
    result = _compare("subtotal", key, total, values[account], tolerances, what, used)
    if result.passed or not EITHER_SIGN & values.keys():
        return result
    added = _line_sum(account, values, children, tree, EITHER_SIGN)
    if added is None or added == total:
        return result
    what += " (exceptional or extraordinary items printed as a gain and added)"
    alternative = _compare("subtotal", key, added, values[account], tolerances, what, used)
    return alternative if alternative.passed else result


def _in_document(
    run: "Run",
    file: str,
    figures: dict[Key, dict[str, Decimal]],
    unmapped: dict[tuple[str, str, str], int],
    tree: dict[str, Account],
    tolerances: dict[str, Decimal],
) -> list[Result]:
    children: dict[str, list[str]] = {}
    for account, info in tree.items():
        if info.parent is not None:
            children.setdefault(info.parent, []).append(account)

    def not_run(check: str, key: Key, reason: str) -> None:
        run.event(
            "validate",
            f"{check} check not run for {key[0]} {key[1]}: {reason}",
            level="warning",
            file=file,
        )

    results = []
    for key, values in sorted(figures.items()):
        pairs = [
            (
                "balance_sheet",
                "total_equity_and_liabilities",
                "total_assets",
                "total assets vs total equity and liabilities",
            ),
            # Old-format "cash and bank balances" (cash_and_bank_balances) also holds other bank
            # balances, so it is not compared with closing cash.
            ("cash_flow", "cash", "closing_cash", "cash flow closing cash vs balance-sheet cash"),
        ]
        for check, expected, actual, what in pairs:
            missing = [account for account in (expected, actual) if account not in values]
            if missing:
                reason = f"no confirmed {' or '.join(missing)} figure"
                if "cash" in missing and "cash_and_bank_balances" in values:
                    reason += (
                        " (the balance sheet shows cash and bank balances, which also hold other "
                        "bank balances)"
                    )
                not_run(check, key, reason)
            else:
                pair = (expected, actual)
                results.append(
                    _compare(check, key, values[expected], values[actual], tolerances, what, pair)
                )
        for statement in SUBTOTAL_STATEMENTS:
            lines = unmapped.get((*key, statement), 0)
            if lines:
                not_run("subtotal", key, f"{lines} {statement} line(s) have no confirmed account")
                continue
            for account in sorted(values):
                if tree[account].statement == statement:
                    result = _subtotal(key, account, values, children, tree, tolerances)
                    if result is not None:
                        results.append(result)
    return results


def _across_documents(
    run: "Run",
    file: str,
    con: duckdb.DuckDBPyConnection,
    doc_id: int,
    figures: dict[Key, dict[str, Decimal]],
    tolerances: dict[str, Decimal],
) -> list[Result]:
    others = con.execute(
        "SELECT d.file_name, x.period, x.scope, x.account, x.value FROM document_facts x "
        "JOIN documents d USING (doc_id) WHERE d.status = 'loaded' AND x.doc_id <> ? "
        "ORDER BY ALL",
        [doc_id],
    ).fetchall()
    results = []
    for other_file, year, scope, account, expected in others:
        actual = figures.get((year, scope), {}).get(account)
        if actual is None:
            continue
        what, key = f"{account} vs {other_file}", (year, scope)
        result = _compare("cross_document", key, expected, actual, tolerances, what, (account,))
        if not result.passed and _unit_factor(expected, actual, result.tolerance):
            what = f"{account} vs {other_file} differs by a unit factor"
            result = _compare("unit_sanity", key, expected, actual, tolerances, what, (account,))
        results.append(result)
    if not results:
        run.event(
            "validate",
            "cross_document and unit_sanity checks not run: no other loaded document has a "
            "confirmed figure for the same period, scope and account",
            level="warning",
            file=file,
        )
    return results


def _unit_factor(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    """True when the larger of a and b is a unit factor times the smaller (within tolerance)."""
    if a == 0 or b == 0 or (a > 0) != (b > 0):
        return False
    small, big = sorted((abs(a), abs(b)))
    return any(abs(big - small * factor) <= tolerance for factor in UNIT_FACTORS)


def _reconciled(con: duckdb.DuckDBPyConnection, doc_id: int, result: Result) -> Result:
    """A failed cash_flow check passes when a cash flow line of the same document, year and scope
    that names cash shows the balance-sheet cash figure: the statement reconciles the two."""
    if result.check_name != "cash_flow" or result.passed:
        return result
    row = con.execute(
        "SELECT r.raw_label FROM facts f JOIN raw_values r USING (raw_id) "
        "WHERE f.doc_id = ? AND f.period = ? AND f.scope = ? AND r.statement = 'cash_flow' "
        "AND r.raw_label ILIKE '%cash%' AND f.value = ? ORDER BY f.raw_id LIMIT 1",
        [doc_id, result.period, result.scope, result.expected],
    ).fetchone()
    if row is None:
        return result
    message = f"{result.message}; reconciled in the cash flow statement by the line {row[0]!r}"
    return replace(result, passed=True, message=message)


def validate_document(
    run: "Run", company: str, con: duckdb.DuckDBPyConnection, doc_id: int
) -> None:
    """Run every gate on the staged document `doc_id`, record each result, and raise the first
    failure (E401-E405)."""
    tolerances = load_config(run.root).tolerances
    row = con.execute("SELECT file_name FROM documents WHERE doc_id = ?", [doc_id]).fetchone()
    assert row is not None
    file_name = row[0]
    file = f"{company}/{file_name}"
    figures = _figures(con, doc_id)
    results = _in_document(run, file, figures, _unmapped(con, doc_id), _accounts(run), tolerances)
    results = [_reconciled(con, doc_id, r) for r in results]
    results += _across_documents(run, file, con, doc_id, figures, tolerances)

    failures = []
    for result in results:
        code = None if result.passed else ERRORS[result.check_name].code
        run.record(
            "validation_results",
            {
                "company": company,
                "doc_id": str(doc_id),
                "file_name": file_name,
                "check_name": result.check_name,
                "fiscal_year": result.period,
                "scope": result.scope,
                "expected": result.expected,
                "actual": result.actual,
                "tolerance": result.tolerance,
                "passed": result.passed,
                "error_code": code,
                "message": result.message,
            },
        )
        if not result.passed:
            failures.append(result)
            run.event("validate", result.message, level="error", code=code, file=file)
    if not failures:
        return
    failures.sort(key=lambda r: list(ERRORS).index(r.check_name))
    first = failures[0]
    more = len(failures) - 1
    message = first.message + (f" (and {more} more failed check(s))" if more else "")
    error = ERRORS[first.check_name](message)
    error.details = tuple(
        {
            "check": r.check_name,
            "code": ERRORS[r.check_name].code,
            "fiscal_year": r.period,
            "scope": r.scope,
            "expected": str(r.expected),
            "actual": str(r.actual),
            "tolerance": str(r.tolerance),
            "message": r.message,
            "lines": _lines(con, doc_id, r),
        }
        for r in failures
    )
    raise error


def _lines(con: duckdb.DuckDBPyConnection, doc_id: int, result: Result) -> list[dict[str, str]]:
    """The document's lines behind a check's accounts, for that year and scope, as written."""
    rows = con.execute(
        "SELECT f.account, r.statement, r.section, r.raw_label, r.raw_value_text, "
        "coalesce(r.sheet || '!' || r.cell, 'page ' || CAST(r.page AS VARCHAR)) "
        "FROM facts f JOIN raw_values r USING (raw_id) WHERE f.doc_id = ? AND f.period = ? "
        "AND f.scope = ? AND f.account IN (SELECT unnest(?)) ORDER BY f.raw_id",
        [doc_id, result.period, result.scope, list(result.accounts)],
    ).fetchall()
    fields = ("account", "statement", "section", "label", "value_as_printed", "where")
    return [dict(zip(fields, row, strict=True)) for row in rows]
