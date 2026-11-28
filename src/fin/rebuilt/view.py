"""Rebuilt statements (SPEC-14): the P&L and balance sheet rebuilt from each year's trial balance
through the confirmed ledger mappings, and an indirect cash flow derived from them.

The temporary view `rebuilt_statements` (made per connection by `add_views`, in `fin sql` too)
has the shape of `standard_statements`, scope standalone, one row per line:

- P&L and balance sheet: each account with a confirmed ledger at or below it, its value the
  ledgers' closing balances (debit - credit) times the account's side; the year's profit also
  counts in other equity, as the balance sheet shows it. `sources`: the trial balance rows
  behind it (file sheet!row), `ledgers` their count; the view `rebuilt_ledgers` has each one.
- Missing is not zero: a ledger with no confirmed account is never rolled up. One waiting for
  review marks the totals above its proposed account incomplete; one with no proposal marks every
  total of its year. `unmapped` (debit - credit) and `unmapped_ledgers` say how much, and a memo
  line (line_type 'unmapped') under the P&L and the balance sheet carries the year's whole
  unmapped amount (a balance, so not under the cash flow, whose lines are marked incomplete).
- Cash flow (indirect), for a year whose previous trial balance is loaded: profit for the year,
  depreciation added back, the change in each balance sheet account by `CASH_LINES`; opening
  plus the three activities equals closing cash, and an "Unmapped / other" line carries any
  difference (an unbalanced trial balance or unmapped ledgers). Lines the balances cannot give are
  'not_derivable' with the reason (`NOT_DERIVABLE`), never estimated (owner decision Q11).

Nothing reported changes; a difference is for a query or an adjustment with evidence.
"""

from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import RebuildNotPossible
from fin.map.dictionary import load_accounts
from fin.rebuilt.sql import VIEW_SQL
from fin.statements import OTHER_LABEL, add_view

RETAINED = "other_equity"
CASH = "cash"
EQUITY_KEY = "rebuilt_equity_other"
OTHER_KEY = "rebuilt_other"
EQUITY_LABEL = "Other changes in equity (dividends and other movements)"
UNMAPPED_LABEL = "Unmapped ledgers (in no line above)"
SHOWN_ROWS = 5  # trial balance rows named in a line's sources; the rest are counted
NOT_DERIVABLE = {
    "cf_taxes_paid": "not derivable: the balances give the tax charged and the change in tax "
    "balances, not the tax paid; both are in operating activities",
    "cf_finance_cost_paid": "not derivable: the balances give the finance cost charged, not the "
    "interest paid; the charge is in profit for the year",
    "cf_interest_received": "not derivable: the balances give the interest income, not the "
    "interest received; the income is in profit for the year",
    "cf_dividends_received": "not derivable: the balances give the dividend income, not the "
    "dividends received; the income is in profit for the year",
    "cf_ppe_sale_proceeds": "not derivable: the balances give the net change in fixed assets, not "
    "purchases and sales apart; both are in purchase of property and equipment",
    "cf_investments_sold": "not derivable: the balances give the net change in investments, not "
    "purchases and sales apart; both are in purchase of investments",
    "cf_dividends_paid": "not derivable: the balances give the change in other equity, not the "
    "dividends paid; they are in other changes in equity",
    "cf_fx_effect": "not derivable: the balances give the change in cash, not the part due to "
    "exchange rates; it is in the change in each balance",
}
# Balance sheet account -> the cash flow line its change goes to. Any other asset goes to other
# current assets, any other liability to other current liabilities.
_LINES = {
    "cf_change_inventories": ["inventories"],
    "cf_change_receivables": ["trade_receivables", "trade_receivables_non_current"],
    "cf_change_oca": ["other_current_assets", "tax_assets_current", "assets_held_for_sale"],
    "cf_change_loans": ["loans_current", "loans_non_current"],
    "cf_change_ofa": ["other_financial_assets_current", "other_financial_assets_non_current"],
    "cf_change_onca": ["other_non_current_assets", "tax_assets_non_current", "deferred_tax_assets"],
    "cf_change_payables": ["trade_payables", "trade_payables_non_current"],
    "cf_change_ocl": [
        "other_current_liabilities",
        "current_tax_liabilities",
        "other_non_current_liabilities",
        "deferred_tax_liabilities",
        "liabilities_held_for_sale",
    ],
    "cf_change_ofl": [
        "other_financial_liabilities_current",
        "other_financial_liabilities_non_current",
    ],
    "cf_change_provisions": ["st_provisions", "lt_provisions"],
    "cf_capex": [
        "fixed_assets",
        "ppe_net",
        "capital_work_in_progress",
        "intangible_assets",
        "intangible_assets_under_development",
        "goodwill",
        "right_of_use_assets",
        "investment_property",
        "biological_assets",
    ],
    "cf_investments_purchased": [
        "non_current_investments",
        "current_investments",
        "equity_method_investments",
    ],
    "cf_bank_deposits_net": ["other_bank_balances"],
    "cf_lt_borrowings_net": ["lt_borrowings"],
    "cf_st_borrowings_net": ["st_borrowings"],
    "cf_share_issue_proceeds": ["share_capital", "share_warrants", "share_application_money"],
    "cf_lease_payments": ["lease_liabilities_current", "lease_liabilities_non_current"],
    EQUITY_KEY: [RETAINED, "non_controlling_interests"],
    CASH: ["cash", "cash_and_bank_balances"],
}
CASH_LINES = {account: line for line, accounts in _LINES.items() for account in accounts}
ACTIVITIES = {
    "operating": [
        "cf_pat",
        "cf_add_depreciation",
        *(line for line in _LINES if line.startswith("cf_change_")),
    ],
    "investing": ["cf_capex", "cf_investments_purchased", "cf_bank_deposits_net"],
    "financing": [
        "cf_lt_borrowings_net",
        "cf_st_borrowings_net",
        "cf_share_issue_proceeds",
        "cf_lease_payments",
        EQUITY_KEY,
    ],
}


def _account_rows(root: Path) -> list[tuple[str, int, str | None]]:
    """(account, side, cash line) of every balance sheet and P&L account in rupees."""
    # Imported here: fin.diligence imports `fin sql` (fin.query), which makes these views.
    from fin.diligence.ledgers import side

    accounts = load_accounts(root)
    rows: list[tuple[str, int, str | None]] = []
    for name, account in accounts.items():
        if account.statement not in ("balance_sheet", "profit_and_loss"):
            continue
        if account.unit != "rupees":
            continue
        sign = side(accounts, name)
        line = None
        if account.statement == "balance_sheet":
            line = CASH_LINES.get(name) or ("cf_change_oca" if sign == 1 else "cf_change_ocl")
        rows.append((name, sign, line))
    return rows


def add_views(con: duckdb.DuckDBPyConnection, root: Path) -> None:
    """Create the temporary views `standard_statements` and `rebuilt_statements`."""
    add_view(con, root)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE rebuilt_accounts (account VARCHAR, side INTEGER, "
        "cash_line VARCHAR)"
    )
    con.executemany("INSERT INTO rebuilt_accounts VALUES (?, ?, ?)", _account_rows(root))
    con.execute("CREATE OR REPLACE TEMP TABLE rebuilt_cash_lines (line VARCHAR, activity VARCHAR)")
    con.executemany(
        "INSERT INTO rebuilt_cash_lines VALUES (?, ?)",
        [(line, activity) for activity, lines in ACTIVITIES.items() for line in lines],
    )
    con.execute("CREATE OR REPLACE TEMP TABLE rebuilt_not_derivable (line VARCHAR, note VARCHAR)")
    con.executemany("INSERT INTO rebuilt_not_derivable VALUES (?, ?)", list(NOT_DERIVABLE.items()))
    con.execute(
        VIEW_SQL.format(
            retained=RETAINED,
            equity=EQUITY_KEY,
            other=OTHER_KEY,
            equity_label=EQUITY_LABEL,
            other_label=OTHER_LABEL,
            unmapped_label=UNMAPPED_LABEL,
            shown=SHOWN_ROWS,
        )
    )


_FILES_SQL = """
SELECT s.period, s.file_name, s.period_start, s.period_end,
    count(*) FILTER (WHERE t.row_type = 'ledger') AS ledgers
FROM source_files s
LEFT JOIN trial_balance t ON t.file_id = s.file_id
WHERE s.engagement_id = ? AND s.kind = 'trial_balance' AND s.status = 'loaded'
GROUP BY ALL
"""

DETAILED = "from Tally in Detailed mode, every ledger with its closing balance"


def readiness(con: duckdb.DuckDBPyConnection, company: str) -> list[dict[str, Any]]:
    """Each period of the latest engagement (its years, then the stub): whether it can be rebuilt
    (a loaded ledger-level trial balance), what to ask the company for when not, and whether it
    has a cash flow (the trial balance of the day before it starts is loaded). E822 when the
    company has no engagement."""
    engagement = con.execute(
        "SELECT engagement_id, periods, stub FROM engagement ORDER BY engagement_id DESC LIMIT 1"
    ).fetchone()
    if engagement is None:
        raise RebuildNotPossible(
            f"{company} has no engagement, so no trial balance to rebuild from: open one "
            f"(`fin engagement open {company} --periods ...`) and load each year's trial balance "
            f"(`fin ingest {company} <file> --kind trial_balance`)"
        ).with_context(company=company)
    files = {row[0]: row for row in con.execute(_FILES_SQL, [engagement[0]]).fetchall()}
    ends = {row[3] for row in files.values() if row[4]}
    periods = [*engagement[1].split(","), *([engagement[2]] if engagement[2] else [])]
    out = []
    for period in periods:
        found = files.get(period)
        year: dict[str, Any] = {"period": period, "file_name": found[1] if found else None}
        if found is None:
            year["ask"] = (
                f"{period}: no trial balance loaded; ask the company for the {period} trial "
                f"balance {DETAILED}, and load it (`fin ingest {company} <file> --kind "
                "trial_balance`)"
            )
        elif not found[4]:
            year["ask"] = (
                f"{period}: {found[1]} has group totals only, no ledgers; ask the company for the "
                f"{period} trial balance {DETAILED}"
            )
        else:
            year["ask"] = None
        year["ready"] = year["ask"] is None
        before = found[2].toordinal() - 1 if found else None
        year["cash_flow"] = bool(year["ready"] and any(e.toordinal() == before for e in ends))
        out.append(year)
    return out
