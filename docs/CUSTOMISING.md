# Customising fin-analyst with Claude Code

Keep changes small. Every change needs a test, and a `PLAN.md` update if it changes behaviour (`CHANGELOG.md` is written at release).
Rules are in `CLAUDE.md`. Local checks: `uv run ruff check <files>`, `uv run ruff format <files>`,
`uv run pytest <test files>`.

## Module map (`src/fin/`)

- `cli.py`: the `fin` commands (Typer). Each runs through the audit logger.
- `config.py`, `paths.py`: `config.toml` settings, and every data-folder path.
- `audit/errors.py`: error classes and codes (E001 to E701). `audit/logger.py`: run records and logging.
  `audit/trace.py`: `fin audit`.
- `db/conn.py`: the only way to open a database; `db/rows.py`: query results as dicts. `db/schema.py`: creates a
  database from its schema file.
  `db/schema/<kind>.sql`: the schema (`audit`, `dictionary`, `analytics`, `company`; the company metric views are
  in `company_views.sql`).
- `ingest.py`: loads one document in one transaction (stage, check, commit, or roll back on an error).
- `findings.py`: findings (what does not add up in a filing): `VALIDATORS` (the checks run on every staged
  document), holding a document with open findings, `fin findings list|accept`, and the re-check of held documents
  after a fix. `status.py`: `fin status`.
- `extract/`: `excel.py`, `pdf.py`, `text.py` (notes as Markdown), `units.py` (crores, lakhs, ...),
  `periods.py` (period names: `FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`, and how a column's length is read),
  `reading.py` (reading files: figures Claude read from pages fin could not read, for `fin ingest --reading`).
  The optional Docling add-on (`docling` extra): `docling_run.py` runs Docling on the statement pages `pdf.py`
  skipped (and on short scanned PDFs), once per document with one converter per process; `docling_pages.py` reads
  Docling's table grids into `PdfValue`s with `pdf.py`'s and `periods.py`'s helpers and guards (no Docling needed,
  so its tests are fast). Ingest uses it only when `[pdf] docling = true` in `config.toml`.
- `verify.py`: `fin verify`, compares a reading with what is loaded and marks agreeing facts verified.
- `map/`: `dictionary.py` (seed, label map, lookup by section and balance-sheet part), `normalize.py` (label
  cleaning), `review.py` and `commands.py` (`fin map propose|set|accounts`, `fin review list|confirm|reject`).
- `validate/gates.py`: the checks that run on a staged document (E401 to E405); a failed check is a finding.
- `metrics/export.py`: copies every company's metrics, metric definitions, standard statements, findings,
  coverage, unread pages and unverified facts to `shared/analytics.duckdb` (`TABLES`), for the dashboards.
- `statements.py`: the `standard_statements` view (made per connection from the dictionary's account tree) and
  `fin statements`. `workbook.py`: `fin export` (the Excel workbook).
- `query/`: `run.py` (`fin sql`, read, and `--evidence`), `write.py` (`fin sql --write`), `evidence.py` (`fin sql --evidence` pages).
- `report/`: `data.py` (what the report reads, categories, charts), `tables.py` (display formatting and the
  Markdown tables), `chart.py` (the SVG line chart),
  `lint.py` (the no-typed-numbers rule), `render.py` (narrative stub, sections, Quarto).
- `delete.py`, `update.py`, `doctor.py`: `fin delete`, `fin update`, `fin doctor`.
- Outside `src/`: `templates/dictionary/` (seed CSVs), `templates/report/health.qmd`, `.claude/commands/` (slash
  commands), `.claude/hooks/` (action log), `dashboards/` (Evidence project).

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
- Run `uv run fin init`. It adds missing rows to the shared dictionary and never removes or changes existing ones. To
  change an existing row (a new parent or sign), edit the row in `templates/dictionary/schedule_iii.csv` and re-create the data folder, or use `fin sql --write` on
  the dictionary for a one-off change.
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
- A page `pdf.py` skips goes to Docling when the add-on is on. A rule for how a Docling table is read belongs in
  `extract/docling_pages.py`, tested with a hand-built grid (`tests/unit/test_docling_pages.py`). A test that runs
  the real Docling is marked `@pytest.mark.docling`; it runs in CI's `docling` job, locally with
  `uv sync --extra docling` and `uv run pytest -m docling`.

## Schema

Each database has one schema file with its final tables and views, in `src/fin/db/schema/`: `audit.sql`,
`dictionary.sql`, `analytics.sql`, `company.sql` (tables) and `company_views.sql` (metric macros and views). `fin init`
creates the shared databases from them, and a company database is created from `company.sql` and `company_views.sql`
the first time that company is used. Nothing alters a database that exists; there are no migrations and no version
numbers.

To change a schema, edit its file. Then either re-create the data folder (move `~/FinAnalysis` away, run `fin init` and
ingest again), or make the same change to the existing databases with `fin sql --write` (it backs up and logs). A data
folder made by an older fin that had migrations is detected (E002) and has to be re-created.

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
