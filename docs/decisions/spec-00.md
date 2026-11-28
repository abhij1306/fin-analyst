# SPEC-00: guardrails

Builder's readings of [SPEC-00](../specs/SPEC-00-guardrails.md), not owner decisions.

## Existing violations go into a per-file list only for S101 and S608

- **Decision**: every new ruff rule was fixed in the code except two security rules with hundreds of hits:
  `S101` (an assert that narrows a type for the type checker) and `S608` (SQL text built from fin's own table and
  column names; values are always bound with `?`). They are ignored per file in `pyproject.toml`, for the files
  that have them today; the list only shrinks. `S603` (running a program) is ignored everywhere: fin runs fixed
  programs (git, npm, quarto, uv, claude) with an argument list, never a shell, and the shell rules stay on.
- **Why**: the spec allows a shrinking per-file list for a rule with more than about 30 violations.
- **How to change it**: remove a file from `[tool.ruff.lint.per-file-ignores]` and fix what ruff reports.

## Typer commands with one parameter per option keep their parameters

- **Decision**: three commands take 14 parameters, one per CLI option (`fdd finding update`, `adjust propose`,
  `review confirm`). They carry `# noqa: PLR0913` and `# NOSONAR` with that reason instead of an options object.
- **Why**: Typer reads a command's options from its parameters; folding them into one object would hide the CLI's
  contract and change no behaviour.

## Masking registry categories

- **Decision**: every text column of a company database is `masked` (a customer's, vendor's or employee's name or
  code, a bank account number), `mask store` (`mask_values`, the real values behind tokens), or `as filed`
  (ledgers, free text, file names, fin's own codes and run ids). The masked list is what the loaders mask today;
  nothing new is masked by this spec.
- **Why**: the spec asks for "masked" or "as filed" per column; the store of real values is neither.

## Gate inputs registry

- **Decision**: the registry lists the tables and columns the gates read directly; inputs computed from many tables
  (G2's and G1's reconciliations, G3's recast runs) are covered by those runs' own input hashes and listed as such.
  The test changes each listed column and expects the gate's input hash to change (the hash is what reopens an
  approved gate). It lives with the gates in `fin.diligence`, not `fin.validate` (which holds the document checks).

## Sonar findings: 21 open in src, not 8

- **Decision**: SonarCloud showed 21 open issues in `src/` when this was built, more than the spec's 8. All were
  fixed in code except two kept with a reason: the session cookie without `Secure` (`fin ui` is plain http on
  127.0.0.1, and Safari drops Secure cookies there; it stays open although the line is marked NOSONAR, so the
  owner marks it in SonarCloud) and `dataclasses.replace` returning
  the type it is given (S5886 reads it as a generic dataclass).
- **Why**: the spec's acceptance is "no open issue in src/"; the count had grown since it was written.

## The CLI and docs test

- **Decision**: a command is documented when docs/product.md names it in backticks (`fin a b|c` names both); an
  option is documented when its flag appears anywhere in docs/product.md. The reverse check covers commands only: an
  option name in prose cannot be tied to one command. "An option the mode ignores is refused" is tested on the
  commands that have modes (ingest, report).

## The type check over all of src/fin

- **Decision**: CI's first pyright run reported 54 errors in files this PR had not touched. They are fixed in code:
  a single row that must exist (an aggregate, `INSERT ... RETURNING`) is read through `fin.db.rows.fetch_one`,
  which raises E012 instead of handing back None; the other fixes narrow types without changing behaviour. The three
  PDF add-on adapters carry `# pyright: reportMissingImports=false`: their engines are optional extras that the
  typecheck job does not install, and nothing else in those files is exempt.
- **Why**: the spec asks for a green type check job, with the fixes in the PR that turns it on.

## The categories tally reads gh's JSON from standard input

- **Decision**: `gh pr list --state merged --json number,body | node tools/tally_categories.mjs`. The script starts no
  program, so it does not depend on what PATH finds (Sonar S4036).
