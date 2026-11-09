# Extraction backlog

Extraction is not where fin's effort goes (`CLAUDE.md`, "Purpose and focus"). The documents come from established
companies and are well formatted. pdfplumber, the optional Docling add-on and openpyxl stay as they are, and Claude
reading (`fin ingest --reading`) covers whatever they cannot read.

This file is the one place where extraction work waits. Nothing here is built until a **real document** shows the
problem.

## Rules for this backlog

- Work from a golden set of real documents, not invented ones. Each entry needs a real file (kept in the data folder,
  never committed) and its hand-checked figures. The private golden set (`pytest --golden`, PLAN.md §12) is the
  place for them.
- Prefer deleting code to adding it. The goal is a simpler extractor: keep the rules the golden set exercises and
  remove the ones it never needs.
- A fix makes fin either read the document correctly or say that it could not read it, so that Claude reads it.
  Loading a wrong label or value and reporting success is the one outcome that is never acceptable.

## Planned: simplify the PDF extractor

`src/fin/extract/pdf.py` (about 1,100 lines) has grown layout rules one test document at a time: wrapped labels,
partial rows, nil headings, notes pages and results-page numbering. Once the golden set has enough real annual
reports and results filings:

1. Record which rules each golden document exercises (coverage of `pdf.py` while running `pytest --golden`).
2. Remove the rules no real document needs, together with their tests.
3. Where a rule is fragile, replace it with "page not read" and let Claude read the page.

## To decide with the golden set

### Which line is the total when several lines map to one account (audit F01)

`document_facts` (`src/fin/db/schema/company_views.sql`) gives one value per document, account, period and scope.
When a document maps several lines to the same account, the rule is:
- With three or more lines, a line equal to the sum of the others is taken as their total, and the others are dropped.
- With two lines, a line is the total only when the document marks it: its label starts with "Total", or the other
  line sits under it.

Each choice of rule fails in a different case:
- **Current rule:** genuine lines that add up by coincidence lose all but one line. Example: current tax 217.03
  beside MAT credit 117.03 and earlier years' tax 100.00. The subtotal check then usually holds the document with
  an E402 that blames the filing. Only a hand-built document has shown this.
- **"Total only with evidence, for any number of lines"** (built and reverted on 2026-11-09): a total printed on
  its heading row above its parts, with no "Total" label and no section link, is counted twice. Example:
  "Trade payables 30" above "Sundry creditors 12" and "Trade payables 18" gives 60.

Decide when real documents show which layout actually occurs. Count how many golden-set documents have several
lines mapped to one account, and how fin's value compares with the hand-checked figure in each.

## Seen on synthetic documents only (unconfirmed)

Found in a dry run on 2026-11-09 with documents generated for that purpose (`/data/workspace/fin-samples`, not
committed). They show what fin does with these layouts. They are **not bugs until a real document shows them**.
Don't build anything for them before then.

| Layout | What fin did | Kind |
|---|---|---|
| Excel: board signature block under a statement, in the figure columns | Whole file failed, E204 | Whole-file failure |
| Excel: SEBI results sheet titled "Financial Results" / "Statement of Assets and Liabilities" (the PDF reader knows these titles) | Whole file failed, E201 | Whole-file failure |
| Excel: a sheet with no period-date header that mentions "profit" (a five-year summary) | Whole file failed, E202 | Whole-file failure |
| Excel: "Sr. No." column typed as text left of the label column | Rows loaded with labels '3', '5', '7' | Silent wrong label |
| PDF: label wrapped over two lines with the figures on the second | First line kept as a section heading, row labelled with the last line ('11)', '+ C)') | Silent wrong label |
| PDF: last row of a table printed as plain integers (`45`) | Row dropped, no unread warning (audit F04) | Silent drop |
| PDF: "3 months ended" / "6 months ended" written with digits | Pages not read, E202 | Flagged, Claude reads |
| PDF: standalone and consolidated balance sheets side by side on one page | Page not read, E202 | Flagged, Claude reads |
| PDF: directors' report summary table and segment page | Listed as unread statement pages | Spurious warning |
