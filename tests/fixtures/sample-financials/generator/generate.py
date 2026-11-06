"""Generate synthetic financial documents + answer key.

Usage:  python generate.py [output_dir]
Needs:  openpyxl, reportlab, pypdf, pypdfium2 (and LibreOffice to cache formula values, optional)
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from companies import COMPANIES, models
from excel_writer import bluepeak_workbook, kestrel_workbook
from layouts import MODERN, OLD
from model import metrics
from pdf_writer import encrypt, scanned, tarang_report

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
PREV = {"FY22": "FY21", "FY23": "FY22", "FY24": "FY23", "FY25": "FY24"}

TARANG_NOTES = {
    "related_party": [
        {"party": "Tarang Logistics LLP", "relationship": "Entity controlled by promoter",
         "nature": "Freight services purchased", "amounts": {"FY23": 318.40, "FY24": 365.20, "FY25": 412.50}},
        {"party": "Mr. R. Sharma", "relationship": "Managing Director",
         "nature": "Remuneration", "amounts": {"FY23": 72.00, "FY24": 84.00, "FY25": 96.00}},
        {"party": "Mrs. A. Sharma", "relationship": "Relative of director",
         "nature": "Rent paid", "amounts": {"FY23": 18.00, "FY24": 18.00, "FY25": 18.00}},
    ],
    "contingent_liabilities": [
        {"item": "Bank guarantees issued on behalf of the Company",
         "amounts": {"FY23": 180.00, "FY24": 210.00, "FY25": 245.00}},
        {"item": "Disputed GST demand under appeal",
         "amounts": {"FY23": 0.00, "FY24": 0.00, "FY25": 132.40}},
    ],
    "emphasis_of_matter": {
        "FY24": None,
        "FY25": ("We draw attention to Note 10 to the financial statements, which describes trade receivables "
                 "of ₹ 186.75 lakhs outstanding for more than 180 days from a customer with whom the Company is "
                 "in arbitration over contractual terms. Management considers the amount fully recoverable. "
                 "Our opinion is not modified in respect of this matter."),
    },
}


def rupees(model, rpu, years):
    return {y: {k: v * rpu for k, v in model[y].items()} for y in years}


def labels(layout):
    return {label: acct for st in layout.values() for label, acct, kind, _ in st if acct}


def main():
    clean, broken = OUT / "clean", OUT / "broken"
    for d in (clean, broken):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    M = models()
    key = {"about": "Expected values for the synthetic test documents. All amounts in rupees (integers).",
           "conventions": {
               "fiscal_year": "April-March; FY25 = year ended 31 March 2025",
               "metrics": "Closing balances, 365-day year. EBITDA = PBT + finance cost + depreciation - other income. "
                          "Debt = long-term + short-term borrowings. Inventory and payable days on cost of materials.",
               "signs": "Cash flow outflows are negative."},
           "companies": {}, "broken_files": []}
    to_recalc = []

    # --- Kestrel: one 3-year formula workbook ---------------------------------
    c = COMPANIES["kestrel"]
    m = M["kestrel"]["standalone"]
    years = ["FY25", "FY24", "FY23"]
    d = clean / "kestrel"
    d.mkdir()
    f = d / "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
    loc = kestrel_workbook(f, c["name"], m, years)
    to_recalc.append(f)
    key["companies"]["kestrel"] = {
        "name": c["name"], "format": "xlsx, one workbook, 3 years side by side, formulas for subtotals",
        "unit_in_documents": "₹ in Lakhs", "rupees_per_document_unit": 100_000,
        "documents": [{"file": f"clean/kestrel/{f.name}", "scope": "standalone", "fiscal_years": years,
                       "locations": {y: loc[y] for y in years}}],
        "labels": labels(MODERN),
        "facts": {"standalone": rupees(m, c["rupees_per_unit"], sorted(years))},
        "metrics": {"standalone": {y: metrics(m[y], m[PREV[y]]) for y in sorted(years)}},
        "story": "Receivable days stretch from ~62 (FY23) to ~94 (FY25); EBITDA margin eases 20% -> 18%.",
    }
    # broken: unbalanced balance sheet
    bad = dict()
    bad[("bs", "trade_receivables", "FY25")] = ("value", m["FY25"]["trade_receivables"] + 5_000)
    f = broken / "Kestrel_UNBALANCED_balance_sheet.xlsx"
    kestrel_workbook(f, c["name"], m, years, tamper=bad)
    to_recalc.append(f)
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E401",
                                "what": "FY25 trade receivables increased by ₹50.00 lakhs; assets no longer equal liabilities."})
    # broken: subtotal mismatch
    bad = {("pl", "total_expenses", "FY24"): ("hardcode", m["FY24"]["total_expenses"] + 1_234)}
    f = broken / "Kestrel_SUBTOTAL_mismatch.xlsx"
    kestrel_workbook(f, c["name"], m, years, tamper=bad)
    to_recalc.append(f)
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E402",
                                "what": "FY24 'Total expenses' hard-coded ₹12.34 lakhs above the sum of its lines."})

    # --- Bluepeak: yearly value-only workbooks, standalone + consolidated -----
    c = COMPANIES["bluepeak"]
    mm = M["bluepeak"]
    d = clean / "bluepeak"
    d.mkdir()
    docs = []
    for y, label in [("FY23", "FY2022-23"), ("FY24", "FY2023-24"), ("FY25", "FY2024-25")]:
        f = d / f"Bluepeak_Polymers_{label}.xlsx"
        loc = bluepeak_workbook(f, c["name"], mm, y)
        docs.append({"file": f"clean/bluepeak/{f.name}", "scope": ["standalone", "consolidated"],
                     "fiscal_years": [y, PREV[y]], "locations": loc})
    fy = ["FY22", "FY23", "FY24", "FY25"]
    key["companies"]["bluepeak"] = {
        "name": c["name"],
        "format": "xlsx, one workbook per year with previous-year comparatives, values only, older wording, "
                  "standalone and consolidated sheets",
        "unit_in_documents": "Rs. in Crores", "rupees_per_document_unit": 10_000_000,
        "documents": docs, "labels": labels(OLD),
        "facts": {s: rupees(mm[s], c["rupees_per_unit"], fy) for s in mm},
        "metrics": {s: {y: metrics(mm[s][y], mm[s][PREV[y]]) for y in fy} for s in mm},
        "story": "Debt-funded expansion: borrowings rise, EBITDA margin falls 16% -> 12% as material costs rise.",
    }
    f = broken / "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx"
    bluepeak_workbook(f, c["name"], mm, "FY25", unit_line=None)
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E203",
                                "what": "Unit line '(Rs. in Crores)' removed from every sheet."})
    shift = 350  # 3.50 crore, moved between revenue and other expenses so subtotals still hold
    s = mm["standalone"]["FY24"]
    f = broken / "Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx"
    bluepeak_workbook(f, c["name"], mm, "FY25", prev_overrides={
        ("standalone", "revenue"): s["revenue"] + shift,
        ("standalone", "other_expenses"): s["other_expenses"] + shift,
        ("standalone", "total_income"): s["total_income"] + shift,
        ("standalone", "total_expenses"): s["total_expenses"] + shift,
    })
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E404",
                                "what": "Previous-year (FY24) standalone revenue and other expenses each ₹3.50 Cr "
                                        "higher than in the FY2023-24 workbook. Subtotals still add up; only a "
                                        "cross-document check catches it. Ingest together with the clean FY2023-24 file."})

    # --- Tarang: PDF annual reports -----------------------------------------
    c = COMPANIES["tarang"]
    m = M["tarang"]["standalone"]
    d = clean / "tarang"
    d.mkdir()
    docs = []
    for y, label, style in [("FY24", "FY2023-24", "none"), ("FY25", "FY2024-25", "rules")]:
        f = d / f"Tarang_Industrial_Services_Annual_Report_{label}.pdf"
        notes = dict(TARANG_NOTES, emphasis_of_matter=TARANG_NOTES["emphasis_of_matter"][y])
        info = tarang_report(str(f), c["name"], m, y, notes, style=style)
        docs.append({"file": f"clean/tarang/{f.name}", "scope": "standalone", "fiscal_years": [y, PREV[y]],
                     "table_style": {"none": "whitespace only, no lines",
                                     "rules": "header and total rules"}[style],
                     "pages": info["pages"]})
    fy = ["FY23", "FY24", "FY25"]
    key["companies"]["tarang"] = {
        "name": c["name"], "format": "text PDF annual reports with comparatives, notes and auditor's report",
        "unit_in_documents": "₹ in Lakhs", "rupees_per_document_unit": 100_000,
        "documents": docs, "labels": labels(MODERN),
        "facts": {"standalone": rupees(m, c["rupees_per_unit"], fy)},
        "metrics": {"standalone": {y: metrics(m[y], m[PREV[y]]) for y in fy}},
        "notes": {
            "related_party_rupees": [{**i, "amounts": {k: round(v * 100_000) for k, v in i["amounts"].items()}}
                                     for i in TARANG_NOTES["related_party"]],
            "contingent_liabilities_rupees": [{**i, "amounts": {k: round(v * 100_000) for k, v in i["amounts"].items()}}
                                              for i in TARANG_NOTES["contingent_liabilities"]],
            "emphasis_of_matter": {"FY24": None, "FY25": "Receivables of ₹186.75 lakhs >180 days, customer in arbitration (Note 10)"},
        },
        "story": "Services business growing ~17-20% a year; collections improving (80 -> 75 days); "
                 "debt falling; new disputed GST demand and an emphasis of matter in FY25.",
    }
    src = d / "Tarang_Industrial_Services_Annual_Report_FY2024-25.pdf"
    f = broken / "Tarang_FY2024-25_ENCRYPTED.pdf"
    encrypt(str(src), str(f))
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E102",
                                "what": "Password-protected copy (password: test1234)."})
    f = broken / "Tarang_FY2024-25_SCANNED.pdf"
    scanned(str(src), str(f))
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E104",
                                "what": "Image-only copy with no text layer."})
    f = broken / "Tarang_FY2024-25_CASHFLOW_not_reconciling.pdf"
    notes = dict(TARANG_NOTES, emphasis_of_matter=TARANG_NOTES["emphasis_of_matter"]["FY25"])
    tarang_report(str(f), c["name"], m, "FY25", notes, style="rules",
                  overrides={("cf", "closing_cash", "FY25"): m["FY25"]["closing_cash"] + 1_200})
    key["broken_files"].append({"file": f"broken/{f.name}", "expected_error": "E403",
                                "what": "Cash flow closing cash ₹12.00 lakhs higher than the balance sheet cash."})
    key["broken_files"].append({"file": "clean/kestrel/Kestrel_Forgings_Financials_FY23-FY25.xlsx",
                                "expected_error": "E105", "what": "Ingest the same clean file twice: second run is a duplicate."})

    # Cache formula values so readers see numbers (needs LibreOffice)
    recalc = Path("/mnt/skills/public/xlsx/scripts/recalc.py")
    for f in to_recalc:
        if recalc.exists():
            res = subprocess.run([sys.executable, str(recalc), str(f), "60"], capture_output=True, text=True)
            print(f.name, res.stdout.strip()[:200])

    (OUT / "answer_key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False, default=float))
    print("done:", OUT)


if __name__ == "__main__":
    main()
