# Customising fin-analyst with Claude Code

Keep changes small. Every change needs a test, and a line in `CHANGELOG.md` (and `PLAN.md` if it changes behaviour).
Rules are in `CLAUDE.md`. Local checks: `uv run ruff check <files>`, `uv run ruff format <files>`,
`uv run pytest <test files>`.

## Module map (`src/fin/`)

- `cli.py`: the `fin` commands (Typer). Each runs through the audit logger.
- `config.py`, `paths.py`: `config.toml` settings, and every data-folder path.
- `audit/errors.py`: error classes and codes (E001 to E701). `audit/logger.py`: run records and logging.
  `audit/trace.py`: `fin audit`.
- `db/conn.py`: the only way to open a database. `db/schema.py`: creates a database from its schema file.
  `db/schema/<kind>.sql`: the schema (`audit`, `dictionary`, `analytics`, `company`; the company metric views are
  in `company_views.sql`).
- `ingest.py`: loads one document in one transaction (stage, check, commit, or roll back on an error).
- `findings.py`: findings (what does not add up in a filing): `VALIDATORS` (the checks run on every staged
  document), holding a document with open findings, `fin findings list|accept`, and the re-check of held documents
  after a fix. `status.py`: `fin status`.
- `extract/`: `excel.py`, `pdf.py`, `text.py` (notes as Markdown), `units.py` (crores, lakhs, ...),
  `periods.py` (period names: `FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`, and how a column's length is read),
  `reading.py` (reading files: figures Claude read from pages fin could not read, for `fin ingest --reading`).
- `verify.py`: `fin verify`, compares a reading with what is loaded and marks agreeing facts verified.
- `map/`: `dictionary.py` (seed, label map, lookup by section and balance-sheet part), `normalize.py` (label
  cleaning), `review.py` and `commands.py` (`fin map propose|set|accounts`, `fin review`).
- `validate/gates.py`: the checks that run on a staged document (E401 to E405); a failed check is a finding.
- `metrics/export.py`: copies every company's `metrics` view to `shared/analytics.duckdb`.
- `query/`: `run.py` (`fin query`), `write.py` (`fin sql --write`), `evidence.py` (evidence pages).
- `report/`: `data.py` (sections and labels), `lint.py` (the no-typed-numbers rule), `render.py` (Quarto).
- `delete.py`, `update.py`, `doctor.py`: `fin delete`, `fin update`, `fin doctor`.
- Outside `src/`: `templates/dictionary/` (seed CSVs), `templates/report/health.qmd`, `.claude/commands/` (slash
  commands), `.claude/hooks/` (action log), `dashboards/` (Evidence project).

## Add an account or a label to the dictionary seed

- Account: add a row to `templates/dictionary/schedule_iii.csv` (`account,statement,parent,sign,unit,description`).
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
- Leave a label to review when it means different accounts in different formats and no section tells them apart.
  The seed does not map "Loans", "Investments", "Other financial assets", "Provisions", "Lease liabilities",
  "Other financial liabilities" or "Other assets" outside a balance-sheet part, nor "Derivative financial
  instruments" or assets held for sale: in a Division II statement whose part heading was not read they would land
  in the wrong subtotal and a correct document would be held on an E402 finding. Map them per company (`fin map set`) instead.
- Run `uv run fin init`. It adds missing rows to the shared dictionary and never removes or changes existing ones. To
  change an existing row (a new parent or sign), edit the row in `templates/dictionary/schedule_iii.csv` and re-create the data folder, or use `fin sql --write` on
  the dictionary for a one-off change.
- One company only: `uv run fin map set --company <co> --statement <statement> --label "<label as written>" --account
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

Metrics are SQL views in each company database, so they are always current.

1. Edit `src/fin/db/schema/company_views.sql`, the one place the metrics live. Add your metric to the `metrics` view
   (the formula, the `UNPIVOT` list, the unit `CASE` and the final `WHERE`) and to `metric_definitions` (the accounts
   the metric uses), so `fin audit <co> <metric> <period>` can trace it. Read the rules at the top of the file: facts
   come from `current_facts` (one value per account, period and scope), a missing input gives NULL, ratios use
   `div6`. The final `WHERE` of the view says which metrics a year gets and which a quarter or half year gets; a
   metric that needs a full year of flows or balance-sheet averages belongs to years only.
2. The views only exist in a company database when it is created, so apply the change to existing ones too: re-create
   the data folder (`fin init` and ingest again), or run the edited `CREATE OR REPLACE VIEW` through `fin sql --write`
   on each company.
3. Optionally show it in the report: add it to `SECTIONS`, `LABELS` (and `PERCENT` if it is a percentage) in
   `src/fin/report/data.py`.
4. Test: add the metric's expected values to the key's `metrics` for each company (in
   `sample-financials/generator/`, then regenerate `answer_key.json`), computed from the key's facts, and run
   `tests/integration/test_metrics.py`. It compares every metric to the key and fails on a metric the key does not
   list.

## Add a validation gate

A validator is a function `f(run, company, con, doc_id)` that returns the findings for the staged document: a list
of dicts with the columns of the company `findings` table (`check_name`, `code`, `finding`, `statement`, `scope`,
`period`, `label`, `fin_value`/`other_value`, `fin_printed`/`other_printed`, `fin_source`/`other_source`,
`other_file`, `message`). A document with any finding is held, not rejected: what a company filed that does not add
up is for the user to see and decide.

1. Pick the next free check code (E4xx) and add a row to the table in `PLAN.md` §9 and a line to the README
   "Findings" section. Check codes are not error classes.
2. Write the check (see `validate/gates.py` for how a check reads the staged facts, records its result and builds
   its findings), and add the function to `VALIDATORS` in `findings.py`. It runs again on held documents after a fix;
   a finding it no longer returns is resolved.
3. Test: add a broken file to `tests/fixtures/sample-financials/` (see the generator in `generator/`) and a test in
   `tests/integration/test_validate.py` that ingests it and asserts that the document is held with a finding carrying
   the code.

## Add a report section

A section is a heading in `templates/report/health.qmd` plus an entry in `SECTIONS` in `src/fin/report/data.py`
(`(key, title, [("metric", name) or ("fact", account), ...])`).

1. Add the entry to `SECTIONS`.
2. Add `## <Title>`, `{{narrative:<key>}}` and `{{table:<key>}}` to `health.qmd`, in the same order.
3. Update `.claude/commands/report.md` only if the way the narrative is written changes.
4. Test: extend `tests/integration/test_report.py`. The narrative never contains typed numbers; it uses
   `{{metric:name:FY25}}` and `{{fact:account:FY25}}` (the lint in `report/lint.py` enforces it).

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
