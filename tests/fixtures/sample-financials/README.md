# Synthetic financial documents for testing

Three fictitious Indian companies, 3–4 years each, in different formats, plus deliberately broken files. Every number is internally consistent: balance sheets balance, subtotals add up, and cash flow closing cash equals balance sheet cash. `answer_key.json` holds the expected value of every line in rupees, where it sits (sheet!cell or page), and the expected metrics.

All companies, people and auditors are invented. Every file carries a "SYNTHETIC TEST DATA" banner.

## Companies

| Company | Format | Unit | Years | What it tests | Story in the numbers |
|---|---|---|---|---|---|
| Kestrel Forgings | One Excel workbook, 3 years side by side, subtotals as formulas, cash flow linked to P&L | ₹ in Lakhs | FY23–FY25 | Formula cells, multi-year columns, note numbers | Receivable days 62 → 94; EBITDA margin 20% → 18% |
| Bluepeak Polymers | One Excel workbook per year with previous-year column; values only; older wording ("Sundry debtors", "Reserves and surplus"); standalone + consolidated sheets | Rs. in Crores | FY22–FY25 | Label mapping, crore units, comparatives, standalone vs consolidated | Debt-funded expansion; EBITDA margin 16% → 12% |
| Tarang Industrial Services | Text PDF annual reports with auditor's report and notes; FY24 tables have no lines, FY25 has rules | ₹ in Lakhs | FY23–FY25 | PDF table extraction, notes text, related parties, contingent liabilities, emphasis of matter | Fast growth, collections improving, new GST dispute in FY25 |

## Broken files

| File | Expected error | Defect |
|---|---|---|
| `Kestrel_UNBALANCED_balance_sheet.xlsx` | E401 | FY25 assets ₹50.00 lakhs above liabilities |
| `Kestrel_SUBTOTAL_mismatch.xlsx` | E402 | FY24 total expenses ₹12.34 lakhs above the sum of its lines |
| `Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx` | E203 | Unit line removed |
| `Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx` | E404 | FY24 comparatives differ from the FY24 workbook by ₹3.50 Cr; subtotals still add up |
| `Tarang_FY2024-25_ENCRYPTED.pdf` | E102 | Password protected (`test1234`) |
| `Tarang_FY2024-25_SCANNED.pdf` | E104 | Image only, no text layer |
| `Tarang_FY2024-25_CASHFLOW_not_reconciling.pdf` | E403 | Cash flow closing cash ₹12.00 lakhs above balance sheet cash |
| Any clean file ingested twice | E105 | Duplicate |

## Using it in the build

- **Phase 3 (Excel):** Kestrel and Bluepeak; every fact must match `facts` and trace to `locations`.
- **Phase 4 (PDF bake-off):** Tarang FY24 (no lines) is the harder case; score extractors on exact matches against the key.
- **Phase 5 (mapping):** `labels` gives the correct account for every label; Bluepeak's older wording is the real test.
- **Phase 6 (validation):** each broken file must fail with its error code and load nothing.
- **Phase 7 (metrics):** compare against `metrics` (definitions in `conventions`).
- **Phases 8–9:** the "story" lines give known trends for test questions and report observations.

## Regenerate and verify

```
cd generator
python generate.py ..     # rebuilds clean/, broken/, answer_key.json
python verify.py ..       # reads every file back and checks it against the key
```

Needs `openpyxl`, `reportlab`, `pypdf`, `pypdfium2`, `pdfplumber`. LibreOffice is used to cache formula values in the Kestrel workbook; without it, formula cells have no stored values until opened in Excel.

## Limits — real documents are messier

- One tax line; no deferred tax, other comprehensive income, exceptional items or prior-period regrouping.
- No note-level breakdowns (e.g., other expenses split) except related parties and contingent liabilities.
- Clean column layouts; real CA files have merged cells, hidden rows, stray totals and inconsistent spacing.
- No rounding differences between statements; real reports often differ by ₹0.01 across pages.

For realistic PDFs, add 3–5 annual reports of small listed Indian manufacturers from BSE or NSE corporate filings (the SME segment is closest to the ₹100–300 Cr revenue band). Keep them in the local private test set, not in the repo.
