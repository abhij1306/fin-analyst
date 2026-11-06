"""Validation gates (PLAN.md Phase 6). `fin.ingest` runs `validate_document` on every staged
document, inside its transaction, before COMMIT.

Checks, for each fiscal year and scope in the document (tolerances from config.toml):

- balance_sheet (E401): total assets = total equity and liabilities.
- subtotal (E402): every balance-sheet and P&L subtotal the document reports = the sum of its lines,
  following `dictionary.accounts` (parent, sign). A subtotal the document does not show is
  replaced by the sum of its own lines.
- cash_flow (E403): cash flow closing cash = balance-sheet cash.
- cross_document (E404): every figure equals the same company / fiscal year / scope / account in
  each other loaded document (for example a comparative column vs that year's own document).
- unit_sanity (E405): a figure that differs from another document by exactly a unit factor (the
  ratio of two units fin reads, such as 100 for crores vs lakhs, or 1000 for lakhs vs thousand
  rupees) is a unit mistake, reported as E405 instead of E404. A single document with a wrong unit
  and no overlap with another document cannot be caught this way.

Only facts with a confirmed account take part. A check whose inputs are missing (no total line, or
unmapped lines in the statement) does not run and says so in a warning event.

Every check that runs is appended to the audit table `validation_results`, pass or fail. If any
fails, `validate_document` raises the first failure's error (in the order above, unit_sanity before
cross_document), so the document is rolled back and sent to review/ with expected vs actual.
"""

from dataclasses import dataclass
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

Key = tuple[str, str]  # (fiscal_year, scope)


@dataclass(frozen=True)
class Result:
    check_name: str
    fiscal_year: str
    scope: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal
    passed: bool
    message: str


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
    """{(fiscal_year, scope): {account: value}} for the confirmed facts of one document."""
    figures: dict[Key, dict[str, Decimal]] = {}
    rows = con.execute(
        "SELECT fiscal_year, scope, account, sum(value) FROM facts "
        "WHERE doc_id = ? AND account IS NOT NULL AND confirmed GROUP BY ALL",
        [doc_id],
    ).fetchall()
    for year, scope, account, value in rows:
        figures.setdefault((year, scope), {})[account] = value
    return figures


def _unmapped(con: duckdb.DuckDBPyConnection, doc_id: int) -> dict[tuple[str, str, str], int]:
    """{(fiscal_year, scope, statement): number of facts without a confirmed account}."""
    rows = con.execute(
        "SELECT f.fiscal_year, f.scope, r.statement, count(*) FROM facts f "
        "JOIN raw_values r USING (raw_id) "
        "WHERE f.doc_id = ? AND (f.account IS NULL OR NOT f.confirmed) GROUP BY ALL",
        [doc_id],
    ).fetchall()
    return {(year, scope, statement): count for year, scope, statement, count in rows}


def _compare(
    check: str,
    key: Key,
    expected: Decimal,
    actual: Decimal,
    tolerances: dict[str, Decimal],
    what: str,
) -> Result:
    tolerance = tolerances[TOLERANCE[check]]
    difference = actual - expected
    passed = abs(difference) <= tolerance
    year, scope = key
    message = (
        f"{year} {scope}: {what}: expected {expected}, actual {actual}, difference {difference}, "
        f"tolerance {tolerance}"
    )
    return Result(check, year, scope, expected, actual, tolerance, passed, message)


def _line_sum(
    account: str,
    values: dict[str, Decimal],
    children: dict[str, list[str]],
    tree: dict[str, Account],
) -> Decimal | None:
    """The sum of `account`'s lines: each child as reported, or, when the document does not show
    it, the sum of its own lines. None when the document shows none of them."""
    total, found = Decimal(0), False
    for child in children.get(account, []):
        value = values.get(child)
        if value is None:
            value = _line_sum(child, values, children, tree)
        if value is not None:
            total += tree[child].sign * value
            found = True
    return total if found else None


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
            ("cash_flow", "cash", "closing_cash", "cash flow closing cash vs balance-sheet cash"),
        ]
        for check, expected, actual, what in pairs:
            missing = [account for account in (expected, actual) if account not in values]
            if missing:
                not_run(check, key, f"no confirmed {' or '.join(missing)} figure")
            else:
                results.append(
                    _compare(check, key, values[expected], values[actual], tolerances, what)
                )
        for statement in SUBTOTAL_STATEMENTS:
            lines = unmapped.get((*key, statement), 0)
            if lines:
                not_run("subtotal", key, f"{lines} {statement} line(s) have no confirmed account")
                continue
            for account, value in sorted(values.items()):
                if tree[account].statement != statement:
                    continue
                total = _line_sum(account, values, children, tree)
                if total is not None:
                    what = f"{account} vs the sum of its lines"
                    results.append(_compare("subtotal", key, total, value, tolerances, what))
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
        "SELECT d.file_name, f.fiscal_year, f.scope, f.account, sum(f.value) FROM facts f "
        "JOIN documents d USING (doc_id) WHERE d.status = 'loaded' AND f.doc_id <> ? "
        "AND f.account IS NOT NULL AND f.confirmed GROUP BY ALL ORDER BY ALL",
        [doc_id],
    ).fetchall()
    results = []
    for other_file, year, scope, account, expected in others:
        actual = figures.get((year, scope), {}).get(account)
        if actual is None:
            continue
        what = f"{account} vs {other_file}"
        result = _compare("cross_document", (year, scope), expected, actual, tolerances, what)
        if not result.passed and _unit_factor(expected, actual, result.tolerance):
            what = f"{account} vs {other_file} differs by a unit factor"
            result = _compare("unit_sanity", (year, scope), expected, actual, tolerances, what)
        results.append(result)
    if not results:
        run.event(
            "validate",
            "cross_document and unit_sanity checks not run: no other loaded document has a "
            "confirmed figure for the same fiscal year, scope and account",
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
                "fiscal_year": result.fiscal_year,
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
            "fiscal_year": r.fiscal_year,
            "scope": r.scope,
            "expected": str(r.expected),
            "actual": str(r.actual),
            "tolerance": str(r.tolerance),
            "message": r.message,
        }
        for r in failures
    )
    raise error
