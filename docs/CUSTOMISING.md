# Customising fin-analyst with Claude Code

Keep changes small. Every change needs a test, and a `PLAN.md` update if it changes behaviour (`CHANGELOG.md` is written at release).
Rules are in `CLAUDE.md`. Local checks: `uv run ruff check <files>`, `uv run ruff format <files>`,
`uv run pytest <test files>`.

## Module map (`src/fin/`)

- `cli/`: the `fin` commands (Typer), one module per group: `core.py` (the root `app`, `--version`, shared argument checks), `init.py` (init, doctor), `ingest.py` (ingest, ingest-batch, verify), `findings.py` (findings, status), `statements.py` (statements, export), `data.py` (sql, dashboard, audit, delete, update, report), `evals.py` (eval init, run, bundle). Each command runs through the audit logger. To add a command group, make a module that does `from fin.cli.core import app` and decorates its commands, then add it to the import line in `cli/__init__.py` (the order is the order in `fin --help`).
- `config.py`, `paths.py`: `config.toml` settings, and every data-folder path.
- `audit/errors.py`: error classes and codes (E001 to E833). `audit/logger.py`: run records and logging.
  `audit/trace.py`: `fin audit`.
- `db/conn.py`: the only way to open a database; `db/rows.py`: query results as dicts. `db/schema.py`: creates a
  database from its schema file.
  `db/schema/<kind>.sql`: the schema (`audit`, `dictionary`, `analytics`, `company`; the company metric views are
  in `company_views.sql`, the diligence tables in `diligence.sql` and its views in `diligence_views.sql`).
- `ingest.py`: loads one document in one transaction (stage, check, commit, or roll back on an error).
- `findings.py`: findings (what does not add up in a filing): `VALIDATORS` (the checks run on every staged
  document), holding a document with open findings, `fin findings list|accept`, and the re-check of held documents
  after a fix. `status.py`: `fin status`.
- `extract/`: `excel.py`, `pdf.py`, `text.py` (notes as Markdown), `units.py` (crores, lakhs, ...),
  `periods.py` (period names: `FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`, and how a column's length is read),
  `reading.py` (reading files: figures Claude read from pages fin could not read, for `fin ingest --reading`).
  `profiles.py`, `ledger.py` and `registers.py`: column profiles (`templates/profiles/*.toml`) and the tabular
  readers for diligence source files (header, period line, rows with sheet and row; `registers.py` finds the sales
  and purchase sheets of a billing-system workbook).
  The optional PDF add-on (one of three uv extras, `[pdf] parser` in `config.toml`): `fallback.py` is ingest's PDF
  reading (pdfplumber, then the chosen engine on the statement pages `pdf.py` skipped, and on short scanned PDFs
  when fin uses the engine's OCR, `OCR = True`: not LiteParse);
  `addon.py` runs an engine once per document on just those pages and reads its result; `docling_run.py`,
  `liteparse_run.py` and `llamaparse_run.py` are the engines' adapters, each giving `pdf_tables.Table` grids (boxes in
  fin's top-left points); `pdf_tables.py` reads those grids into `PdfValue`s with `pdf.py`'s and `periods.py`'s
  helpers and guards, the same for every engine (no engine needed, so its tests are fast). `fin parser use <name>`
  (`parser.py`) installs and records the choice.
- `verify.py`: `fin verify`, compares a Claude reading with what is loaded for a document, records the run
  (`verify_runs`, `verify_lines`) and its disagreements as findings.
- `diligence/`: a diligence engagement on one company (`docs/DILIGENCE_PLAN.md`). `engagement.py` (`fin engagement
  open|status|close`, the open-engagement check (E803), and `record_event`, which appends every change to the audit
  table `diligence_events`), `queries.py` (accountant queries and their status flow, `TRANSITIONS`),
  `export.py` with `export_data.py`, `export_xlsx.py`, `export_docs.py` (`fin query export`: the one reader of
  `mask_values`; E858–E861) and `answers.py` (`query answer --file`),
  `adjustments.py` (adjustments to EBITDA, revenue and costs with evidence, E804; `resolve_effects` makes the EBITDA effect revenue minus cost), `passthrough.py` (`fin engagement passthrough`: the user's pass-through customers, E845; the views are `db/schema/diligence_split_views.sql`), `customers.py` (customer sectors: sync, propose, confirm, reject, E868; `fin map customers`, `fin review --customers`), `analytics.py` (`fin analytics`, E869; the views are `db/schema/diligence_analytics.sql`; the report section is `report/customers.py`), `bridge.py` (`fin engagement bridge`: reads and
  formats the EBITDA bridge and EBITDA-to-cash walk views), `gates.py` (`fin gate status|approve`: the `GATES`
  registry with each gate's checklist and inputs, the approved/reopened/open states, `require` (E801) and
  approval (E802)), `cli.py` (the `fin engagement`, `fin query`, `fin adjust` and `fin gate` Typer groups). Tables: `db/schema/diligence.sql`; views: `db/schema/diligence_views.sql`.
  Source files (`fin ingest --kind`): `sources.py` (one file in one transaction: period check, archive,
  `source_files`, supersede), `tally.py` (what trial balance and Ledger Vouchers rows mean: groups, ledger blocks,
  the group-sum finding), `mask.py` (tokens for names, `mask_values`), `payroll.py` (the payroll snapshot:
  rows, snapshot period, the monthly-amounts check, `fin payroll confirm-unit`, G1's payroll line; the CLI is
  the group-sum finding), `mask.py` (tokens for names, `mask_values`), `bank.py` (`--kind bank`: a bank statement as
  a `Statement`, the tie check, the load), `bank_pdf.py` (a PDF statement: the chosen PDF add-on's tables, saved
  once per sha256 in `cache/bank/`, read with the same profiles), `bank_raw.py`, `bank_checks.py` (overlap and
  balance gap findings, G1's bank line). Tables: `db/schema/sources.sql`.
  `ledgers.py` (`ledger_accounts`: Tally ledger to account, proposed by Claude, confirmed in review; an account's
  side), `recon.py` (`tb_vs_statements`: `recon_runs`, `recon_items`, draft queries at or above the threshold,
  E812; CLI `cli/reconcile.py`), `stub.py` (the current-year stub, `--stub FY27-YTD-SEP`), `tb_checks.py` (gate
  G1's trial balance checklist lines and inputs).
- `reconcile/`: the D4 reconciliations, one function each: `registers.py` (sales and purchase register vs Ledger
  Vouchers), `gl_vs_tb.py`, `payroll_vs_gl.py`; `base.py` (a line, the books side), `store.py` (`recon_runs`,
  `recon_items`, keeping a query on an unchanged line), `items.py` (`fin query raise --items`), `checks.py` (gate
  G2's lines), `format.py`. To add one: a `compute(con, company, engagement, period) -> Computed` and an
  `applies`, registered in `reconcile/__init__.py` `RECONCILIATIONS`, its name in the `recon_runs` CHECK, and its
  golden eval kind (`evals/golden.py` `RECON_KINDS`). The service-period recast is a reconciliation too but is not in
  `RECONCILIATIONS` (gate G3 reads it, not G2): `recast.py` (compute), `recast_run.py` (`fin recast run`, proposes the
  adjustment), `recast_checks.py` (G3's lines), `contracts.py` (`fin recast contracts`).
- `map/`: `dictionary.py` (seed, label map, lookup by section and balance-sheet part), `normalize.py` (label
  cleaning), `review.py` and `commands.py` (`fin map propose|set|accounts`, `fin review list|confirm|reject`).
- `validate/gates.py`: the checks that run on a staged document (E401 to E405); a failed check is a finding.
- `metrics/export.py`: copies every company's metrics, metric definitions, standard statements, findings,
  coverage and unread pages to `shared/analytics.duckdb` (`TABLES`), for the dashboards.
- `statements.py`: the `standard_statements` view (made per connection from the dictionary's account tree) and
  `fin statements`. `workbook.py`: `fin export` (the Excel workbook).
- `query/`: `run.py` (`fin sql`, read, and `--evidence`), `write.py` (`fin sql --write`; refuses the diligence tables, E806), `evidence.py` (`fin sql --evidence` pages).
- `report/`: `data.py` (what the report reads, categories, charts), `tables.py` (display formatting and the
  Markdown tables), `chart.py` (the SVG line chart),
  `lint.py` (the no-typed-numbers rule), `render.py` (narrative stub, sections, Quarto).
- `evals/`: the golden evaluation (`docs/EVAL.md`): `golden.py` (the golden set format, `evals/<company>/golden.csv`,
  E830/E831), `scaffold.py` (`fin eval init`), `score.py` (`fin eval run` and its report), `counts.py` (error codes,
  findings, unread pages, Claude-read figures, unmapped labels and ledgers), `bundle.py` (`fin eval bundle`: no names,
  no figures by default, E832/E833). CLI: `cli/evals.py`.
- `delete.py`, `update.py`, `doctor.py`, `parser.py`: `fin delete`, `fin update`, `fin doctor`, `fin parser show|use`
  (the PDF add-on: uv extra, models, `[pdf] parser`).
- Outside `src/`: `templates/dictionary/` (seed CSVs), `templates/report/health.qmd`, `.claude/commands/` (slash
  commands), `.claude/hooks/` (action log), `dashboards/` (Evidence project).

## Add a column profile (a new export layout for a diligence source file)

When `fin ingest <co> <file> --kind <kind>` fails with E807, the message lists the title and header lines it found.
Copy the nearest profile in `templates/profiles/` to `<name>.toml` (lower case, digits, `_`) and change only what
differs: `[columns]` (header text as printed, joined over `header_rows` rows, to a field; the fields each kind needs
are `FIELDS` in `extract/profiles.py`), `date_format`, `period` (a regular expression with groups `start` and `end`
for the header line naming the period; a payroll profile has one group, `date`), `unit`, `[rows]` (`skip`; for a trial balance `group` and `level`; for
Ledger Vouchers `block` and `block_prefix`) and `[mask]` (`groups`: the party groups whose ledgers are masked, and
`fields`; a bank profile also has `bank`, `currency` and `[header]`: a regular expression with a group `value` for each of `account`, `currency`, `opening`, `closing`, matched against the lines around the table; the amounts stay in the account's own currency, `unit = 1`). Then ingest with `--profile <name>`. A bank profile reads Excel and PDF statements alike (`tests/bank_fixtures.py`: Excel layouts for the three banks). Add a test with a synthetic file in that layout
(`tests/tally_fixtures.py` shows how). Keep it to what the profile can state: no layout special cases in code.

A `registers` profile (`billing_registers.toml`) has no `[columns]` and no `period`: it has `[sales.columns]` and
`[purchase.columns]` (header text to field; the fields are `REGISTER_FIELDS` in `extract/profiles.py` and
`REGISTER_REQUIRED` must be mapped), `date_format`, `unit` and `[rows] skip` (total rows). List only the headers
the sheet has. Which fields are masked (customer, vendor, handler, submitted by) is fixed in
`diligence/registers.py` (`MASKED`), not in the profile (`docs/decisions/d3-registers.md`).

## Add an account or a label to the dictionary seed

- Account: add a row to `templates/dictionary/schedule_iii.csv` (`account,statement,parent,sign,unit,description`).
  The description starts with the line name statements show, up to the first `;` or bracket
  (`Other comprehensive income items; one line of ...`).
  `parent` is the subtotal it adds into and `sign` is 1 or -1; the validation gates use them. `unit` is `rupees`, or
  `rupees_per_share` for a per-share figure such as EPS: it is stored as printed (not scaled by the statement's unit)
  and cannot have a parent. `uv run fin map accounts` prints the tree.
- Label: add a row to `templates/dictionary/labels.csv` (`label,statement,section,account`). The account must exist.
  `section` is the heading the label sits under, or empty for any heading. For the balance sheet it can also be the
  part (`Non-current assets`, `Current assets`, `Non-current liabilities`, `Current liabilities`): a row under a
  sub-heading such as "Financial liabilities" is looked up under the part it is in (`map_rows` in
  `map/dictionary.py`; the part is followed per statement and scope, so side-by-side standalone and consolidated
  columns keep theirs).
- What the seed covers: Schedule III Divisions I, II and III (NBFCs), the bank Forms A and B, SEBI Reg 33 results
  and the Ind AS 7 / AS 3 indirect cash flow. NBFC and bank lines with no current / non-current split add straight
  into `total_assets` or `total_liabilities` (`loans`, `investments`, `borrowings`, `deposits`, `debt_securities`,
  ...), so the balance-sheet checks work without a current / non-current part. "Total financial assets" and similar
  NBFC subtotals, the bank appropriation lines and results memo lines (paid-up capital, face value) are accounts
  without a parent: mapped, never checked. `fixed_assets` (old format and banks) is the parent of `ppe_net`, CWIP and
  intangibles.
- Lender balance sheets. "Loans", "Investments", "Other financial assets", "Other assets", "Provisions", "Lease
  liabilities", "Other financial liabilities" and "Derivative financial instruments" mean a current or non-current
  account in a Division II statement but the unsplit lender account in an NBFC (Division III) or bank (Form A)
  balance sheet. `map_rows` decides from the whole balance sheet (per statement and scope): with no current /
  non-current part heading or part total, and a lender marker (a line only lenders have, such as "Debt securities",
  "Deposits" or "Total financial assets", or "Loans" / "Investments" under a "Financial assets" heading), its rows
  are also looked up under the pseudo-section `Lender assets` or `Lender liabilities` (by the side of the balance
  sheet the row is on) in `labels.csv`. A Division II statement always prints its part totals, so it is never
  read as a lender's. To map another lender-only line, add a `labels.csv` row with that section.
- Leave a label to review when it means different accounts in different formats and nothing tells them apart, such
  as assets held for sale: in a statement whose part heading was not read it would land in the wrong subtotal and a
  correct document would be held on an E402 finding. Map it per company (`fin map set`) instead.
- Run `uv run fin init`. It adds missing rows to the shared dictionary and never removes or changes existing ones, so
  a new account or label in the seed CSVs reaches an existing data folder this way. A changed row (a new parent or
  sign) reaches only a data folder created afterwards. `fin sql --write` cannot change the dictionary: it works on
  one company's database. For one company, map the label to another account with `fin map set` (below).
- One company only: `uv run fin map set <co> --statement <statement> --label "<label as written>" --account
  <account> [--section "<heading>"]`. It wins over the seed for that company, for files ingested from then on. This
  is also how to fix a wrong mapping behind a held document's findings (`fin findings list`): map it, then
  `fin ingest <co> <file> --force`.
- From review: when no account fits a label, Claude proposes a new one in `fin map propose` (`account` is the new
  name, `new_account` is `{parent, sign, unit, description}`). `fin review confirm` adds it to the shared dictionary
  (`accounts.source = 'review'`, with the run_id) and maps the label, in one transaction; `--name` and `--parent`
  change it, `--account` uses an existing account instead, and reject adds nothing. It then behaves like a seed
  account: `fin map accounts` marks it `[added]`, `fin map set` can target it, and the gates add it into its parent.
- Share what review added: `uv run fin map accounts --added` prints the added accounts and the global labels
  confirmed in review that the seed does not have yet, as rows for `schedule_iii.csv` and `labels.csv`. Paste them
  at the end of each file, run the tests, and commit; `fin init` then adds them on any other machine. Labels print
  normalized, which matches the same way.
- Changing `normalize.py` is safe for stored mappings: their keys are normalized again when read.
- Test: add the label to a case in `tests/unit/test_dictionary.py` or `tests/unit/test_normalize.py`.

### Sign conventions

Every figure is stored with the sign printed in the statement. The tree's signs say how lines add up: expenses are
printed positive and subtracted from income. Exceptional and extraordinary items are the exception: Schedule III
prints a charge as positive ("V - VI"), but some companies print a gain as positive and add it ("iii + iv"). E402
tries the dictionary sign first, then the opposite, and its message says "printed as a gain and added" when the
document adds them. To read the effect on profit, compare the subtotals around it (`profit_before_exceptional_items`
and the next one shown), not the sign of `exceptional_items` alone.

## Add a metric

Metrics are SQL views in each company database, so they are always current. All of a metric is in
`src/fin/db/schema/company_views.sql`; read the rules at the top of the file first (facts come from `current_facts`,
one value per account, period and scope; ratios use `div6`; nothing is guessed).

1. `metric_definitions`: add a row: name, category (`profitability`, `returns`, `liquidity`, `solvency`,
   `efficiency`, `cash_flow`, `growth`, `earnings_quality`, `per_share`, `lender`, `ttm`, `headline`, or a new
   one), unit (`rupees`, `rupees_per_share`, `ratio` or `days`), periods (`annual`, `all`, `interim` or
   `quarter`), when_missing (`null` keeps a NULL row so the report can say which input is missing; `absent` leaves
   the row out, for metrics only some companies have), its display label, whether a ratio shows as times
   (`true`: "2.35x"; `false`: a percentage), a plain-English definition and the formula in words. The row's place
   in the list is its order in the report.
2. `metric_input_accounts`: list the accounts it uses and from which period (`same`, `year-1`, `quarter-1`, ...),
   so `fin audit <co> <metric> <period>` and `metric_inputs` can trace it.
3. `metrics_wide` view: if it needs an account not read yet, add a `max(p) FILTER (WHERE account = '...')`
   line to `period_inputs` (and a join in `derived` for an earlier period). Add the formula as a column of its
   final SELECT, on integer paise (`div6` for a ratio, `rupees` for money, `growth6` / `cagr6` for growth); every
   column there becomes a row of `metrics`. `tests/unit/test_schema.py` checks that steps 1 to 3 agree.
4. The views only exist in a company database when it is created, so apply the change to existing ones too:
   re-create the data folder (`fin init` and ingest again), or run the edited `CREATE OR REPLACE VIEW` statements
   through `fin sql --write` on each company.
5. The report shows it in its category's table already. Optionally add it to `CHARTS` or `KEY_ROWS` in
   `src/fin/report/data.py`. Every metric is in `fin sql`, the dashboard and the `fin export` workbook already.
6. Test: add the expected values to `tests/fixtures/sample-financials/answer_key_analysis.json` (computed by hand
   or with Python's `decimal` from the facts in `answer_key.json`, never by running the SQL) and run
   `tests/integration/test_metrics.py`. A metric the synthetic companies cannot have (lender, quarters, EPS) is
   tested on a synthetic PDF instead, as in `tests/integration/test_standard_formats.py` and
   `tests/integration/test_ingest_results.py`.

## Add a validation gate

A validator is a function `f(run, company, con, doc_id)` that returns the findings for the staged document: a list
of dicts with the columns of the company `findings` table (`check_name`, `code`, `finding`, `statement`, `scope`,
`period`, `label`, `fin_value`/`other_value`, `fin_printed`/`other_printed`, `fin_source`/`other_source`,
`other_file`, `message`). A document with any finding is held, not rejected: what a company filed that does not add
up is for the user to see and decide.

1. Pick the next free check code (E4xx) and add a row to the error-code table in `README.md` and a line to
   its "Findings" section. Check codes are not error classes.
2. Write the check (see `validate/gates.py` for how a check reads the staged facts, records its result and builds
   its findings), and add the function to `VALIDATORS` in `findings.py`. It runs again on held documents after a fix;
   a finding it no longer returns is resolved.
3. Test: add a broken file to `tests/fixtures/sample-financials/` (see the generator in `generator/`) and a test in
   `tests/integration/test_validate.py` that ingests it and asserts that the document is held with a finding carrying
   the code.

## Add a report section

The report is built in `src/fin/report/`: `data.py` reads the data (`collect`), `tables.py` turns it into Markdown tables;
`render.py` fills `templates/report/health.qmd`. A narrative section is a key in `TITLES` (`data.py`): the stub
gets a `## <Title>` heading for it, and `{{narrative:<key>}}` in the template places the text.

- **A metric category** (a metric whose `metric_definitions.category` is new): add `(category, title)` to
  `CATEGORIES` in `data.py`. Its section (heading, narrative, trend table of every metric in the category, chart)
  is made by `category_section` in `render.py` and appears whenever the company has such a metric. Optionally list
  up to four same-unit metrics in `CHARTS` for its chart. Display names and times-or-percentage are in
  `metric_definitions` (`label`, `times`).
- **A metric in the summary or peer table:** add `("metric", name)` or `("fact", account)` to `KEY_ROWS`.
- **A new fixed section** (one table): add its key and title to `TITLES` and to `sections()` in `render.py`, a
  function in `tables.py` that returns its Markdown table, and an entry in the `tables` dict in `build` (`render.py`);
  then put `## <Title>`, `{{narrative:<key>}}` and `{{table:<key>}}` in `health.qmd`. Read new data in `collect`
  with SQL, so the data hash covers it.
- Update `.claude/commands/report.md` with what to write under the new heading.
- Test: extend `tests/integration/test_report.py` (the fake Quarto keeps the filled template as the HTML, so assert
  table lines in it). The narrative never contains typed numbers; it uses `{{metric:name:FY25}}`,
  `{{metric:name:FY26-Q1}}` and `{{fact:account:FY25}}` (the lint in `report/lint.py` enforces it).

## Change the PDF parser

`extract/pdf.py` reads clean digital statements. A page it cannot read safely is skipped and listed as not read
(E201-E204), and a Claude reading (`fin ingest --reading`) fills it; nothing is guessed. Keep it that way:

- Add a parsing rule only when it reads a statement correctly in a document it was not written for. A rule that
  helps one document only is a workaround: leave that page to a Claude reading.
- A rule must never load a wrong figure. When a page looks misread, raise an `E2xx` error for the page
  (`read_page`), so it is listed as not read.
- Test the rule with a tiny synthetic PDF (`tests/synthetic_pdf.py`), asserting the rows read or the page reported.
- A page `pdf.py` skips goes to the chosen PDF add-on. A rule for how an add-on's table is read belongs in
  `extract/pdf_tables.py` (for every engine), tested with a hand-built grid (`tests/unit/test_pdf_tables.py`); an
  engine's adapter only turns its output into grids, tested with recorded or hand-built output
  (`tests/fixtures/pdf_addon/`). A test that runs a real engine is marked `@pytest.mark.docling` or
  `@pytest.mark.liteparse`; it runs in CI's `pdf-addon` jobs, locally with `uv sync --extra <name>` and
  `uv run pytest -m <name>`. A `@pytest.mark.llamaparse` test calls the paid API: it skips without
  `LLAMA_CLOUD_API_KEY` and never runs in CI.

## Schema

Each database has one schema file with its final tables and views, in `src/fin/db/schema/`: `audit.sql`,
`dictionary.sql`, `analytics.sql`, `company.sql` (tables), `company_views.sql` (metric macros and views) and
`diligence.sql` (diligence tables) and `diligence_views.sql` (EBITDA bridge and walk views). `fin init` creates the
shared databases from them, and a company database is created from `company.sql`, `company_views.sql`,
`diligence.sql` and `diligence_views.sql` the first time that company is used. Nothing alters a database that exists; there are no migrations and no version
numbers.

To change a schema, edit its file. Then either re-create the data folder (move `~/FinAnalysis` away, run `fin init` and
ingest again), or make the same change to the existing databases with `fin sql --write` (it backs up and logs). A database
that lacks a table, view or column its schema files create (made by an older fin) is detected (E002) and has to be
re-created or changed to match.

## Eval checks

The golden evaluation (`docs/EVAL.md`, `src/fin/evals/`) scores fin's outputs against values a person checked in real
documents. A feature that produces a figure the user relies on (a later diligence phase's registers, bank and
reconciliation lines, adjusted EBITDA) adds a check kind so the client's golden sets cover it:

1. `evals/golden.py`: add the kind to `KINDS`, the columns it needs to `NEEDS` (`period`, `scope`, `account`,
   `ledger`), and a line on what its value is to the module docstring. Reuse the columns: a key is kind, period,
   scope, account and ledger.
2. `evals/score.py`: add its query to `_FIN_VALUE` (fin's value now, in rupees, or NULL when fin has none).
3. `evals/scaffold.py`: add a query to `candidates` listing every output of the kind with its `source` (file +
   sheet!cell, sheet!row or page). Leave `expected` and `checked` empty: only a person fills them.
4. `evals/counts.py`: if the feature has its own failure markers, add them to `related_codes`; if it adds a count
   worth watching, add it to `counts`. The bundle carries whatever these return, so return only accounts, periods,
   codes, tokens and counts: never names, file names, labels or figures.
5. `docs/EVAL.md`: the kind in the column table of step 4, with what its value is.
6. Test in `tests/unit/test_evals.py`: a correct golden line for the kind passes and a planted wrong one fails.

## Tests and fixtures

- `uv run pytest tests/path/to/test_file.py`. Unit tests are in `tests/unit/`; `tests/integration/` runs the real CLI
  with Typer's `CliRunner` against a temporary data folder (`FIN_DATA_DIR`). Never touch `~/FinAnalysis` in a test.
- `tests/fixtures/sample-financials/` holds synthetic companies (Kestrel, Bluepeak, Tarang) in `clean/`, deliberately
  broken files in `broken/`, and `answer_key.json` with the expected value of every line, its source cell or page, and
  the expected metrics. Its README says which file tests which feature and which error each broken file must raise.
  Assert against the key, not against numbers you copy from the output.
- `sample-financials/generator/` builds the files and `answer_key.json` (`python generate.py ..`, then
  `python verify.py ..`; it needs extra packages, listed in the fixtures README). Change the generator, not the
  generated files by hand. Never commit real financial data.
