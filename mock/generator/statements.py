"""The statements, built from the trial balances by the mapping in `data.LEDGERS`.

`accounts` rolls a period's trial balance up to fin's accounts (rupees, as each statement prints
them: income and liabilities positive). `printed` is what a statement document prints for a
period: the provisional FY25 statements come from the trial balance before the auditor's
adjustment, every other one from the final books, and the FY24 figures (in every document that
prints FY24) carry the statement reclassification. The layouts are Schedule III lines whose labels
fin's dictionary knows.
"""

from mock.generator import books as b
from mock.generator import data as d

# (label, account, kind): kind h heading, v value, t total
BALANCE_SHEET = [
    ("EQUITY AND LIABILITIES", None, "h"),
    ("(a) Equity share capital", "share_capital", "v"),
    ("(b) Other equity", "other_equity", "v"),
    ("Total equity", "total_equity", "t"),
    ("Non-current liabilities", None, "h"),
    ("(a) Financial liabilities - Borrowings", "lt_borrowings", "v"),
    ("Total non-current liabilities", "total_non_current_liabilities", "t"),
    ("Current liabilities", None, "h"),
    ("(b) Trade payables", "trade_payables", "v"),
    ("(c) Other current liabilities", "other_current_liabilities", "v"),
    ("Short-term provisions", "st_provisions", "v"),
    ("Current tax liabilities (net)", "current_tax_liabilities", "v"),
    ("Total current liabilities", "total_current_liabilities", "t"),
    ("TOTAL EQUITY AND LIABILITIES", "total_equity_and_liabilities", "t"),
    ("ASSETS", None, "h"),
    ("Non-current assets", None, "h"),
    ("(a) Property, plant and equipment", "ppe_net", "v"),
    ("Total non-current assets", "total_non_current_assets", "t"),
    ("Current assets", None, "h"),
    ("(b) Financial assets - Trade receivables", "trade_receivables", "v"),
    ("(c) Financial assets - Cash and cash equivalents", "cash", "v"),
    ("(d) Other current assets", "other_current_assets", "v"),
    ("Total current assets", "total_current_assets", "t"),
    ("TOTAL ASSETS", "total_assets", "t"),
]
PROFIT_AND_LOSS = [
    ("I. Revenue from operations", "revenue", "v"),
    ("II. Other income", "other_income", "v"),
    ("III. Total income (I + II)", "total_income", "t"),
    ("IV. Expenses", None, "h"),
    ("Purchases of stock-in-trade", "purchases_stock_in_trade", "v"),
    ("Employee benefits expense", "employee_expense", "v"),
    ("Finance costs", "finance_cost", "v"),
    ("Depreciation and amortisation expense", "depreciation", "v"),
    ("Other expenses", "other_expenses", "v"),
    ("Total expenses (IV)", "total_expenses", "t"),
    ("V. Profit before tax (III - IV)", "pbt", "t"),
    ("VI. Tax expense - Current tax", "tax_expense", "v"),
    ("VII. Profit for the year (V - VI)", "pat", "t"),
]
CASH_FLOW = [
    ("A. CASH FLOW FROM OPERATING ACTIVITIES", None, "h"),
    ("Profit before tax", "cf_pbt", "v"),
    ("Adjustments for: Depreciation and amortisation expense", "cf_add_depreciation", "v"),
    ("Adjustments for: Finance costs", "cf_add_finance_cost", "v"),
    ("Operating profit before working capital changes", "cf_op_profit_before_wc", "t"),
    ("(Increase)/decrease in trade receivables", "cf_change_receivables", "v"),
    ("(Increase)/decrease in other current assets", "cf_change_oca", "v"),
    ("Increase/(decrease) in trade payables", "cf_change_payables", "v"),
    ("Increase/(decrease) in other current liabilities", "cf_change_ocl", "v"),
    ("Increase/(decrease) in provisions", "cf_change_provisions", "v"),
    ("Cash generated from operations", "cf_cash_generated", "t"),
    ("Income taxes paid", "cf_taxes_paid", "v"),
    ("Net cash from operating activities (A)", "cfo", "t"),
    ("B. CASH FLOW FROM INVESTING ACTIVITIES", None, "h"),
    ("Purchase of property, plant and equipment", "cf_capex", "v"),
    ("Net cash used in investing activities (B)", "cfi", "t"),
    ("C. CASH FLOW FROM FINANCING ACTIVITIES", None, "h"),
    ("Proceeds from/(repayment of) long-term borrowings (net)", "cf_lt_borrowings_net", "v"),
    ("Finance costs paid", "cf_finance_cost_paid", "v"),
    ("Net cash from/(used in) financing activities (C)", "cff", "t"),
    ("Net increase/(decrease) in cash and cash equivalents (A+B+C)", "net_change_cash", "t"),
    ("Cash and cash equivalents at the beginning of the year", "opening_cash", "v"),
    ("Cash and cash equivalents at the end of the year", "closing_cash", "t"),
]
LAYOUTS = {
    "Balance Sheet": BALANCE_SHEET,
    "Profit and Loss": PROFIT_AND_LOSS,
    "Cash Flow": CASH_FLOW,
}
ASSETS = ("ppe_net", "trade_receivables", "cash", "other_current_assets")
INCOME = ("revenue", "other_income")


def accounts(tb: dict[str, int]) -> dict[str, int]:
    """account -> amount as printed (assets and expenses debit - credit, the rest
    credit - debit)."""
    out: dict[str, int] = {}
    for ledger, value in tb.items():
        group, account = d.LEDGERS[ledger]
        if group in d.PL_GROUPS:
            out[account] = out.get(account, 0) + (-value if account in INCOME else value)
        else:
            out[account] = out.get(account, 0) + (value if account in ASSETS else -value)
    return out


def _totals(a: dict[str, int]) -> dict[str, int]:
    g = a.get
    a["total_income"] = g("revenue", 0) + g("other_income", 0)
    a["total_expenses"] = sum(
        g(k, 0)
        for k in (
            "purchases_stock_in_trade",
            "employee_expense",
            "finance_cost",
            "depreciation",
            "other_expenses",
        )
    )
    a["pbt"] = a["total_income"] - a["total_expenses"]
    a["pat"] = a["pbt"] - g("tax_expense", 0)
    a["other_equity"] = g("other_equity", 0) + a["pat"]  # the opening plus the year's profit
    a["total_equity"] = g("share_capital", 0) + a["other_equity"]
    a["total_non_current_liabilities"] = g("lt_borrowings", 0)
    a["total_current_liabilities"] = sum(
        g(k, 0)
        for k in (
            "trade_payables",
            "other_current_liabilities",
            "st_provisions",
            "current_tax_liabilities",
        )
    )
    a["total_equity_and_liabilities"] = (
        a["total_equity"] + a["total_non_current_liabilities"] + a["total_current_liabilities"]
    )
    a["total_non_current_assets"] = g("ppe_net", 0)
    a["total_current_assets"] = sum(g(k, 0) for k in ASSETS[1:])
    a["total_assets"] = a["total_non_current_assets"] + a["total_current_assets"]
    if a["total_assets"] != a["total_equity_and_liabilities"]:
        raise ValueError("the balance sheet does not balance")
    return a


def opening() -> dict[str, int]:
    """The balance sheet on 31-Mar-2022 (the FY23 cash flow starts from it)."""
    tb = {ledger: d.OPENING.get(ledger, 0) for ledger in d.LEDGERS}
    tb[d.RETAINED] = -sum(d.OPENING.values())
    return _totals(accounts(tb))


def books_view(books: b.Books, period: str, audited: bool = True) -> dict[str, int]:
    """The period's figures from its trial balance, with no reclassification."""
    return _totals(accounts(b.balances(books, period, audited)))


def printed(books: b.Books, period: str, audited: bool = True) -> dict[str, int]:
    """What a statement document prints for the period, cash flow included."""
    a = books_view(books, period, audited)
    move = d.STATEMENT_RECLASS
    if period == move["period"]:
        a[move["from"]] -= move["amount"]
        a[move["to"]] += move["amount"]
    index = list(d.PERIODS).index(period)
    before = opening() if index == 0 else books_view(books, list(d.PERIODS)[index - 1])
    return a | _cash_flow(a, before)


def _cash_flow(a: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    def change(account: str) -> int:
        return a.get(account, 0) - before.get(account, 0)

    c = {
        "cf_pbt": a["pbt"],
        "cf_add_depreciation": a.get("depreciation", 0),
        "cf_add_finance_cost": a.get("finance_cost", 0),
        "cf_change_receivables": -change("trade_receivables"),
        "cf_change_oca": -change("other_current_assets"),
        "cf_change_payables": change("trade_payables"),
        "cf_change_ocl": change("other_current_liabilities"),
        "cf_change_provisions": change("st_provisions"),
        "cf_taxes_paid": -(a.get("tax_expense", 0) - change("current_tax_liabilities")),
        "cf_capex": -(change("ppe_net") + a.get("depreciation", 0)),
        "cf_lt_borrowings_net": change("lt_borrowings"),
        "cf_finance_cost_paid": -a.get("finance_cost", 0),
        "opening_cash": before["cash"],
    }
    c["cf_op_profit_before_wc"] = c["cf_pbt"] + c["cf_add_depreciation"] + c["cf_add_finance_cost"]
    c["cf_cash_generated"] = c["cf_op_profit_before_wc"] + sum(
        c[k]
        for k in (
            "cf_change_receivables",
            "cf_change_oca",
            "cf_change_payables",
            "cf_change_ocl",
            "cf_change_provisions",
        )
    )
    c["cfo"] = c["cf_cash_generated"] + c["cf_taxes_paid"]
    c["cfi"] = c["cf_capex"]
    c["cff"] = c["cf_lt_borrowings_net"] + c["cf_finance_cost_paid"]
    c["net_change_cash"] = c["cfo"] + c["cfi"] + c["cff"]
    c["closing_cash"] = c["opening_cash"] + c["net_change_cash"]
    if c["closing_cash"] != a["cash"]:
        raise ValueError("the cash flow does not reach the balance sheet cash")
    return c
