# Forensic layer (SPEC-18 to SPEC-21): owner decisions, feasibility and readings

The owner's forensic plan (2026-11-28, kept outside the repo) proposed six workstreams, X1 to X6. This file holds the
owner's decisions on it, the answers to its thirteen feasibility questions (read from the code and the fixtures, not
from the plan) and the builder's readings the specs rest on. Readings follow [README](README.md): the owner confirms
or changes them.

## Owner decisions

Given to the orchestrator on the plan, 2026-11-28.

- **OWNER DECISION (2026-11-28): what is built now.** Before the end-to-end test: X1 journal-entry tests
  ([SPEC-18](../specs/SPEC-18-forensic-journal-tests.md)); X3 bank forensics without the PDF file checks (metadata,
  fonts, text layer: skipped) ([SPEC-19](../specs/SPEC-19-forensic-bank.md)); X4 counterparty and scheme tests without
  the GSTR-2B intake ([SPEC-20](../specs/SPEC-20-forensic-counterparty.md)); from X6, the forensic section of the FDD
  report only ([SPEC-21](../specs/SPEC-21-forensic-report.md)). One PR per spec.
- **OWNER DECISION (2026-11-28): backlog, not built now.** X2 (Tally Edit Log and narration intake): the next client
  handoff asks the client for the column headings of the Edit Log export and of a Day Book with narration. X5
  public-record evidence: "no public record checks needed for now will deiced later". Slides (pptx). GSTR-2B, unless
  the client can provide it. A ranking system: "keep ordering for now and keep a proposal for ranking system in
  backlog will revisit later" (flags may be ordered, for example by amount; no per-entry score). GSTR-2B was on this
  list first; the owner's later answer (below) moved it into SPEC-20.
- **OWNER DECISION (2026-11-28): out of scope.** Statutory audit and peer comparison.
- **OWNER DECISION (2026-11-28): the plan's D6 is declined.** `CLAUDE.md` keeps its extraction wording: extraction
  "doesnt affect finanical diligence and can be solved separately later".
- **OWNER DECISION (2026-11-28): planted forensic cases in the mock set.** "yes". The mock set
  (`mock/generator/data.py`, `mock/EXPECTED.md`, PR #141) gets planted cases for every forensic test; acceptance
  for every test is that it flags exactly the planted rows.
- **OWNER DECISION (2026-11-28): the web UI is the main interface.** Every forensic test has a place in the UI
  (the Forensic screen, inputs on Settings) through the same functions as `fin forensic ...`.
- **OWNER DECISION (2026-11-28): user-kept inputs.** Approval limits, holidays and keyword lists are kept on the
  Settings screen, and a test without its input reports that it cannot run and what it needs: "ok".
- **OWNER DECISION (2026-11-28): conservative defaults, tuned later, with logs.** "yes tune later but keep logs":
  every forensic run logs, per test, the thresholds used, the population size, the flagged count and how many rows
  fell just outside a threshold, through the run log (`fin.audit.logger`) with masked client fields, so tuning after
  the second client run has the numbers without running again. `fin eval bundle` carries these counts (SPEC-18).
- **OWNER DECISION (2026-11-28): GSTR-2B is built.** "if u need form gstr2b for fomatting its easily available on the
  web": SPEC-20 includes a GSTR-2B profile from the published Excel layout, a synthetic mock file in that layout and
  the purchase register against GSTR-2B reconciliation, beside the four GST reconciliations already built. It is not
  backlog.
- The plan's rules stand: no scores or ratings; a flag is the company's to explain and becomes a query or a finding
  the user approves; deterministic SQL and Python only; masked fields stay tokens in everything printed.

## Feasibility questions

1. **Does `particulars` hold the counter-ledger?** Not reliably. fin stores the cell as filed
   (`src/fin/diligence/tally.py: ledger_entries`, `templates/profiles/tally_ledger_vouchers.toml` maps it as text)
   and nothing checks it is a ledger. In the mock set a voucher with several counter-ledgers (a sales voucher: party,
   revenue, two GST ledgers) shows one of them (`mock/generator/sources.py: _particulars`, the first other ledger).
   **Reshaped:** the pair and set-off tests do not read `particulars`; they read vouchers rebuilt from every ledger
   block (question 2).
2. **Can vouchers be rebuilt?** Yes, for an export of all ledgers. Each voucher appears once in the block of each
   ledger it touches, so the lines of one file with the same voucher type, number and date are one voucher
   (`ledger_entries` keeps `file_id`, `voucher_type`, `voucher_no`, `entry_date`: `db/schema/sources.sql`). The
   type is in the key because numbers repeat across types; the file and date because they repeat across years. A
   rebuilt voucher whose debits do not equal its credits (a ledger left out of the export, or two vouchers sharing
   type, number and date with different amounts) is "not whole" and left out of the voucher tests, counted in the
   status. Two balanced vouchers sharing a number (the mock's two `GST-SETOFF` journals a month) merge into one
   balanced voucher; the pair test reads them as one, which changes no pair.
3. **Does `diligence/bank.py` check every row's balance?** No. It checks the statement: opening + credits - debits
   against closing (`Statement.computed_closing`, `ties`, `tie_findings`); `bank_checks.py` checks overlap and the
   gap between statements. Each row's balance is stored (`bank_lines.balance`, NULL when the row has none), so the
   row chain can run (SPEC-19).
4. **Is the original PDF reachable?** Yes (`source_files.archive_path`); the PDF file checks are skipped by the owner.
5. **pdfplumber metadata and fonts on bank PDFs?** Not checked: skipped by the owner; the repo has no bank PDF, only a
   saved parse (`tests/fixtures/bank/llamaparse_axis_response.json`).
6. **`forensic_flags` or `recon_items`?** A new table (reading below).
7. **Which tests need a real name?** None that is built. Register parties are tokens and the books and bank text are
   as filed ([masking.md](masking.md)); a test that joins a register party to a ledger or bank line does it through
   `mask_values` in `fin.diligence.mask.Parties`, as the reconciliations do, and prints the token and the ledger
   name. Payroll names are masked and no built test uses them (question 12).
8. **Minimum populations.** Reading below. Below the minimum a test prints "too few entries: N of M needed" and
   flags nothing.
9. **Holidays offline.** No source in the repo or its dependencies. Reading below: a list the user keeps.
10. **pptx from `fdd.qmd`?** Backlog: `fdd.qmd` has 20 sections of long tables; slides would need their own shorter
    template.
11. **Which tests would flood a real ledger?** Round amounts (the mock set's vouchers are all multiples of 1,000;
    rent, salaries and hosting are round in real books too), weekend dates (month-end entries are dated the last day
    whatever the weekday), entries near the year end (depreciation, gratuity and tax provisions every year), duplicate
    lines (two equal salary or fee payments on a day), rarely used ledgers (every one-off expense) and own-account
    transfers (sweeps). Defaults below make each flag little; all are marked for tuning on the second client run (H3).
12. **Can bank remarks link vendors and payroll names?** No. The payroll profile has no bank account column
    (`templates/profiles/sparsh_payroll.toml`); a bank line's `reference` is a cheque or transfer number
    (`bank_icici.toml`, `bank_axis.toml`, `bank_sbi.toml`); no vendor master with bank details is loaded.
    **Dropped:** "payroll names or vendors sharing a bank reference with another party".
13. **PR rows.** One PR per spec (owner): [plans/ACTIVE.md](../plans/ACTIVE.md), SPEC-18 to 21, each size XL.

## Readings

### One table, `forensic_flags`, beside `forensic_runs`

- **Decision:** new tables in `db/schema/forensic.sql`: `forensic_runs` (engagement, test, period, outcome `ran`,
  `cannot_run` or `too_few`, population, what is needed, input hash, run id) and `forensic_flags` (run, test,
  period, source file + sheet + row or page, voucher type and number, date, ledger, party token, amount, the reason in
  words, at or above the engagement's query threshold, query). A flag is handed to a query through a link table, as
  `query_items` links recon items.
- **Why:** `recon_items` compares two sources (`left_amount`, `right_amount`, `difference NOT NULL`, kinds such as
  `left_only`); most forensic tests look at one source, and a voucher gap has no amount. Forcing them into it would
  store a difference that is not one. The discrepancies list (SPEC-15) stays a list of differences.
- **How to change it:** `db/schema/forensic.sql`.

### Runs are kept; the latest one per test and period is current

- **Decision:** a run never replaces an earlier one; flags of earlier runs stay (a query keeps the flags it was raised
  on). A run whose input hash (its rows, mappings and forensic settings) no longer matches the data is shown as stale.
  No gate needs a forensic run.
- **Why:** the reconciliations keep their runs the same way (`recon_runs`); a gate on forensic runs was not asked for.

### Thresholds flag little by default

- **Decision:** every test that flags single entries flags only amounts at or above the engagement's query threshold
  (`[forensic] min_amount = "query_threshold"`), and the defaults below. All are `config.toml [forensic]`, each with
  its reason, and **marked for tuning on the second client run (H3)**.

  | Setting | Default | Why |
  |---|---|---|
  | `journal_types` | `["Journal"]` | manual entries; sales, payments and receipts are dated by invoices and the bank |
  | `round_unit` | 1,000,000 | multiples of 1,000 and 100,000 are routine (rent, hosting, retainers) |
  | `weekend_days` | `["Sunday"]` | many Indian companies work on Saturdays |
  | month-end dates | not weekend flags | month-end entries carry the last day's date whatever the weekday |
  | `period_end_days`, `reversal_days` | 5, 15 | the last days of a period; the first days of the next |
  | `limit_band` | 0.02 | within 2% below a limit the user states |
  | `rare_max_lines` | 2 | and only a ledger with no line in any other loaded period |
  | `gap_days` (bank) | 31 | a month with no transaction inside a statement period |
  | `flow_days` | 7 | money out and back, or between own accounts (3 days for own accounts) |
  | `spike_factor`, `spike_history` | 3, 6 months | a vendor's month at three times its median month |

- **Why:** "a test that would flood a real ledger must default to flagging little" (the orchestrator's brief); the
  mock set's routine vouchers (round thousands, month ends, yearly provisions) must not flag.

### Minimum populations

- **Decision:** Benford needs 1,000 lines of at least 10 rupees in a Tally group (from the period's trial balance);
  rare ledgers and unusual pairs need 500 lines, or 500 whole vouchers, in the period; a voucher series needs 20
  numbers; a vendor spike needs 6 months of the vendor. Below it the test reports "too few entries" with the count.
  Benford conformity uses the mean absolute deviation of first digits with 0.015 as the bound of nonconformity
  (the published conformity bound), a measure of the group, not of an entry.
- **Why:** the plan's open point; published Benford work treats a few hundred items as the floor and recommends
  1,000 or more.

### Holidays are the user's list

- **Decision:** `[forensic] holidays` holds dates the user keeps (per company in `[forensic.companies.<name>]`), set
  on the Settings screen or with `fin forensic set`. The default is the three national holidays with fixed dates
  (26 January, 15 August, 2 October), every year. No holiday package is added.
- **Why:** no offline source in the repo; bank and state holidays differ by state and year, so a package would be
  wrong for many companies, and fin never fetches.

### The GSTR-2B layout

- **Decision:** the profile (`templates/profiles/gstr2b.toml`) reads the B2B sheet of the portal's GSTR-2B Excel
  download (and B2B-CDNR for credit and debit notes): a preamble above a two-row header, data from row 7. Columns, by
  name: GSTIN of supplier, Trade/Legal name, Invoice number, Invoice type, Invoice Date, Invoice Value, Place of
  supply, Supply Attract Reverse Charge, Taxable Value, Integrated Tax, Central Tax, State/UT Tax, Cess, the
  GSTR-1/IFF/GSTR-5 period and filing date, and ITC Availability. The mock file is built to that layout.
- **Sources:** the GST portal's user manual for GSTR-2B
  (https://tutorial.gst.gov.in/userguide/returns/Manual_gstr2b.htm: the Excel download, its tables B2B, B2BA,
  B2B-CDNR and filters by document date and type, filing date and ITC availability); the portal's B2B table help
  (https://tutorial.gst.gov.in/contextualhelp/gstr2biff/B2B_Table_of_GSTR-2B.htm: invoice type, reverse charge,
  filing date, ITC availability); a reconciliation utility that pastes the downloaded B2B sheet "from row 7 exactly
  as downloaded" (https://gstreconcile.in/blog/gst-reconciliation-format-in-excel); a three-way reconciliation guide
  listing supplier GSTIN, trade name, invoice number and date, invoice type, taxable value, IGST, CGST, SGST, cess,
  ITC availability and place of supply (https://www.terra-insight.com/insights/three-way-itc-reconciliation-excel-india/).
- **Not verified:** no page read on 2026-11-28 prints the header row verbatim. Before the profile is final, the
  headings of one real download are checked (human task H8, headings only); a different heading is a profile edit,
  not code.
- **Matching:** the purchase register has no GSTIN column, so a 2B line meets the register by vendor (the trade name,
  masked as kind `vendor` on load, so it shares the register's token) and invoice number; amounts compared are taxable
  value. A second pass matches the lines left by invoice number and taxable value in the month, marked "vendor names
  differ" (a 2B legal name and a register trade name of one supplier); fin does not guess an alias otherwise. Like the four GST reconciliations, it is not a gate input.

### Who sets the inputs

- **Decision:** approval limits, holidays, keyword lists and thresholds are the user's: Claude sets one only when the user gives it,
  like a stated rate (SPEC-17). Each change is in the audit log with the actor, and makes the runs it feeds stale.

## Backlog

What a ranking could be, the X2 dependency and the rest: [specs/README.md](../specs/README.md), "Forensic backlog".
