# SPEC-12: install and first run

Builder's readings (2026-11-28), for the owner to confirm or change.

## The install ends with the command that opens the UI, not a link

- **Decision**: the last line of `install.sh` is `Open the web UI: cd <repo> && uv run fin ui`, with the address
  it opens (http://127.0.0.1:8765). With `--no-ui` it says how to install the UI later.
- **Why**: the spec says the install "ends with the link to open". The UI's link carries a token made each time
  `fin ui` starts (docs/web-ui-plan.md), so no link printed before the server runs would open it. Starting the server
  from the install would leave the install running, and an agent runs the install.
- **How to change it**: `install.sh`, the last `echo` lines.

## The PDF add-on question is printed, not asked

- **Decision**: with no add-on chosen, the install prints the three add-ons with where they run, size and cost, and
  the command for each. It never waits for an answer, and nothing is installed without a choice.
- **Why**: the client's install ran under an agent with no terminal; a prompt would stop it. The spec's "nothing is
  installed without a choice" holds either way.
- **How to change it**: `install.sh`, the block after `fin doctor` (`PARSER == none`).

## `--ui` is gone; `--no-ui` removes an installed UI

- **Decision**: every run installs the `ui` extra unless `--no-ui` is given. `--no-ui` on a checkout that has the
  UI removes it (a plain sync), as `--parser none` removes an add-on. The old `--ui` flag is refused as unknown.
- **Why**: greenfield, no compatibility code; one flag, and the second run without flags still changes nothing.
- **How to change it**: `install.sh`, `NO_UI` and `EXTRAS`.

## Doctor's install checks are warnings

- **Decision**: `python source` (fin is not on uv's own Python) and `repo folder` (the repo is in a temporary folder)
  are `warn` lines with the fix, never failures.
- **Why**: another Python 3.12 runs fin correctly (a wrong version still fails the `python` check), and a repo in a
  temporary folder works until it is deleted. A failure would also fail the CI test jobs, which use the runner's
  Python.
- **How to change it**: `src/fin/doctor.py: check_python_source, check_repo_folder`.

## What counts as a temporary folder

- **Decision**: `/tmp`, `/var/tmp`, `/var/folders` (and their `/private` forms on macOS), `$TMPDIR`, and in fin
  also Python's temporary folder. A path with spaces alone is not warned about.
- **Why**: the client's repo landed in an app's temporary workspace; these are the folders the system empties. The
  app's own workspace folder is not named in the run's notes, so no guess was added for it.
- **How to change it**: `TEMP_ROOTS` in `install.sh` and `src/fin/doctor.py` (a test keeps them equal).

## A `.venv` from another Python is made again

- **Decision**: the install removes a `.venv` whose `pyvenv.cfg` `home` is outside `uv python dir`, then syncs it
  again from uv's own Python.
- **Why**: the client's second attempt broke on a Python from PATH; the next run should repair it, not keep it.
- **How to change it**: `install.sh`, the `VENV_HOME` block.

## Quarto on macOS lives in the home folder; fin looks there first

- **Decision**: the pinned Quarto tarball is unpacked into `~/.local/opt/quarto-<version>` and linked from
  `~/.local/bin/quarto`. fin (`fin doctor`, `fin report --render`) looks for a tool in `~/.local/bin` before PATH,
  as the install does, so the folder need not be on the user's PATH.
- **Why**: "into the user's home" with no sudo; `~/.local/bin` is where Quarto's own tarball instructions link it.
  Looking there first keeps fin and the install agreeing on which Quarto is used.
- **How to change it**: `QUARTO_HOME` and `USER_BIN` in `install.sh`; `src/fin/systools.py: which`.
