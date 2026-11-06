"""Internally consistent synthetic financial statements.

All arithmetic is done in integer "units" so every total is exact:
  - lakh companies: 1 unit = 0.01 lakh  = Rs 1,000
  - crore companies: 1 unit = 0.01 crore = Rs 1,00,000

Construction guarantees:
  - Balance sheet balances every year (cash comes from the cash flow statement).
  - P&L subtotals are sums of their lines.
  - Cash flow closing cash = balance sheet cash.
"""

from dataclasses import dataclass, field

YEARS = ["FY21", "FY22", "FY23", "FY24", "FY25"]  # FY21 = opening balance sheet only
FULL_YEARS = YEARS[1:]


def r(x: float) -> int:
    return int(round(x))


@dataclass
class Params:
    revenue_fy21: float          # units
    growth: list                 # FY22..FY25
    materials_pct: float | list  # single value or FY22..FY25
    employee_pct: float
    other_exp_pct: float
    other_income_pct: float
    dep_pct_of_opening_ppe: float
    interest_rate: float
    tax_rate: float
    receivable_days: list        # FY21..FY25
    inventory_days: list         # on materials, FY21..FY25
    payable_days: list           # on materials, FY21..FY25
    oca_pct: float
    ocl_pct: float
    capex_pct: list              # FY22..FY25, of revenue
    ppe_fy21_pct: float
    share_capital: int
    other_equity_fy21: int
    lt_borrowings: list          # FY21..FY25 closing
    st_borrowings_fy21: int
    cash_fy21: int
    min_cash_pct: float
    dividends: list              # FY22..FY25 paid
    extra: dict = field(default_factory=dict)


def build(p: Params) -> dict:
    """Return {year: {account: int units}}."""
    out = {}
    rev = {"FY21": r(p.revenue_fy21)}
    for y, g in zip(FULL_YEARS, p.growth):
        prev = YEARS[YEARS.index(y) - 1]
        rev[y] = r(rev[prev] * (1 + g))

    # FY21 opening balance sheet
    y0 = "FY21"
    mat0 = r(rev[y0] * (p.materials_pct[0] if isinstance(p.materials_pct, list) else p.materials_pct))
    bs = {
        "share_capital": p.share_capital,
        "other_equity": p.other_equity_fy21,
        "lt_borrowings": p.lt_borrowings[0],
        "st_borrowings": p.st_borrowings_fy21,
        "trade_payables": r(mat0 * p.payable_days[0] / 365),
        "other_current_liabilities": r(rev[y0] * p.ocl_pct),
        "inventories": r(mat0 * p.inventory_days[0] / 365),
        "trade_receivables": r(rev[y0] * p.receivable_days[0] / 365),
        "other_current_assets": r(rev[y0] * p.oca_pct),
        "cash": p.cash_fy21,
    }
    liab = (bs["share_capital"] + bs["other_equity"] + bs["lt_borrowings"] + bs["st_borrowings"]
            + bs["trade_payables"] + bs["other_current_liabilities"])
    non_ppe_assets = bs["inventories"] + bs["trade_receivables"] + bs["other_current_assets"] + bs["cash"]
    bs["ppe_net"] = liab - non_ppe_assets  # opening PPE plugs the opening balance sheet
    if bs["ppe_net"] <= 0:
        raise ValueError("Opening PPE not positive; adjust params")
    out[y0] = _bs_totals(bs)

    for i, y in enumerate(FULL_YEARS, start=1):
        prev = out[YEARS[i - 1]]
        R = rev[y]
        mpct = p.materials_pct[i - 1] if isinstance(p.materials_pct, list) else p.materials_pct
        mat = r(R * mpct)
        emp = r(R * p.employee_pct)
        oth = r(R * p.other_exp_pct)
        oi = r(R * p.other_income_pct)
        dep = r(prev["ppe_net"] * p.dep_pct_of_opening_ppe)
        fin = r((prev["lt_borrowings"] + prev["st_borrowings"]) * p.interest_rate)
        total_income = R + oi
        total_exp = mat + emp + fin + dep + oth
        pbt = total_income - total_exp
        tax = r(pbt * p.tax_rate)
        pat = pbt - tax

        capex = r(R * p.capex_pct[i - 1])
        ppe = prev["ppe_net"] + capex - dep
        inv = r(mat * p.inventory_days[i] / 365)
        rec = r(R * p.receivable_days[i] / 365)
        oca = r(R * p.oca_pct)
        tp = r(mat * p.payable_days[i] / 365)
        ocl = r(R * p.ocl_pct)
        ltb = p.lt_borrowings[i]
        div = p.dividends[i - 1]

        d_inv = inv - prev["inventories"]
        d_rec = rec - prev["trade_receivables"]
        d_oca = oca - prev["other_current_assets"]
        d_tp = tp - prev["trade_payables"]
        d_ocl = ocl - prev["other_current_liabilities"]
        d_ltb = ltb - prev["lt_borrowings"]

        op_before_wc = pbt + dep + fin - oi
        wc_change = -d_inv - d_rec - d_oca + d_tp + d_ocl
        cash_generated = op_before_wc + wc_change
        cfo = cash_generated - tax
        cfi = -capex + oi
        pre_stb_cff = d_ltb - fin - div
        cash_pre = prev["cash"] + cfo + cfi + pre_stb_cff
        min_cash = r(R * p.min_cash_pct)
        d_stb = max(0, min_cash - cash_pre)   # short-term borrowings fund any shortfall
        stb = prev["st_borrowings"] + d_stb
        cff = pre_stb_cff + d_stb
        net_change = cfo + cfi + cff
        cash = prev["cash"] + net_change

        bs = {
            "share_capital": p.share_capital,
            "other_equity": prev["other_equity"] + pat - div,
            "lt_borrowings": ltb,
            "st_borrowings": stb,
            "trade_payables": tp,
            "other_current_liabilities": ocl,
            "ppe_net": ppe,
            "inventories": inv,
            "trade_receivables": rec,
            "other_current_assets": oca,
            "cash": cash,
        }
        row = _bs_totals(bs)
        row.update({
            "revenue": R, "other_income": oi, "total_income": total_income,
            "cost_of_materials": mat, "employee_expense": emp, "finance_cost": fin,
            "depreciation": dep, "other_expenses": oth, "total_expenses": total_exp,
            "pbt": pbt, "tax_expense": tax, "pat": pat,
            "cf_pbt": pbt, "cf_add_depreciation": dep, "cf_add_finance_cost": fin,
            "cf_less_interest_income": -oi, "cf_op_profit_before_wc": op_before_wc,
            "cf_change_inventories": -d_inv, "cf_change_receivables": -d_rec,
            "cf_change_oca": -d_oca, "cf_change_payables": d_tp, "cf_change_ocl": d_ocl,
            "cf_cash_generated": cash_generated, "cf_taxes_paid": -tax, "cfo": cfo,
            "cf_capex": -capex, "cf_interest_received": oi, "cfi": cfi,
            "cf_lt_borrowings_net": d_ltb, "cf_st_borrowings_net": d_stb,
            "cf_finance_cost_paid": -fin, "cf_dividends_paid": -div, "cff": cff,
            "net_change_cash": net_change, "opening_cash": prev["cash"], "closing_cash": cash,
        })
        out[y] = row
    check(out)
    return out


def _bs_totals(bs: dict) -> dict:
    bs = dict(bs)
    bs["total_equity"] = bs["share_capital"] + bs["other_equity"]
    bs["total_non_current_liabilities"] = bs["lt_borrowings"]
    bs["total_current_liabilities"] = bs["st_borrowings"] + bs["trade_payables"] + bs["other_current_liabilities"]
    bs["total_equity_and_liabilities"] = (bs["total_equity"] + bs["total_non_current_liabilities"]
                                          + bs["total_current_liabilities"])
    bs["total_non_current_assets"] = bs["ppe_net"]
    bs["total_current_assets"] = (bs["inventories"] + bs["trade_receivables"]
                                  + bs["other_current_assets"] + bs["cash"])
    bs["total_assets"] = bs["total_non_current_assets"] + bs["total_current_assets"]
    return bs


def check(model: dict) -> None:
    for y, v in model.items():
        assert v["total_assets"] == v["total_equity_and_liabilities"], f"{y} BS does not balance"
        assert v["cash"] > 0, f"{y} cash not positive"
        if "revenue" in v:
            assert v["pbt"] == v["total_income"] - v["total_expenses"]
            assert v["pat"] == v["pbt"] - v["tax_expense"]
            assert v["closing_cash"] == v["cash"], f"{y} CF does not reconcile"
            assert v["pat"] > 0, f"{y} loss-making; adjust params"


def combine(a: dict, b: dict) -> dict:
    """Line-by-line sum (consolidated = parent + subsidiary, no intercompany)."""
    out = {y: {k: a[y][k] + b[y][k] for k in a[y]} for y in a}
    check(out)
    return out


def metrics(v: dict, prev: dict | None) -> dict:
    """Expected metric values. Closing balances, 365-day year."""
    ebitda = v["pbt"] + v["finance_cost"] + v["depreciation"] - v["other_income"]
    debt = v["lt_borrowings"] + v["st_borrowings"]
    cur_liab = v["total_current_liabilities"]
    m = {
        "ebitda": ebitda,
        "ebitda_margin": ebitda / v["revenue"],
        "pat_margin": v["pat"] / v["revenue"],
        "receivable_days": v["trade_receivables"] / v["revenue"] * 365,
        "inventory_days": v["inventories"] / v["cost_of_materials"] * 365,
        "payable_days": v["trade_payables"] / v["cost_of_materials"] * 365,
        "debt_to_equity": debt / v["total_equity"],
        "debt_to_ebitda": debt / ebitda,
        "interest_cover": ebitda / v["finance_cost"],
        "current_ratio": v["total_current_assets"] / cur_liab,
        "cfo_to_ebitda": v["cfo"] / ebitda,
        "free_cash_flow": v["cfo"] + v["cf_capex"],
    }
    m["cash_conversion_cycle"] = m["receivable_days"] + m["inventory_days"] - m["payable_days"]
    if prev is not None and "revenue" in prev:
        m["revenue_growth"] = v["revenue"] / prev["revenue"] - 1
    return m
