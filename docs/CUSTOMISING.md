# Customising fin-analyst with Claude Code

Keep changes small. Every change needs a test, and a line in `CHANGELOG.md` (and `PLAN.md` if it changes behaviour).
Rules are in `CLAUDE.md`. Local checks: `uv run ruff check <files>`, `uv run ruff format <files>`,
`uv run pytest <test files>`.

## Module map (`src/fin/`)

- `cli.py`: the `fin` commands (Typer). Each runs through the audit logger.
- `config.py`, `paths.py`: `config.toml` settings, and every data-folder path.
- `audit/errors.py`: error classes and codes (E001 to E701). `audit/logger.py`: run records and logging.
  `audit/trace.py`: `fin audit`.
- `db/conn.py`: the only way to open a database. `db/migrate.py`: runs migrations. `db/migrations/<kind>/NNN_*.sql`:
  the schema (`audit`, `dictionary`, `analytics`, `company`).
- `ingest.py`: loads one document in one transaction (stage, validate, commit or roll back). `VALIDATORS` lives here.
- `extract/`: `excel.py`, `pdf.py`, `text.py` (notes as Markdown), `units.py` (crores, lakhs, ...),
  `periods.py` (period names: `FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`, and how a column's length is read).
- `map/`: `dictionary.py` (seed, label map, lookup by section and balance-sheet part), `normalize.py` (label
  cleaning), `review.py` and `commands.py` (`fin map propose|set|accounts`, `fin review`).
- `validate/gates.py`: the checks that run on a staged document (E401 to E405).
- `metrics/export.py`: copies every company's `metrics` view to `shared/analytics.duckdb`.
- `query/`: `run.py` (`fin query`), `write.py` (`fin sql --write`), `evidence.py` (evidence pages).
- `report/`: `data.py` (sections and labels), `lint.py` (the no-typed-numbers rule), `render.py` (Quarto).
- `delete.py`, `update.py`, `doctor.py`: `fin delete`, `fin update`, `fin doctor`.
- Outside `src/`: `templates/dictionary/` (seed CSVs), `templates/report/health.qmd`, `.claude/commands/` (slash
  commands), `.claude/hooks/` (guard and action log), `dashboards/` (Evidence project).

## Add an account or a label to the dictionary seed

- Account: add a row to `templates/dictionary/schedule_iii.csv` (`account,statement,parent,sign,unit,description`).
  `parent` is the subtotal it adds into and `sign` is 1 or -1; the validation gates use them. `unit` is `rupees`, or
  `rupees_per_share` for a per-share figure such as EPS: it is stored as printed (not scaled by the statement's unit)
  and cannot have a parent. `uv run fin map accounts` prints the tree.
- Label: add a row to `templates/dictionary/labels.csv` (`label,statement,section,account`). The account must exist.
  `section` is the heading the label sits under, or empty for any heading. For the balance sheet it can also be the
  part (`Non-current assets`, `Current assets`, `Non-current liabilities`, `Current liabilities`): a row under a
  sub-heading such as "Financial liabilities" is looked up under the part it is in (`map_rows` in
  `map/dictionary.py`).
- Run `uv run fin init`. It adds missing rows to the shared dictionary and never removes or changes existing ones. To
  change an existing row (a new parent or sign), add a `dictionary` migration (see
  `db/migrations/dictionary/002_account_unit_and_pl_tree.sql`), or use `fin sql` on the dictionary.
- One company only: `uv run fin map set --company <co> --statement <statement> --label "<label as written>" --account
  <account> [--section "<heading>"]`. It wins over the seed for that company, for files ingested from then on. This
  is also how to fix a file a check rejected: its `review/<co>/<file>.error.json` lists the lines and the commands.
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

1. Add a new file `src/fin/db/migrations/company/NNN_<name>.sql` (next free number; never edit an applied one). It
   redefines the `metrics` view with `CREATE OR REPLACE VIEW`, copying the latest one (`003_periods.sql`) and adding
   your metric. Do the same for `metric_definitions` (the accounts the metric uses), so `fin audit <co> <metric>
   <period>` can trace it. Read the rules at the top of `002_metrics.sql` and `003_periods.sql`: facts come from
   `current_facts` (one value per account, period and scope), a missing input gives NULL, ratios use `div6`. The
   final `WHERE` of the view says which metrics a year gets and which a quarter or half year gets; a metric that
   needs a full year of flows or balance-sheet averages belongs to years only.
2. Run `uv run fin migrate`. Migrations back up, run in a transaction, and roll back on failure (E503).
3. Optionally show it in the report: add it to `SECTIONS`, `LABELS` (and `PERCENT` if it is a percentage) in
   `src/fin/report/data.py`.
4. Test: add the metric's expected values to the key's `metrics` for each company (in
   `sample-financials/generator/`, then regenerate `answer_key.json`), computed from the key's facts, and run
   `tests/integration/test_metrics.py`. It compares every metric to the key and fails on a metric the key does not
   list.

## Add a validation gate

A validator is a function `f(run, company, con, doc_id)` that raises a `DocumentFailure` subclass (error code in
`audit/errors.py`) when the staged document is wrong.

1. Add the error class, with the next free code, to `audit/errors.py`, and a row to the table in `PLAN.md` §9 and in
   the README Troubleshooting table (a test fails if the README misses a code).
2. Write the check (see `validate/gates.py` for how a check reads the staged facts and records its result), and add
   the function to `VALIDATORS` in `ingest.py`. The document is rolled back and sent to `review/` when it raises.
3. Test: add a broken file to `tests/fixtures/sample-financials/` (see the generator in `generator/`) and a test in
   `tests/integration/test_validate.py` that ingests it and asserts the exit code, the error code and that nothing
   was loaded.

## Add a report section

A section is a heading in `templates/report/health.qmd` plus an entry in `SECTIONS` in `src/fin/report/data.py`
(`(key, title, [("metric", name) or ("fact", account), ...])`).

1. Add the entry to `SECTIONS`.
2. Add `## <Title>`, `{{narrative:<key>}}` and `{{table:<key>}}` to `health.qmd`, in the same order.
3. Update `.claude/commands/report.md` only if the way the narrative is written changes.
4. Test: extend `tests/integration/test_report.py`. The narrative never contains typed numbers; it uses
   `{{metric:name:FY25}}` and `{{fact:account:FY25}}` (the lint in `report/lint.py` enforces it).

## Migrations

SQL files in `src/fin/db/migrations/<kind>/NNN_name.sql`, numbered 001, 002, ... with no gaps. Each file's version is
recorded in its database's `schema_version` table. `fin migrate` (also run by `fin update` and `fin init`) backs up
every database that has pending migrations to `backups/<run_id>/`, applies each migration in its own transaction, and
restores the backup on failure (E503). A database newer than the code stops with E004. Never edit a migration that has
been released; add a new one.

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
