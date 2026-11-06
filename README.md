# fin-analyst

A local tool for analysing the financial health of companies. You drop financial documents (Excel, PDF) into a
folder, and `fin` loads them into a local database, answers questions with the source cell or page for every number,
and produces reports and dashboards. It analyses only: it gives no scores, rankings or recommendations.

Everything runs on your Mac. Your documents and databases stay in your data folder (`~/FinAnalysis`), outside this
code folder.

## Requirements

- macOS (Apple Silicon or Intel)
- An internet connection for the first install

## Install

Open Terminal and run:

```sh
git clone https://github.com/abhij1306/fin-analyst.git
cd fin-analyst
./install.sh
```

The installer:

1. Checks that you are on macOS.
2. Checks for [Homebrew](https://brew.sh) and asks before installing it if it is missing.
3. Installs `uv`, `node` and `quarto` with Homebrew.
4. Sets up the Python environment at the exact versions this release was tested with.
5. Creates your data folder at `~/FinAnalysis` (it never deletes or overwrites anything there).
6. Runs `fin doctor` to check that everything works.

You can run `./install.sh` again at any time. It only installs what is missing. A log of every run is kept in
`~/FinAnalysis/logs/install.log`.

To keep your data somewhere else, set `FIN_DATA_DIR` before installing, for example
`export FIN_DATA_DIR="$HOME/Documents/FinAnalysis"`, and add that line to your `~/.zshrc`.

## Check your installation

```sh
uv run fin doctor
```

Each line says `ok` or `FAIL`. A failed line shows an error code and the fix:

| Code | Meaning | Fix |
|---|---|---|
| E001 | A required program, `config.toml` or the audit database is missing | Run the fix shown (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database needs migrating | Run the fix shown (`brew upgrade`, correct `config.toml`, or `uv run fin migrate`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or check folder permissions |
| E004 | A database is newer than this version of fin | Update fin: `git pull`, then `uv sync --frozen` |
| E501 | A database is in use by another `fin` command | Wait for it to finish and run the command again |
| E503 | A database migration failed | The database was restored from `backups/`; report the error |

## Settings

`fin init` writes `~/FinAnalysis/config.toml` once: the display unit (crores by default), default statement scope,
fiscal year start and validation tolerances. Edit it to change them; `fin init` never overwrites it. Run
`uv run fin doctor` after editing to check it.

## Loading documents

Put a company's Excel (`.xlsx`) and PDF files in `~/FinAnalysis/inbox/<company>/` (company names are lower case, for
example `kestrel`) and run `uv run fin ingest <company>`, or `uv run fin ingest <company> <file or folder>`. To load
several companies, give each a folder and run `uv run fin ingest-batch ~/FinAnalysis/inbox`.

- A file already loaded is skipped (E105), so re-running a batch that stopped picks up where it left off. Add
  `--force` to load a file again as a new version.
- Each original is copied to `~/FinAnalysis/archive/<company>/`, named by its sha256. Your files are not moved.
- A file that cannot be loaded is copied to `~/FinAnalysis/review/<company>/` with a `.error.json` giving the error
  code and reason; nothing from it is loaded.
- Labels the dictionary does not know load as unconfirmed and are logged with E301.

## Logs

Every `fin` command gets a run id, ends with a one-line summary (`loaded, skipped, failed, pending review, run_id`),
and is recorded in `~/FinAnalysis/shared/audit.duckdb` and `~/FinAnalysis/logs/<date>/<run_id>.jsonl`.

## Updating the database layout

`uv run fin migrate` brings every database up to the current layout. It backs each file up to
`~/FinAnalysis/backups/<run_id>/` first and is safe to run again.

## Using it with Claude Code

Start Claude Code in this folder with `claude`. Claude runs `fin` commands for you, and can change your data when you
ask it to. The guardrails only catch accidents: before Claude deletes or moves files in your data folder, opens a
database directly, or runs `fin delete` or `fin review confirm`, Claude Code asks you to confirm. Approve only if you
asked for that change. Data changes go through `fin`, which backs up first and records them. Every action Claude takes
is logged to `~/FinAnalysis/logs/claude_actions.jsonl`.

## Uninstall

Delete the `fin-analyst` folder. Your data folder is never touched.
