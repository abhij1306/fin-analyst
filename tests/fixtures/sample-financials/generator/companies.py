"""Three fictitious companies with different formats, units and stories."""

from model import Params, build, combine

L = 100      # lakh companies: 1 lakh = 100 units (unit = Rs 1,000)
C = 100      # crore companies: 1 crore = 100 units (unit = Rs 1,00,000)

COMPANIES = {
    # Excel, one 3-year workbook, Rs in lakhs, formulas for subtotals.
    # Story: receivable days stretch from 62 (FY23) to 94 (FY25).
    "kestrel": {
        "name": "Kestrel Forgings Private Limited",
        "unit": "lakh",
        "rupees_per_unit": 1_000,
        "params": Params(
            revenue_fy21=8_600 * L, growth=[0.10, 0.12, 0.13, 0.12],
            materials_pct=[0.575, 0.58, 0.59, 0.60], employee_pct=0.09, other_exp_pct=0.13, other_income_pct=0.004,
            dep_pct_of_opening_ppe=0.08, interest_rate=0.095, tax_rate=0.2517,
            receivable_days=[58, 60, 62, 78, 94], inventory_days=[75] * 5, payable_days=[60] * 5,
            oca_pct=0.04, ocl_pct=0.03, capex_pct=[0.06, 0.08, 0.09, 0.05], ppe_fy21_pct=0,
            share_capital=500 * L, other_equity_fy21=3_200 * L,
            lt_borrowings=[x * L for x in [1_500, 1_400, 1_800, 2_200, 2_000]],
            st_borrowings_fy21=900 * L, cash_fy21=250 * L, min_cash_pct=0.015,
            dividends=[x * L for x in [50, 50, 75, 75]],
        ),
    },
    # Excel, one workbook per year with previous-year comparatives, Rs in crores,
    # older label wording, values only, standalone + consolidated.
    # Story: debt-funded expansion, leverage rising.
    "bluepeak": {
        "name": "Bluepeak Polymers Limited",
        "unit": "crore",
        "rupees_per_unit": 100_000,
        "params": Params(
            revenue_fy21=165 * C, growth=[0.14, 0.16, 0.12, 0.10],
            materials_pct=[0.63, 0.64, 0.66, 0.68], employee_pct=0.06, other_exp_pct=0.14, other_income_pct=0.003,
            dep_pct_of_opening_ppe=0.07, interest_rate=0.09, tax_rate=0.2517,
            receivable_days=[72, 70, 68, 72, 75], inventory_days=[60] * 5, payable_days=[75] * 5,
            oca_pct=0.05, ocl_pct=0.035, capex_pct=[0.05, 0.12, 0.15, 0.10], ppe_fy21_pct=0,
            share_capital=12 * C, other_equity_fy21=70 * C,
            lt_borrowings=[x * C for x in [20, 22, 40, 62, 70]],
            st_borrowings_fy21=15 * C, cash_fy21=6 * C, min_cash_pct=0.02,
            dividends=[int(x * C) for x in [2, 2, 2.5, 2.5]],
        ),
        "subsidiary": Params(
            revenue_fy21=12 * C, growth=[0.10, 0.10, 0.08, 0.08],
            materials_pct=0.60, employee_pct=0.08, other_exp_pct=0.15, other_income_pct=0.002,
            dep_pct_of_opening_ppe=0.07, interest_rate=0.10, tax_rate=0.2517,
            receivable_days=[60] * 5, inventory_days=[50] * 5, payable_days=[70] * 5,
            oca_pct=0.04, ocl_pct=0.03, capex_pct=[0.03, 0.03, 0.04, 0.03], ppe_fy21_pct=0,
            share_capital=1 * C, other_equity_fy21=4 * C,
            lt_borrowings=[x * C for x in [2, 2, 2, 3, 3]],
            st_borrowings_fy21=1 * C, cash_fy21=int(0.5 * C), min_cash_pct=0.02,
            dividends=[0, 0, 0, 0],
        ),
    },
    # PDF annual reports (FY24, FY25) with comparatives, Rs in lakhs, notes and
    # auditor's report text. Story: services business, fast growth, improving collections.
    "tarang": {
        "name": "Tarang Industrial Services Private Limited",
        "unit": "lakh",
        "rupees_per_unit": 1_000,
        "params": Params(
            revenue_fy21=5_800 * L, growth=[0.15, 0.18, 0.20, 0.17],
            materials_pct=[0.36, 0.35, 0.345, 0.34], employee_pct=0.30, other_exp_pct=0.14, other_income_pct=0.006,
            dep_pct_of_opening_ppe=0.10, interest_rate=0.09, tax_rate=0.2517,
            receivable_days=[85, 82, 80, 78, 75], inventory_days=[20] * 5, payable_days=[50] * 5,
            oca_pct=0.03, ocl_pct=0.05, capex_pct=[0.03, 0.04, 0.03, 0.04], ppe_fy21_pct=0,
            share_capital=300 * L, other_equity_fy21=2_100 * L,
            lt_borrowings=[x * L for x in [600, 500, 400, 300, 200]],
            st_borrowings_fy21=400 * L, cash_fy21=300 * L, min_cash_pct=0.03,
            dividends=[x * L for x in [40, 60, 80, 100]],
        ),
    },
}


def models() -> dict:
    out = {}
    for key, c in COMPANIES.items():
        standalone = build(c["params"])
        out[key] = {"standalone": standalone}
        if "subsidiary" in c:
            out[key]["consolidated"] = combine(standalone, build(c["subsidiary"]))
    return out


if __name__ == "__main__":
    from model import metrics
    for key, scopes in models().items():
        for scope, m in scopes.items():
            for y in ["FY23", "FY24", "FY25"]:
                v = m[y]
                prev = m[{"FY23": "FY22", "FY24": "FY23", "FY25": "FY24"}[y]]
                mt = metrics(v, prev)
                print(f"{key:9} {scope:12} {y} rev={v['revenue']:>9} pat={v['pat']:>7} "
                      f"cash={v['cash']:>7} stb={v['st_borrowings']:>7} "
                      f"ebitda%={mt['ebitda_margin']:.1%} recd={mt['receivable_days']:.0f} "
                      f"d/e={mt['debt_to_equity']:.2f} cfo/ebitda={mt['cfo_to_ebitda']:.2f}")
