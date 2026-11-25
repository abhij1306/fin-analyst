# Error codes

When a command fails it prints an error code and a run id; Claude reports both. Exit codes: 0 success (held
documents and findings included), 1 finished but some files failed, 2 the command could not run, 3 refused until you
confirm. Every run is logged in `~/FinAnalysis/logs/<date>/<run_id>.jsonl`.
A command-line usage error (a wrong option, argument or value, exit 2) is reported by the parser before a run starts, so it has no error code, run id or log.

| Code | Meaning | What to do |
|---|---|---|
| E001 | A required program (uv, node, quarto), `config.toml` or the audit database is missing, or a program cannot be started | Run the fix `fin doctor` shows (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database was made by an older fin | Run the fix shown (`brew upgrade`, correct `config.toml`, or move the data folder away and run `uv run fin init`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or fix the folder permissions |
| E005 | `fin update` stopped: local changes in this folder, or a git, uv or npm step failed; or the `uv sync` of `fin parser use` failed | Commit or discard local changes, or fix the failing step, and run it again. The backup is in `backups/<run_id>/` |
| E101 | File is unreadable or corrupt | Replace it with a good copy and ingest again. The file is in `review/<company>/` |
| E102 | File is password protected | Save an unprotected copy and ingest that |
| E103 | Unsupported file format | Convert it to `.xlsx` or a text PDF |
| E104 | Scanned PDF (no text layer); fin cannot read it | Get an Excel or text-PDF version, or ask Claude to read the pages |
| E105 | The same file is already loaded | Nothing to do. Use `--force` to load it again as a new version |
| E201 | No tables found in the file | Check the file has statement tables; read the reason in `review/<company>/` |
| E202 | Fiscal periods or column headers not detected | Fix the headers in the file, or ask Claude to read the pages |
| E203 | Unit (crores, lakhs, ...) not detected | Add a unit line such as "Rs. in Crores" to the file, then ingest again |
| E204 | Parse error while reading the file | Read the reason in `review/<company>/<file>.error.json` and fix the file |
| E205 | A Claude reading file cannot be used (missing columns, unknown statement or scope, bad figure, date or source) | Fix the line named in the message and run again |
| E206 | The PDF add-on (Docling, LiteParse or LlamaParse; the message names it) failed on the pages pdfplumber skipped, could not download its models, or (LlamaParse) found no `LLAMA_CLOUD_API_KEY`; a warning, the pages stay unread | Read the warning in the run log; `uv run fin doctor`; set the key; or ask Claude to read the pages |
| E301 | A line is not in the chart of accounts | Loaded as unconfirmed. Run `/review` to confirm a mapping |
| E302 (warning) | A proposed mapping has low confidence; a warning, not an error | Check it in `/review` |
| E303 | A line maps to conflicting accounts | You decide in `/review` |
| E304 | A mapping proposal or review request cannot be applied (unknown account, wrong statement, unknown id, invalid new account) | Nothing was applied. Check `/review` and the account name, then retry |
| E401-E405 | Findings, not errors: balance sheet (E401), subtotal (E402), cash (E403), documents disagree (E404), unit factor (E405) | The document is held, not rejected. See [Findings](#findings) |
| E406 | A finding to accept does not exist or is not open | Nothing was accepted. Check the ids with `uv run fin findings list <company>` |
| E501 | A database is in use by another `fin` command | Wait for it to finish and run again |
| E502 | `fin sql` without `--write` was given something other than one SELECT | Use `--write` only for a change you asked for |
| E503 | A database could not be created from its schema file | Report the error |
| E504 | A SQL statement failed (syntax, unknown column, constraint) | Nothing changed. Fix the statement |
| E601 | Report render failed | The last good report is kept. Run `uv run fin doctor` (Quarto) and try again |
| E602 | The report narrative contains hand-typed numbers | Replace each listed number with a `{{metric:...}}` or `{{fact:...}}` reference, or reword it |
| E603 | Dashboard build failed | The last good build is kept. Run `uv run fin doctor` (node) and try again |
| E604 | An evidence source file is missing from the archive, cannot be opened, or has no page a figure names | The page is still produced and flags it. Restore the original into `archive/` |
| E701 | The action needs your confirmation | `fin delete` run by hand exits 3 when the typed name does not match; Claude uses `--yes` when you asked |
| E801 | A diligence gate this command needs is not approved: never approved (open), or its data changed since it was approved (reopened) | Nothing changed. See what the gate needs with `uv run fin gate status <company>`; once its checklist passes, you approve it with `uv run fin gate approve <company> <gate>` |
| E802 | A gate cannot be approved: its checklist fails, or an earlier gate is not approved | Nothing approved. Fix the failing checklist lines shown (or approve the earlier gate first), then approve again |
| E803 | No open engagement for the company, or one is already open | Open one with `uv run fin engagement open <company> --periods ...`, or close the open one first |
| E804 | An adjustment cannot be proposed: no evidence (query, finding or source), a category not in `config.toml`, a period not in the engagement, an unknown account, effects that do not add up (EBITDA effect not revenue effect minus cost effect), or a reclassification that is not valid | Nothing changed. Add the evidence or correct the value and run again |
| E805 | An unknown query, adjustment or finding id, or a status change that is not allowed | Nothing changed. Check the ids and statuses with `uv run fin query list` or `uv run fin adjust list` |
| E806 | `fin sql --write` was given a statement that writes to a diligence table (`engagement`, `accountant_queries`, `adjustments`, `gate_approvals`, `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements`, `bank_lines`, `payroll_lines`, `payroll_units`, `ledger_accounts`, `customers`, `recon_runs`, `recon_items`, `recon_match_lines`, `match_proposals`, `passthrough_customers`, `query_items`) | Nothing was run. Make the change with `uv run fin engagement`, `fin query`, `fin adjust` or `fin gate`, load the source file again with `fin ingest --kind`, map ledgers or customer sectors with `fin map ledgers|customers` and `fin review --ledgers|--customers`, or run `fin reconcile` again |
| E807 | `fin ingest --kind`: the column profile named is unknown or invalid, or no profile matches the file's headers | Nothing loaded. The message lists the header lines found: write a profile in `templates/profiles/` (docs/development.md) and ingest with `--profile` |
| E808 | `fin ingest --kind`: the export's header gives no period, or a period that is not a year of the open engagement nor exactly its stub; or `fin engagement open --stub` names a stub that is not the year to date of the fiscal year after the last period | Nothing loaded or opened. Check the export's period, or the engagement's periods and stub (`uv run fin engagement status <company>`) |
| E809 | A trial balance with group rows only (Tally's Condensed export) | Nothing loaded. Export the trial balance again in Detailed mode, with ledgers, and ingest that |
| E810 | `fin sql` was given SQL naming `mask_values` (the real names behind the tokens), or calling `query`, `query_table` or `read_duckdb`, which could reach it | Nothing was run. Work with the tokens; fin never prints the real names |
| E811 | Ledger Vouchers ingested before their period's trial balance | Nothing loaded. Ingest the period's trial balance first (it says which ledgers are parties), then the Ledger Vouchers |
| E812 | `fin reconcile`: the period is not a year of the engagement (the stub has no audited statement), or it has no loaded trial balance or no audited standalone statement figures | Nothing recorded. Load the period's trial balance and statements, or see the stub's figures in the view `stub_figures` |
| E813 | `fin ingest --kind registers`: a register row is not an invoice line: no invoice number, date, party or Amount in Rs, a date not in the profile's date format, or a row the profile does not skip (a total) | Nothing loaded. The message names the file, sheet!row and what is missing: fix the cell, or add the row's name to `[rows] skip` in the profile |
| E816 | A PDF bank statement cannot be read: `[pdf] parser` is `none`, its add-on is not installed, the parser could not read a page, or it found no table | Nothing loaded. Choose a PDF add-on (`uv run fin parser use docling\|liteparse\|llamaparse`; a local one keeps the pages on this Mac) and ingest again |
| E817 | A bank statement's header lines give no account number, or no opening or closing balance when its first or last row has no balance to stand in for it | Nothing loaded. Check the header lines against the profile's `[header]` patterns in `templates/profiles/bank_*.toml` (docs/development.md) |
| E818 | The saved parse of a bank PDF (`cache/bank/<sha256>.json` in the data folder) cannot be read | Nothing loaded. Delete the file to parse the PDF again |
| E840 | `fin reconcile`: a reconciliation (`sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`) cannot run: a period not in the engagement, Ledger Vouchers, registers covering the period, a trial balance or a payroll file (unit confirmed annual) not loaded, or no ledger confirmed to the account the books side reads (revenue, purchases, employee expense) | Nothing recorded. Load or map what the message names, then run it again |
| E841 | `fin query raise --items` names a reconciliation line that does not exist, is not of the open engagement, or is not in the latest run (the data was reconciled again) | Nothing raised. Take the line ids from the latest `fin reconcile` output |
| E858 | `fin query export`: the open engagement has no approved query | Nothing written. Approve drafts first: `uv run fin query approve <company> <ids>` |
| E859 | `fin query export`: a source workbook a query cites cannot be read from the archive, or the export folder cannot be written | Nothing left in the export folder. Check the archive file and the data folder's permissions, then export again |
| E860 | `fin query answer --file`: the reply file is missing, cannot be read or archived, or is not plain text (.txt, .eml, .md) and no `--text` says what it answers | Nothing recorded. Give `--text` with the answer (names as tokens), or a plain-text reply |
| E861 | A query's wording names a token (`CUST-0001`, ...) with no real value in the company's masked names: refused by `fin query raise` and `fin query edit`, and by `fin query export` for a wording or line that reached it | Nothing raised, changed or written; the token is named. Use a token fin printed (`fin sql` on `register_lines`, `ledger_entries` or `bank_lines`) |
| E881 | `fin query raise --period`: a period that is not a year of the open engagement nor its stub | Nothing raised. Use a period `uv run fin engagement status <company>` shows |
| E842 | `fin query raise --items` names a line that cannot be queried: matched, fx, not reconciled, unmapped, a month or party roll-up, below the threshold, already in a query, or lines of different periods | Nothing raised. Raise lines at or above the threshold, one period at a time |
| E820 | `fin payroll confirm-unit` names no loaded payroll file of the open engagement (unknown name or id, not a payroll file, or superseded by a later snapshot, or several loaded files with that name) | Nothing recorded. The message lists the loaded payroll files: use one's file name or file_id |
| E821 | A payroll snapshot row has no employee name or code, or a joining or resign date that is not a date in the profile's format | Nothing loaded. Ask the company for a corrected file, or fix the profile's `date_format`, and ingest again |
| E830 | `fin eval run`: the company has no golden set | Make one with `uv run fin eval init <company>` ([docs/client-handoff.md](docs/client-handoff.md)) |
| E831 | The golden set cannot be read: a missing column, an unknown kind, scope, account or unit, a figure that is not a number, a checked line with no value or source, or the same check twice | Nothing scored or changed. Fix the lines the message lists in `evals/<company>/golden.csv` |
| E832 | `fin eval bundle`: no company database to evaluate, or none for the company named | Nothing written. Load the company's documents first |
| E833 | `fin eval bundle`: the results would contain a real name (a masked party, a company or a file name) | Nothing written; the name is not printed. Report the run id: a bundle must never carry names |
| E845 | `fin engagement passthrough`: a customer token that is not on a loaded sales register, or no sales register is loaded | Nothing changed. Load the registers (`fin ingest <company> <file> --kind registers`) and use a token from `SELECT DISTINCT customer FROM register_lines WHERE kind = 'sales'` |
| E854 | `fin reconcile <co> match --propose`: the file is not a JSON list of matches, a match has no bank lines or invoices, names a line that is not a loaded bank credit or sales invoice line, has no reason, or a confidence outside 0 to 1 | Nothing stored. Fix the file; take the `[file_id, sheet, row]` of each line from `fin sql` on `bank_lines` and `register_lines` |
| E855 | `fin review confirm|reject --matches`: no match proposal with that id, or it is no longer waiting | Nothing changed. List them with `fin review list <co> --matches --all` |
| E873 | `fin report <co> --diligence`: the company has no engagement | Nothing written. Open an engagement and load its files (`/diligence`), then run it again |
| E874 | `fin report <co> --diligence`: the narrative has a `{{figure:<name>:<period>}}` reference to a figure the report does not have (or one that needs a stub or a period the engagement has not), or a period outside the engagement | Nothing rendered. The message lists each reference; the narrative stub names the figures the report has |
| E868 | `fin map customers` or `fin review ... --customers`: a proposal that is not an object with customer, sector, confidence and reason, a customer that is on no loaded sales register, an empty sector or the sector `unmapped`, a confirmation with no sector, a confirmed customer proposed again, or an unknown customer id | Nothing changed. List the customers with `fin map customers <company>` and fix the proposal |
| E869 | `fin analytics`: the company has no engagement, or no sales register line in an engagement period | Nothing shown. Open an engagement and load the registers (`fin ingest <company> <file> --kind registers`) |
| E863 | `fin recast run`: a period that is not an annual period of the open engagement, or no loaded sales register line to recast | Nothing recorded. Load the sales register (`fin ingest <company> <file> --kind registers`) and use a period of the engagement |
| E864 | `fin recast contracts`: the contract reading cannot be used (a missing column, a bad date, amount or page, an end before its start, no rows) | Nothing recorded. Fix the CSV lines the message names |
| E865 | `fin recast contracts`: a contract names a customer or invoice number that no loaded sales register has | Nothing recorded. Use the customer's token (`SELECT DISTINCT customer FROM register_lines WHERE kind = 'sales'`) or the invoice number as in the register |

The code that raises each error is in `src/fin/audit/errors.py`.
