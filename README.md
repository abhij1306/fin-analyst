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
| E001 | A required program is missing | Run `./install.sh` again |
| E002 | A program is too old, or the wrong version | Run the `brew upgrade` command shown |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or check folder permissions |

## Using it with Claude Code

Start Claude Code in this folder with `claude`. Claude runs `fin` commands for you, and can change your data when you
ask it to. The guardrails only catch accidents: before Claude deletes or moves files in your data folder, opens a
database directly, or runs `fin delete` or `fin review confirm`, Claude Code asks you to confirm. Approve only if you
asked for that change. Data changes go through `fin`, which backs up first and records them. Every action Claude takes
is logged to `~/FinAnalysis/logs/claude_actions.jsonl`.

## Uninstall

Delete the `fin-analyst` folder. Your data folder is never touched.
