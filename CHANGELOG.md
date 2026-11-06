# Changelog

All notable changes to this project. Versions follow semantic versioning; `v1.0.0` is tagged in Phase 12.

## [Unreleased]

### Phase 0: Repo, install, guardrails

Added
- `fin` CLI (Typer) with `fin --version`, `fin init` (creates the data folder tree from PLAN.md §6, idempotent, never
  deletes) and `fin doctor` (checks Python 3.12, uv, node, quarto and the data folder; `--json` output; exit 1 on
  failure).
- Data folder resolution: `FIN_DATA_DIR`, defaulting to `~/FinAnalysis`.
- Error classes for E001, E002, E003 and E701, plus exit-code constants.
- Claude Code hooks in `.claude/settings.json`:
  - `guard.py` (PreToolUse) blocks with E701: `rm`/`mv`/`rmdir`/`unlink`/`find -delete`, redirects and `tee` into
    the data folder; any direct access to a data-folder `.duckdb` file except through `fin`; and the human-only
    `fin delete` and `fin review confirm`. It fails closed on unreadable input and logs every block.
  - `log_action.py` (PostToolUse) appends every tool call to `<data>/logs/claude_actions.jsonl`.
  - A permissions deny list for the human-only commands.
- `install.sh` and `Brewfile` (uv, node, quarto), with the log in `<data>/logs/install.log`.
- CI: ruff and shellcheck (ubuntu), pytest (macOS), and an install job (macOS) that runs `install.sh` twice and checks
  that the second run changes nothing.
- README install section, MIT license.

Dependencies (exact pins in `uv.lock`)
- `typer` 0.28.0: CLI framework (frozen stack).
- Dev: `pytest` 9.2.0, `ruff` 0.17.4 (frozen stack); `pytest-timeout` 2.4.0, so a hung test fails fast in CI.

Notes and deviations
- Homebrew cannot pin exact versions, so `fin doctor` enforces minimums for system tools: uv ≥ 0.11, node ≥ 24,
  quarto ≥ 1.7. Python dependencies are pinned exactly.
- Phase 0 commands do not yet write run records or audit events. The audit logger arrives in Phase 1, which wires
  every command into it.
- `fin init` creates folders only. `config.toml` is created in Phase 1, and databases in Phase 2 (when `fin doctor`
  also gains the schema-version check).
- `install.sh` leaves out `npm ci --prefix dashboards` until the dashboards project exists (Phase 10).
