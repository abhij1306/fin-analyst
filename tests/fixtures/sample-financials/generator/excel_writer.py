from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from layouts import MODERN, OLD, TITLES, TOTALS

BANNER = "SYNTHETIC TEST DATA - fictitious company, generated for software testing"
NUM = '#,##0.00;(#,##0.00);"-"'
FONT = "Arial"
YEAR_END = {"FY22": "31.03.2022", "FY23": "31.03.2023", "FY24": "31.03.2024", "FY25": "31.03.2025"}
YEAR_END_LONG = {"FY22": "31-Mar-2022", "FY23": "31-Mar-2023", "FY24": "31-Mar-2024", "FY25": "31-Mar-2025"}
PREV = {"FY23": "FY22", "FY24": "FY23", "FY25": "FY24"}


def disp(units: int) -> float:
    """Display value: units are hundredths of the display unit."""
    return units / 100


def _font(bold=False, italic=False, color=None):
    return Font(name=FONT, bold=bold, italic=italic, size=10, color=color)


def kestrel_workbook(path, company_name, model, years, tamper=None):
    """3-year workbook, subtotals as formulas (cross-sheet links in cash flow).

    tamper: optional dict {(statement, account, year): ("value", new_units) | ("hardcode", new_units)}
    Returns locations {year: {account: "Sheet!Cell"}}.
    """
    tamper = tamper or {}
    wb = Workbook()
    wb.remove(wb.active)
    locations = {y: {} for y in years}
    sheet_names = {"bs": "Balance Sheet", "pl": "Profit and Loss", "cf": "Cash Flow"}
    row_of = {}  # (statement, account) -> row

    for st in ["pl", "bs", "cf"]:
        ws = wb.create_sheet(sheet_names[st])
        ws["A1"] = BANNER
        ws["A1"].font = _font(italic=True, color="C00000")
        ws["A2"] = company_name.upper()
        ws["A2"].font = _font(bold=True)
        title = TITLES[st] + (" as at" if st == "bs" else " for the year ended")
        ws["A3"] = title
        ws["A3"].font = _font(bold=True)
        ws["A4"] = "(₹ in Lakhs)"
        ws["A4"].font = _font(italic=True)
        hdr = ["Particulars", "Note No."] + [YEAR_END[y] for y in years]
        for c, h in enumerate(hdr, start=1):
            cell = ws.cell(row=6, column=c, value=h)
            cell.font = _font(bold=True)
            cell.alignment = Alignment(horizontal="center" if c > 1 else "left")
            cell.border = Border(bottom=Side(style="thin"))
        r = 7
        for label, acct, kind, note in MODERN[st]:
            ws.cell(row=r, column=1, value=label).font = _font(bold=(kind != "v"))
            if note:
                nc = ws.cell(row=r, column=2, value=note)
                nc.alignment = Alignment(horizontal="center")
                nc.font = _font()
            if acct:
                row_of[(st, acct)] = r
            r += 1
        ws.column_dimensions["A"].width = 62
        ws.column_dimensions["B"].width = 9
        for i in range(len(years)):
            ws.column_dimensions[get_column_letter(3 + i)].width = 15

    # Fill values / formulas
    for st in ["pl", "bs", "cf"]:
        ws = wb[sheet_names[st]]
        for label, acct, kind, note in MODERN[st]:
            if not acct:
                continue
            r = row_of[(st, acct)]
            for i, y in enumerate(years):
                col = get_column_letter(3 + i)
                cell = ws[f"{col}{r}"]
                key = (st, acct, y)
                if key in tamper and tamper[key][0] == "hardcode":
                    cell.value = disp(tamper[key][1])
                elif kind == "t":
                    parts = []
                    for sign, comp in TOTALS[acct]:
                        ref = f"{col}{row_of[(st, comp)]}"
                        parts.append(("+" if sign > 0 else "-") + ref)
                    cell.value = "=" + "".join(parts).lstrip("+")
                elif st == "cf" and acct in ("cf_pbt", "cf_add_depreciation", "cf_add_finance_cost"):
                    src = {"cf_pbt": "pbt", "cf_add_depreciation": "depreciation",
                           "cf_add_finance_cost": "finance_cost"}[acct]
                    cell.value = f"='Profit and Loss'!{col}{row_of[('pl', src)]}"
                else:
                    units = tamper[key][1] if key in tamper else model[y][acct]
                    cell.value = disp(units)
                cell.number_format = NUM
                cell.font = _font(bold=(kind == "t"))
                if kind == "t":
                    cell.border = Border(top=Side(style="thin"))
                locations[y][acct] = f"{sheet_names[st]}!{col}{r}"
    wb.save(path)
    return locations


def bluepeak_workbook(path, company_name, models, year, unit_line="(Rs. in Crores)", prev_overrides=None):
    """One workbook per year: current + previous year, standalone and consolidated, values only.

    prev_overrides: {(scope, account): units} applied to the previous-year column.
    Returns locations {scope: {year: {account: "Sheet!Cell"}}}.
    """
    prev_overrides = prev_overrides or {}
    prev = PREV[year]
    wb = Workbook()
    wb.remove(wb.active)
    locations = {}
    for scope, scope_label in [("standalone", "Standalone"), ("consolidated", "Consolidated")]:
        locations[scope] = {year: {}, prev: {}}
        for st, short in [("bs", "BS"), ("pl", "P&L"), ("cf", "CF")]:
            ws = wb.create_sheet(f"{scope_label} {short}")
            ws.merge_cells("A1:C1")
            ws["A1"] = company_name.upper()
            ws["A1"].font = _font(bold=True)
            ws["A1"].alignment = Alignment(horizontal="center")
            ws["A2"] = BANNER
            ws["A2"].font = _font(italic=True, color="C00000")
            ye = YEAR_END_LONG[year]
            title = {"bs": f"{scope_label} Balance Sheet as at {ye}",
                     "pl": f"{scope_label} Profit & Loss Account for the year ended {ye}",
                     "cf": f"{scope_label} Cash Flow Statement for the year ended {ye}"}[st]
            ws["A3"] = title
            ws["A3"].font = _font(bold=True)
            if unit_line:
                ws["C4"] = unit_line
                ws["C4"].font = _font(italic=True)
                ws["C4"].alignment = Alignment(horizontal="right")
            prefix = "As at " if st == "bs" else "Year ended "
            for c, h in enumerate(["Particulars", prefix + YEAR_END_LONG[year], prefix + YEAR_END_LONG[prev]],
                                  start=1):
                cell = ws.cell(row=5, column=c, value=h)
                cell.font = _font(bold=True)
                cell.border = Border(top=Side(style="thin"), bottom=Side(style="thin"))
            r = 6
            for label, acct, kind, indent in OLD[st]:
                if kind == "h" and r > 6 and indent == 0:
                    r += 1  # blank spacer row before major sections
                ws.cell(row=r, column=1, value=("    " * indent) + label).font = _font(bold=(kind != "v"))
                if acct:
                    for c, y in [(2, year), (3, prev)]:
                        units = models[scope][y][acct]
                        if y == prev and (scope, acct) in prev_overrides:
                            units = prev_overrides[(scope, acct)]
                        cell = ws.cell(row=r, column=c, value=disp(units))
                        cell.number_format = NUM
                        cell.font = _font(bold=(kind == "t"))
                        locations[scope][y][acct] = f"{ws.title}!{get_column_letter(c)}{r}"
                r += 1
            ws.column_dimensions["A"].width = 52
            ws.column_dimensions["B"].width = 22
            ws.column_dimensions["C"].width = 22
    wb.save(path)
    return locations
