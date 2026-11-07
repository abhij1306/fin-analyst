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

Every check that runs is appended to the audit table `validation_results`, pass or fail.
`validate_document` returns each failed check as a finding (`fin.findings`): what the company filed
does not add up, which is for the user to see and decide, not a failure. The finding has this
document's figure (fin_*) and what it was compared with (other_*), each with its page or cell.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import duckdb

from fin.config import load_config
from fin.extract.units import RUPEES_PER_UNIT
from fin.map.dictionary import Account, load_accounts
from fin.map.normalize import normalize_label

if TYPE_CHECKING:
    from fin.audit.logger import Run

# Each check's PLAN.md §9 code, in the order findings are listed.
CODES = {
    "balance_sheet": "E401",
    "subtotal": "E402",
    "cash_flow": "E403",
    "unit_sanity": "E405",
    "cross_document": "E404",
}
# The config.toml tolerance a check uses, when it is not the check's own.
TOLERANCE = {"unit_sanity": "cross_document"}
UNIT_FACTORS = sorted(
    {a / b for a in RUPEES_PER_UNIT.values() for b in RUPEES_PER_UNIT.values() if a > b}
)
SUBTOTAL_STATEMENTS = ("balance_sheet", "profit_and_loss")
EITHER_SIGN = frozenset({"exceptional_items", "extraordinary_items"})
# Checks of one figure against another: (check, expected account, actual account, what).
# Old-format "cash and bank balances" (cash_and_bank_balances) also holds other bank balances, so
# it is not compared with closing cash.
PAIRS = (
    (
        "balance_sheet",
        "total_equity_and_liabilities",
        "total_assets",
        "total assets vs total equity and liabilities",
    ),
    ("cash_flow", "cash", "closing_cash", "cash flow closing cash vs balance-sheet cash"),
)
# An unmapped P&L line whose label or heading matches this is never part of a subtotal.
OUTSIDE_SUBTOTALS = re.compile(
    r"\bper (?:equity )?share\b|\bbasic\b|\bdiluted\b|\bface value\b|\bnominal value\b"
    r"|\bnumber of (?:equity )?shares\b|\battributable to\b"
    # Results statements print paid-up share capital and other equity below the P&L.
    r"|\bpaid up\b|\bshare capital\b|\bother equity\b"
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
    other_doc_id: int | None = None  # cross_document / unit_sanity: the document compared with
    other_file: str | None = None


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
    tolerance = tolerances[TOLERANCE.get(check, check)]
    difference = actual - expected
    passed = abs(difference) <= tolerance
    period, scope = key
    message = (
        f"{period} {scope}: {what}: expected ₹{expected:,}, actual ₹{actual:,}, "
        f"difference ₹{difference:,}, tolerance ₹{tolerance:,}"
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


def _missing_reason(missing: list[str], values: dict[str, Decimal]) -> str:
    reason = f"no confirmed {' or '.join(missing)} figure"
    if "cash" in missing and "cash_and_bank_balances" in values:
        reason += (
            " (the balance sheet shows cash and bank balances, which also hold other bank balances)"
        )
    return reason


def _children(tree: dict[str, Account]) -> dict[str, list[str]]:
    """Each account's child accounts, in dictionary order."""
    children: dict[str, list[str]] = {}
    for account, info in tree.items():
        if info.parent is not None:
            children.setdefault(info.parent, []).append(account)
    return children


def _in_document(
    run: "Run",
    file: str,
    figures: dict[Key, dict[str, Decimal]],
    unmapped: dict[tuple[str, str, str], int],
    tree: dict[str, Account],
    tolerances: dict[str, Decimal],
) -> list[Result]:
    children = _children(tree)

    def not_run(check: str, key: Key, reason: str) -> None:
        message = f"{check} check not run for {key[0]} {key[1]}: {reason}"
        run.event("validate", message, level="warning", file=file)

    results = []
    for key, values in sorted(figures.items()):
        results += _pair_checks(key, values, tolerances, not_run)
        for statement in SUBTOTAL_STATEMENTS:
            lines = unmapped.get((*key, statement), 0)
            if lines:
                not_run("subtotal", key, f"{lines} {statement} line(s) have no confirmed account")
                continue
            for account in sorted(a for a in values if tree[a].statement == statement):
                result = _subtotal(key, account, values, children, tree, tolerances)
                if result is not None:
                    results.append(result)
    return results


def _pair_checks(
    key: Key,
    values: dict[str, Decimal],
    tolerances: dict[str, Decimal],
    not_run: Callable[[str, Key, str], None],
) -> list[Result]:
    """The PAIRS checks (two accounts that must agree) for one period and scope; a check whose
    accounts are not both confirmed is reported through `not_run` instead."""
    results = []
    for check, expected, actual, what in PAIRS:
        missing = [account for account in (expected, actual) if account not in values]
        if missing:
            not_run(check, key, _missing_reason(missing, values))
            continue
        pair = (expected, actual)
        results.append(
            _compare(check, key, values[expected], values[actual], tolerances, what, pair)
        )
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
        "SELECT d.doc_id, d.file_name, x.period, x.scope, x.account, x.value "
        "FROM document_facts x JOIN documents d USING (doc_id) "
        "WHERE d.status = 'loaded' AND x.doc_id <> ? ORDER BY ALL",
        [doc_id],
    ).fetchall()
    results = []
    for other_doc, other_file, year, scope, account, expected in others:
        actual = figures.get((year, scope), {}).get(account)
        if actual is None:
            continue
        what, key = f"{account} vs {other_file}", (year, scope)
        result = _compare("cross_document", key, expected, actual, tolerances, what, (account,))
        if not result.passed and _unit_factor(expected, actual, result.tolerance):
            what = f"{account} vs {other_file} differs by a unit factor"
            result = _compare("unit_sanity", key, expected, actual, tolerances, what, (account,))
        results.append(replace(result, other_doc_id=other_doc, other_file=other_file))
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
    return Result(**{**vars(result), "passed": True, "message": message})


def validate_document(
    run: "Run", company: str, con: duckdb.DuckDBPyConnection, doc_id: int
) -> list[dict[str, Any]]:
    """Run every gate on the staged document `doc_id`, record each result, and return the failed
    checks as findings (see `_finding`)."""
    tolerances = load_config(run.root).tolerances
    row = con.execute("SELECT file_name FROM documents WHERE doc_id = ?", [doc_id]).fetchone()
    assert row is not None
    file_name = row[0]
    file = f"{company}/{file_name}"
    figures = _figures(con, doc_id)
    results = _in_document(
        run, file, figures, _unmapped(con, doc_id), load_accounts(run.root), tolerances
    )
    results = [_reconciled(con, doc_id, r) for r in results]
    results += _across_documents(run, file, con, doc_id, figures, tolerances)

    failures = []
    for result in results:
        code = None if result.passed else CODES[result.check_name]
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
            run.event("validate", result.message, level="warning", code=code, file=file)
    failures.sort(key=lambda r: list(CODES).index(r.check_name))
    return [_finding(con, doc_id, file_name, r) for r in failures]


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


def _finding(
    con: duckdb.DuckDBPyConnection, doc_id: int, file_name: str, r: Result
) -> dict[str, Any]:
    """A failed check as a finding (columns of the `findings` tables). fin_* is the figure in this
    document; other_* what it was compared with: the other side of the balance sheet, the
    balance-sheet cash, the sum of a subtotal's lines, or another document's figure."""
    if r.check_name in ("balance_sheet", "cash_flow"):
        other_account, account = r.accounts
        other_doc, other_file = doc_id, None
    else:
        account, other_account = r.accounts[0], r.accounts[0]
        other_doc, other_file = r.other_doc_id, r.other_file
    statement, fin_printed, fin_source = _printed(con, doc_id, r.period, r.scope, account)
    other_printed, other_source = None, None
    if r.check_name == "subtotal":
        other_source = "sum of its lines"
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
    }
