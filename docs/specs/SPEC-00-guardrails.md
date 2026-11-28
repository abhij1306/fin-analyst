# SPEC-00: Guardrails

## Summary

Turn the defects that keep coming back into checks that fail a PR, so reviewers and bots stop finding the same
things. 27% of merged PRs so far were fixes of earlier PRs, six of them for SonarCloud alone. This spec adds the
checks named in [engineering.md](../engineering.md) as "to add" and nothing else. It changes no behaviour of `fin`.

## Scope

- Lint rules, a type check, CI jobs and structural tests listed below.
- The shared helpers those tests need (escaping, paths, atomic writes).

Out of scope: refactoring for its own sake, a coverage floor, splitting files that are under the size limit.

## Design

1. **Ruff.** Add `C901` (complexity, limit 15), `PLR0913` (arguments, limit 13), `PLR0915`, `S` (security),
   `SIM`, `RUF` and `ERA` to `[tool.ruff.lint]`. The limits are SonarCloud's, so a PR that passes ruff passes
   Sonar on those rules. Existing violations are fixed in the PR that turns a rule on; a rule with more than about
   30 violations goes in with a per-file ignore list that only shrinks.
2. **Type check.** Pyright in basic mode on `src/fin`, as a CI job (not run locally). New dev dependency: it catches
   the argument-type and return-type mistakes Sonar reported after merge (S5655, S5886).
3. **CI.**
   - The `ui` job runs every `tests/unit/test_ui_*.py`. Today it names three files; the chat and format tests
     (24 tests) skip in the main job because the `ui` extra is not installed there.
   - `macos.yml` also runs on a PR that changes `install.sh`, `Brewfile` or `macos.yml`.
   - The SonarCloud quality gate becomes a required check.
4. **Shared helpers, each with a test that fails when code goes around it.**
   - `fin.output.escape`: one function per target (Markdown cell, HTML, Excel cell). Test: report and export table
     builders reach text only through it.
   - `fin.paths.inside(root, path)`: resolves symlinks and refuses a path outside the root.
   - `fin.paths.atomic_write`: a unique temporary name and an atomic replace. Ruff bans fixed temporary names.
5. **Structural tests.**
   - Gates: each gate lists its input tables and columns in one registry; a test changes each one and expects the
     gate to reopen.
   - Masking: a registry of every text column with "masked" or "as filed"; a test fails on a column missing from it.
   - CLI: one parametrised test over every Typer command: `--help` works, a usage error exits with its code, an
     option the chosen mode ignores is refused.
   - Docs: a test fails when a command or option exists in the CLI and not in `docs/product.md`, or the other way.
6. **Sonar findings in `src/`** (8 open): the chat stream handler's complexity (70 against 15), two functions over
   the argument limit, the cookie flag, the accessible names of four buttons, one regular expression.
7. **Categories tally.** `tools/tally_categories.mjs` reads the `Categories:` line of merged PR bodies (through
   `gh`) and prints a count per category. The retro in [process.md](../process.md) uses it.

## Acceptance criteria

- [ ] Each rule and test above fails on a deliberately broken example, shown in its PR.
- [ ] SonarCloud shows no open issue in `src/`.
- [ ] Every UI test runs in CI (the job log lists 62 or more).
- [ ] [engineering.md](../engineering.md) says "enforced by" for every rule this spec turned on.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Run every UI test in CI and macOS checks on install changes | `.github/workflows/` | job logs show the tests |
| 2 | Turn on complexity, argument and security lint rules | `pyproject.toml`, fixes | ruff green |
| 3 | Add the type check job | `pyproject.toml`, CI, fixes | job green |
| 4 | Add shared escaping, path and atomic-write helpers | `src/fin/output`, `src/fin/paths.py`, callers | tests green |
| 5 | Add the gate-input and masking registries with their tests | `src/fin/validate`, `src/fin/diligence/mask.py` | tests green |
| 6 | Add the CLI and docs consistency tests | `tests/unit` | tests green |
| 7 | Clear the open Sonar findings in src and add the categories tally | `src/fin/ui`, `tools/` | Sonar clean |

Human task: Q8 (switch the two test-style rules off in the Sonar profile) and making the Sonar gate required.
