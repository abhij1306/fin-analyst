# Golden evaluation: runbook for the client's Claude agent

This runbook is for a Claude Code agent working on the client's Mac. It assumes no prior context. Follow it step by
step. The goal: install fin, load the client's real documents, build a **golden set** (values a person checked in the
documents), score fin against it, review fin's design decisions, and produce a **results bundle** the client sends
back to fin's maintainers so fin can be improved.

Read `CLAUDE.md` in this repo first: it holds the rules for working with fin. The ones that matter most here:

- Use `uv run fin ...` for all data access. Every figure you state comes from `fin` output.
- **Claude does not verify its own reading.** A golden value is scored only once a person has checked it against
  the document. You may draft values; only the person marks them checked.
- Data never leaves the Mac except the results bundle, and only when the person sends it. The data folder
  (`~/FinAnalysis`), the golden sets and the eval reports hold real figures, file names and (in `mask_values`) real
  names: never paste them into a message, an issue or a PR.
- If a command fails, report its error code and run_id (the `summary:` line) and stop that step. Don't work around
  it. The codes are in `README.md`, "Error codes".

## 1. Install

```sh
git clone https://github.com/abhij1306/fin-analyst.git
cd fin-analyst
./install.sh                  # Homebrew tools, uv sync, the data folder ~/FinAnalysis, fin init, fin doctor
uv run fin doctor             # every line ok or warn; a FAIL line shows its fix
```

If the client keeps data elsewhere, `export FIN_DATA_DIR=/path/to/FinAnalysis` before `./install.sh` (and in
`~/.zshrc`). If the client wants the optional PDF add-on, ask which (README "Install"); `./install.sh --parser
docling` (local) is the usual choice. LlamaParse uploads pages to a cloud service: only if the client says so.

Without `install.sh` (tools already installed): `uv sync`, then `uv run fin init` and `uv run fin doctor`.

Record the `fin doctor` result; the bundle includes a summary of it.

## 2. Load each company's documents

Company names are lower case (`acme`, `acme-holdings`). For each company:

1. Put its statement files (Excel, PDF) in `~/FinAnalysis/inbox/<company>/`.
2. Run `/ingest <company>` (or `uv run fin ingest <company>`). Follow the `/ingest` steps: propose accounts for new
   lines, and show findings and held documents to the person. **Ask; don't decide**: mappings are confirmed in
   `/review` when the person says so; a finding is saved as is (`fin findings accept`) or fixed only when the person
   says so.
3. Pages fin could not read are listed. If the person asks, read them (`fin ingest --reading`, step 7 of `/ingest`).
   These figures count as "read by Claude" in the eval.
4. `uv run fin status <company>` shows what is loaded and what is waiting.

## 3. Open an engagement and load diligence sources (when the client has them)

Skip this step for a company with statements only.

1. `/diligence <company>`, or `uv run fin engagement open <company> --periods FY24,FY25 [--stub FY26-YTD-SEP]`
   (ask the person which years, and whether there is a current-year stub).
2. For each period, the Tally trial balance (Detailed mode) first, then its Ledger Vouchers:
   `uv run fin ingest <company> <file> --kind trial_balance`, then `--kind ledger_vouchers`. Then the other
   sources the client has, each with `--kind`: `registers` (the billing workbook), `bank` (each statement, Excel or
   PDF; a PDF needs the client's PDF add-on) and `payroll` (the snapshot; ask the person whether its amounts are
   annual or monthly, then `uv run fin payroll confirm-unit <company> <file> --annual|--monthly`). A repeated
   invoice number, a statement that does not tie, an overlap or a balance gap is a finding: show it, never fix it.
   Party names are stored as tokens (`CUST-0001`, `VEND-0001`, `LEDG-0001`). Use the tokens; never try to read
   the real names (`fin sql` refuses, E810). E807 means no column profile reads the layout: write one
   (`docs/CUSTOMISING.md`, "Add a column profile") and note it for the decisions review (step 6).
3. Map the ledgers: `uv run fin map ledgers <company>`, propose accounts, and have the person confirm them
   (`/diligence` step on ledger mapping).
4. `uv run fin reconcile <company>` runs `tb_vs_statements` for each period with a trial balance.

## 4. Build the golden set

The golden set is `~/FinAnalysis/evals/<company>/golden.csv`. It is never committed and never shared.

1. `uv run fin eval init <company>` writes it, or adds what is new to an existing one. It lists one line per output fin
   has now, with where it is printed (`source`), and leaves `expected` and `checked` empty. It never changes or
   removes a line already there, so run it again after loading more documents.
2. Columns:

   | Column | Meaning |
   |---|---|
   | `kind` | `statement` (a statement figure), `tb_ledger` (a trial balance ledger's closing balance), `tb_vs_statements` (the difference on an account in the period's latest reconciliation), `stub` (a stub figure, unaudited), `core_revenue` and `passthrough_revenue` (the period's sales register revenue of customers not on / on the pass-through list), `recast_revenue`, `recast_difference` and `recast_undated` (the period's service-period recast: revenue recognised over service periods, its difference from revenue booked by invoice date, and the value of sales invoices without service dates), `adjusted_revenue` and `adjusted_ebitda` (after accepted adjustments, by scope), `customer_revenue`, `sector_revenue`, `product_revenue`, `top_customers_revenue`, `retained_revenue`, `pvm_price_effect`, `pvm_volume_effect`, `pvm_mix_effect` (customer analytics on the sales register, see below), `bank_closing` (a bank statement's closing balance), `register_total` (the Amount in Rs total of a register sheet), `payroll_total` (the gross salary or CTC total of a payroll file), `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl` (one detail line of that reconciliation: its `ledger` is the invoice number, the ledger as stored, or the period for payroll), `query_issue` (an approved-or-later issue's amount at issue, lines' differences added without signs; `ledger` is the query id) and `query_difference` (one kind of difference in an issue, without signs; `ledger` is the query id, a space and `left_only`, `right_only` or `difference`), `bank_unmatched`, `invoice_unmatched`, `fx_line`, `bank_vs_tb` (bank matching, below) |
   | `period`, `scope` | `FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`, or the stub (`FY26-YTD-SEP`); scope `standalone` or `consolidated` (statements only) |
   | `account` | the dictionary account (`uv run fin map accounts`) |
   | `ledger` | the ledger as stored: the token for a party ledger; for `bank_closing` the account number, with `period` the statement's end date (`2025-04-30`); for `register_total` `sales` or `purchase`; for `payroll_total` `gross_salary` or `ctc` (with `period` the file's period as `fin status` shows it, e.g. `FY25` or `FY24-FY25`); for `bank_unmatched` the bank account number, for `invoice_unmatched` and `fx_line` the customer token (these take the engagement period, e.g. `FY25`) |
   | `expected` | the figure as printed in the document: `1,234.56`, `(56.67)` or `-56.67` for a negative, `-` for nil |
   | `unit` | the unit it is printed in: `rupees`, `thousand`, `lakh`, `million`, `crore` (plurals are fine) |
   | `source` | where it is printed: `file sheet!cell`, `file sheet!row` or `file page N` |
   | `checked` | `yes` once a person has checked `expected` against the source; otherwise empty (not scored) |
   | `note` | free text; `fin eval init` notes where fin adds several printed lines into one account |

   What each kind's value is:
   - `statement`: the line as printed, with its sign as printed.
   - `tb_ledger`: closing debit less closing credit: a debit balance positive, a credit balance negative.
   - `tb_vs_statements`: the trial balance ledgers mapped to the account, rolled up, less the audited standalone
     statement figure. This one the person works out from the two documents.
   - the four reconciliation kinds: the line's difference, register less books (sales and purchases: an invoice's
     Amount in Rs less the Ledger Vouchers amount on the revenue or purchase ledgers, same number or same party and
     amount), Ledger Vouchers movement less trial balance change (`gl_vs_tb`), payroll CTC less employee expense
     (`payroll_vs_gl`). The person works it out from the documents.
   - `bank_closing`: the closing balance printed on the statement, in the account's own currency (INR or USD, unit
     `rupees`), whether or not the statement ties.
   - `core_revenue`, `passthrough_revenue`: the sum of the sales register's Amount in Rs (before GST) for invoices dated in
     the period, for customers not on, and on, the user's pass-through list. The person adds them from the register.
   - `linked_purchase_cost`: the Amount in Rs of the purchase register lines whose Purchase Id is on a pass-through
     sales line dated in the period, each Purchase Id counted once, whatever the purchase's own date.
   - `recast_revenue`, `recast_difference`, `recast_undated`: from the sales register's service start and end dates (Amount in Rs,
     before GST, spread over the service days by month): the revenue recognised in the period's months, the same less the
     Amount in Rs of invoices dated in the period, and the Amount in Rs of the period's invoices with no service dates (nil when
     none). The person works them out from the register.
   - `adjusted_revenue`, `adjusted_ebitda`: the statement's revenue (or EBITDA) plus the revenue effects (or the EBITDA
     effects) of the adjustments the user accepted; the person works it out from the statements and the accepted adjustments.
   - `register_total`: the sum of the Amount in Rs column (before GST) over every invoice row of the sales or the
     purchase sheet, the total row left out, in rupees. Duplicate invoice rows count: fin keeps both.
   - `payroll_total`: the sum of the gross salary or of the CTC column over the employees of the snapshot, the total
     row left out, annual rupees as in the file (a blank cell adds nothing).
   - `bank_unmatched`: the receipts (credits) of the bank account that no invoice matched, summed in the account's
     currency: the person works it out from the bank statement and the sales register. `invoice_unmatched`: the
     customer's invoices (Amount in Rs) with no receipt. `customer_received`: the customer's invoices (Amount in Rs)
     that a receipt matched, whether or not the amounts agree. `fx_line`: the customer's FX gain (positive) or loss in
     rupees (receipt as booked in Ledger Vouchers less the invoice's Amount in Rs), 0 if none. `bank_vs_tb`: the
     bank statements' closing balances less the trial balance's cash and bank ledgers.
   - customer analytics, all on the sales register's Amount in Rs (before GST), in rupees; "core" is customers not on the
     pass-through list. `customer_revenue`: a customer token's revenue in the period (`ledger` is the token).
     `sector_revenue`: core revenue of customers whose sector the user confirmed (`ledger` is the sector).
     `product_revenue`: core revenue of the product as written in the register (`ledger`). `top_customers_revenue`: core
     revenue of the largest 1, 5 or 10 customers (`ledger` is 1, 5 or 10). `retained_revenue`: this year's core revenue from
     customers who also had positive core revenue the year before. `pvm_price_effect`, `pvm_volume_effect`,
     `pvm_mix_effect`: for products with a quantity and a rate in both years, price effect = revenue less quantity times last
     year's price (revenue / quantity); volume effect = last year's revenue times (the volume index at last year's prices
     less 1); mix = the rest of the change. The person works these out from the register.
   - `stub`: the stub trial balance ledgers mapped to the account, as the statements show it (income and
     liabilities positive).
3. Choose what to check. About 30 `statement` lines per company is a good start, spread over:
   - every statement (balance sheet, profit and loss, cash flow), both scopes when there are both, every year loaded,
     quarters and halves if loaded;
   - totals and the lines under them; lines from PDF pages and from Excel; figures read by Claude or the PDF add-on;
   - every line whose note says fin adds several printed lines into the account (see section 8, "Which line is the
     total");
   - with diligence sources: some `tb_ledger` lines (party tokens and others), the `tb_vs_statements` lines that show
     a difference, the stub lines, the `bank_closing` line of each bank statement, the `register_total` lines of each registers
     file and the `payroll_total` lines of each payroll file.
   You may delete lines nobody will check, or leave them: an unchecked line is not scored.
4. **Draft the expected values from the documents, never from fin.** Open the file at `source` (Read tool: Excel
   cells, or the PDF page as an image) and copy the figure exactly as printed, with its unit. Do not run `fin sql`,
   `fin statements` or `fin audit` for these lines first: the golden value must be independent of what fin read.
   When `source` points at the wrong place, find the figure in the document and correct `source`.
5. **The person checks.** Give the person the list of drafted lines (period, account or ledger, expected, unit,
   source). The person compares each with the document. Write `yes` in `checked` only on the lines the person says
   they checked; correct any value they correct. Never mark a line checked on your own.

## 5. Run the eval

```sh
uv run fin eval run <company>
```

It prints the score and the path of the report (`evals/<company>/eval-<run_id>.md` and `.json`, in the data
folder). Each checked line is compared exactly, in rupees after unit conversion, with fin's value now:

- `pass`: equal; `fail`: different; `missing`: fin has no value (the figure was not read, not mapped, or its
  document is held); `unchecked`: not scored.
- Score: passes out of the scored lines (pass, fail and missing).

The report also counts: error codes in the company's runs, findings by code (open and accepted), unread pages by
code, figures read by Claude (document loaded or held, and the latest `fin verify` outcome on each), figures read
by the PDF add-on, printed lines with no confirmed account, mapping items waiting, and ledgers by mapping status.

Show the person the score and each line that does not pass, with both values and the source. A failure is a
question, not something to fix quietly:
- fin misread the figure, or mapped it to the wrong account: that is fin's error. Fix it only the usual way
  (`/review`, `fin map set` and `fin ingest --force`) when the person says so, and keep the report from before the
  fix: it is the evidence of the problem.
- The golden value is wrong: correct it with the person, and run the eval again.
- The document itself does not add up: that is a finding (`fin findings list`), not an eval failure to fix.

## 6. Review the decisions and write suggestions

`docs/decisions/*.md` record how fin's builders read the plan where it left a choice (how a figure is computed,
matched, rolled up, thresholded or labelled). The maintainers want to know which ones do not fit the client's
documents.

1. Read every file in `docs/decisions/`.
2. For each decision the eval or the client's documents bear on, write an entry in
   `~/FinAnalysis/evals/decisions-review.md`:
   - **Decision**: the file and the entry's title;
   - **Suggestion**: keep it, or what to change;
   - **Evidence**: what in the eval shows it (the check kind, account, period, outcome, error codes, counts). Use
     tokens and accounts, never names, file names or figures;
   - **Who**: whether the person agrees (ask them).
3. Add any problem the eval showed that no decision covers (a layout fin could not read, a profile you had to write,
   a step of this runbook that did not work).

## 7. Produce and share the results bundle

```sh
uv run fin eval bundle                    # every company; or: uv run fin eval bundle <company>
uv run fin eval bundle --with-figures     # only if the person agrees to share figures
```

fin prints the two file paths only (`evals/bundle-<run_id>.md` and `.json`). The bundle holds the fin version and
git commit, a `fin doctor` summary, and per company (named `company-1`, `company-2`, ...) the score, every line not
passing (kind, period, scope, account or ledger token, outcome, the kind of source location, related codes) and the
counts. It holds no real names: no company, party or file names, no labels, no source text. Without `--with-figures`
it holds no absolute figures, only each failing line's relative difference. fin refuses to write a bundle that would
contain a masked party, company or file name (E833).

Then:
1. Show the person both files and `decisions-review.md`. Read them yourself for anything identifying (a name in a
   decisions-review entry, for example) and remove it.
2. The person sends the three files to fin's maintainers. You never send anything.

## 8. What the eval should reveal

These were open questions about extraction, waiting for real documents. The eval answers them; mention what you see
in `decisions-review.md`.

### Rules for extraction changes

- Extraction changes are driven by real documents in a golden set, not invented ones. Each change needs a real file
  (in the data folder, never committed) and its checked figures.
- Prefer deleting code to adding it: keep the rules the golden set exercises and remove the ones it never needs.
- A fix makes fin either read the document correctly or say it could not read it, so that Claude reads it. Loading a
  wrong label or value and reporting success is the one outcome that is never acceptable. **A `fail` on a figure fin
  loaded without any warning is the most important thing the eval can show.**
- `src/fin/extract/pdf.py` has grown layout rules one test document at a time (wrapped labels, partial rows, nil
  headings, notes pages, results-page numbering). Once enough real annual reports and results filings are in golden
  sets, the plan is to record which rules they exercise, remove the rest, and replace fragile ones with "page not
  read" so Claude reads the page.

### Which line is the total when several lines map to one account

`document_facts` (`src/fin/db/schema/company_views.sql`) gives one value per document, account, period and scope.
When a document maps several lines to the same account:
- with three or more lines, a line equal to the sum of the others is taken as their total, and the others are
  dropped;
- with two lines, a line is the total only when the document marks it: its label starts with "Total", or the other
  line sits under it.

Each choice fails in a different case. The current rule drops genuine lines that add up by coincidence (current tax
beside MAT credit and earlier years' tax, where one equals the sum of the other two); the subtotal check usually
then holds the document with an E402. The alternative, "total only with evidence, for any number of lines" (built and
reverted on 2026-11-09), counts twice a total printed on its heading row above its parts with no "Total" label.

To answer it: check every `statement` line whose note says "fin adds N printed lines into this account". Report how
many there were, and how many passed and failed.

### Layouts seen on synthetic documents only

A dry run on 2026-11-09 with documents made for the purpose showed what fin does with these layouts. They are not
bugs until a real document shows them. If a client document has one, the eval shows it: report the layout (not the
document) in `decisions-review.md`.

| Layout | What fin did | Kind |
|---|---|---|
| Excel: board signature block under a statement, in the figure columns | Whole file failed, E204 | Whole-file failure |
| Excel: SEBI results sheet titled "Financial Results" / "Statement of Assets and Liabilities" (the PDF reader knows these titles) | Whole file failed, E201 | Whole-file failure |
| Excel: a sheet with no period-date header that mentions "profit" (a five-year summary) | Whole file failed, E202 | Whole-file failure |
| Excel: "Sr. No." column typed as text left of the label column | Rows loaded with labels '3', '5', '7' | Silent wrong label |
| PDF: label wrapped over two lines with the figures on the second | First line kept as a section heading, row labelled with the last line ('11)', '+ C)') | Silent wrong label |
| PDF: last row of a table printed as plain integers (`45`) | Row dropped, no unread warning | Silent drop |
| PDF: "3 months ended" / "6 months ended" written with digits | Pages not read, E202 | Flagged, Claude reads |
| PDF: standalone and consolidated balance sheets side by side on one page | Page not read, E202 | Flagged, Claude reads |
| PDF: directors' report summary table and segment page | Listed as unread statement pages | Spurious warning |

A silent wrong label or silent drop shows as `missing` (or as an unmapped line in the counts) with no unread page; a
whole-file failure shows in the error-code counts.

## 9. Later phases

Diligence phases still to come (reconciliations of registers, bank and ledgers, adjusted EBITDA; `docs/DILIGENCE_PLAN.md`)
add their own golden check kinds as they are built (`docs/CUSTOMISING.md`, "Eval checks"). Run `fin eval init`
again after updating fin: it adds lines for the new kinds and keeps the checked ones.
