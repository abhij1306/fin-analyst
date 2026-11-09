"""Read every generated file back and check it against answer_key.json.

Usage: python verify.py [sample_dir]
"""

import json
import sys
from pathlib import Path

import openpyxl
import pdfplumber
from pypdf import PdfReader

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
KEY = json.loads((ROOT / "answer_key.json").read_text())
fails, checks = [], 0


def ok(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


def cell(path, ref):
    sheet, addr = ref.split("!")
    wb = openpyxl.load_workbook(ROOT / path, data_only=True)
    return wb[sheet][addr].value


def to_rupees(v, per_unit):
    return round(v * 100) * per_unit // 100


def fmt(rupees, per_unit):
    v = rupees / per_unit
    s = f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


# Clean Excel: every located value equals the answer key
for co in ["kestrel", "bluepeak"]:
    c = KEY["companies"][co]
    per = c["rupees_per_document_unit"]
    for doc in c["documents"]:
        wb = openpyxl.load_workbook(ROOT / doc["file"], data_only=True)
        locs = doc["locations"]
        scoped = locs if "standalone" in locs else {"standalone": locs}
        for scope, years in scoped.items():
            for y, accts in years.items():
                for acct, ref in accts.items():
                    sheet, addr = ref.split("!")
                    v = wb[sheet][addr].value
                    ok(v is not None, f"{doc['file']} {ref} empty")
                    if v is not None:
                        ok(to_rupees(v, per) == c["facts"][scope][y][acct],
                           f"{doc['file']} {scope} {y} {acct}: {v} != key")

# Clean PDFs: every statement value (current and previous year) appears on its page
c = KEY["companies"]["tarang"]
per = c["rupees_per_document_unit"]
stmt_accts = {
    "bs": ["share_capital", "other_equity", "lt_borrowings", "trade_receivables", "cash", "total_assets",
           "total_equity_and_liabilities"],
    "pl": ["revenue", "other_income", "total_expenses", "pbt", "tax_expense", "pat"],
    "cf": ["cfo", "cfi", "cff", "cf_capex", "cf_dividends_paid", "opening_cash", "closing_cash"],
}
for doc in c["documents"]:
    with pdfplumber.open(ROOT / doc["file"]) as pdf:
        for st, page in doc["pages"].items():
            if st == "notes":
                continue
            text = pdf.pages[page - 1].extract_text()
            for y in doc["fiscal_years"]:
                for acct in stmt_accts[st]:
                    want = fmt(c["facts"]["standalone"][y][acct], per)
                    ok(want in text, f"{doc['file']} p{page} {y} {acct} {want} not found")
        notes_text = pdf.pages[doc["pages"]["notes"] - 1].extract_text()
        ok("Tarang Logistics LLP" in notes_text, f"{doc['file']} related party note missing")
        aud = pdf.pages[1].extract_text()
        has_eom = "Emphasis of Matter" in aud
        ok(has_eom == (doc["fiscal_years"][0] == "FY25"), f"{doc['file']} emphasis of matter presence wrong")

# Internal consistency of the key itself
for co, c in KEY["companies"].items():
    for scope, years in c["facts"].items():
        for y, f in years.items():
            ok(f["total_assets"] == f["total_equity_and_liabilities"], f"key {co} {scope} {y} BS")
            if "revenue" in f:
                cash = f.get("cash", f.get("cash_and_bank_balances"))  # bluepeak: cash and bank balances
                ok(f["closing_cash"] == cash, f"key {co} {scope} {y} CF")

# Broken files show exactly the intended defect
b = {Path(x["file"]).name: x for x in KEY["broken_files"]}
k = KEY["companies"]["kestrel"]["documents"][0]["locations"]
per = 100_000

wb = openpyxl.load_workbook(ROOT / "broken/Kestrel_UNBALANCED_balance_sheet.xlsx", data_only=True)
g = lambda acct, y: wb[k[y][acct].split("!")[0]][k[y][acct].split("!")[1]].value
ok(round(g("total_assets", "FY25") - g("total_equity_and_liabilities", "FY25"), 2) == 50.00, "unbalanced: diff != 50")
ok(round(g("total_assets", "FY24") - g("total_equity_and_liabilities", "FY24"), 2) == 0, "unbalanced: FY24 should balance")

wb = openpyxl.load_workbook(ROOT / "broken/Kestrel_SUBTOTAL_mismatch.xlsx", data_only=True)
lines = ["cost_of_materials", "employee_expense", "finance_cost", "depreciation", "other_expenses"]
ok(round(g("total_expenses", "FY24") - sum(g(a, "FY24") for a in lines), 2) == 12.34, "subtotal: diff != 12.34")
ok(round(g("total_expenses", "FY25") - sum(g(a, "FY25") for a in lines), 2) == 0, "subtotal: FY25 should add up")

wb = openpyxl.load_workbook(ROOT / "broken/Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx")
ok(not any("Crore" in str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value),
   "no-unit file still has a unit line")

bp = KEY["companies"]["bluepeak"]
loc25 = bp["documents"][2]["locations"]["standalone"]["FY24"]
loc24 = bp["documents"][1]["locations"]["standalone"]["FY24"]
bad = openpyxl.load_workbook(ROOT / "broken/Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx", data_only=True)
good = openpyxl.load_workbook(ROOT / "clean/bluepeak/Bluepeak_Polymers_FY2023-24.xlsx", data_only=True)
v = lambda w, ref: w[ref.split("!")[0]][ref.split("!")[1]].value
ok(round(v(bad, loc25["revenue"]) - v(good, loc24["revenue"]), 2) == 3.50, "comparative: revenue diff != 3.50")
pl = ["cost_of_materials", "employee_expense", "finance_cost", "depreciation", "other_expenses"]
ok(round(v(bad, loc25["total_expenses"]) - sum(v(bad, loc25[a]) for a in pl), 2) == 0, "comparative: subtotals broken")
ok(round(v(bad, loc25["pbt"]) - (v(bad, loc25["total_income"]) - v(bad, loc25["total_expenses"])), 2) == 0,
   "comparative: PBT broken")

ok(PdfReader(ROOT / "broken/Tarang_FY2024-25_ENCRYPTED.pdf").is_encrypted, "encrypted: not encrypted")
with pdfplumber.open(ROOT / "broken/Tarang_FY2024-25_SCANNED.pdf") as pdf:
    ok(all(not (p.extract_text() or "").strip() for p in pdf.pages), "scanned: has a text layer")
    ok(len(pdf.pages) == 6, "scanned: page count")
t = KEY["companies"]["tarang"]
with pdfplumber.open(ROOT / "broken/Tarang_FY2024-25_CASHFLOW_not_reconciling.pdf") as pdf:
    cf = pdf.pages[4].extract_text()
    bs = pdf.pages[2].extract_text()
    f25 = t["facts"]["standalone"]["FY25"]
    ok(fmt(f25["closing_cash"] + 1_200_000, per) in cf, "cf tamper: altered closing cash not on CF page")
    ok(fmt(f25["cash"], per) in bs, "cf tamper: BS cash changed")

print(f"{checks} checks, {len(fails)} failures")
for f in fails[:50]:
    print(" FAIL", f)
sys.exit(1 if fails else 0)
