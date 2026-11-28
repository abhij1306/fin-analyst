# Development process

How a spec becomes merged code with the owner involved as little as possible, and how each round leaves fewer
defects for the next. Rules for the code: [engineering.md](engineering.md). What to build:
[specs/](specs/README.md). Live status: [plans/ACTIVE.md](plans/ACTIVE.md). Running several agents at once: the
`orchestrate` skill, which takes its repo specifics from this file.

## Who does what

| Role | Does | Never |
|---|---|---|
| Owner | Answers the open decisions before a phase; reads the phase report; does human-only tasks | Reviews PRs, watches CI |
| Orchestrator (the main session) | Picks ready rows, starts builders, runs the merge queue, runs the retro | Writes product code |
| Builder | Takes one PR from a ready row to green | Merges out of turn, widens scope |
| Reviewer | Takes over a large or high-risk PR | Reviews small, low-risk PRs |

## When the owner is asked

Only for these. Everything else is decided by the agent, recorded and continued.

1. **Before a phase:** the "Owner decisions" table in [specs/README.md](specs/README.md). Each has a default; no
   answer means the default is built and stays marked as a reading.
2. **After a phase:** the phase report (below), in one message.
3. **Human-only tasks** in [plans/ACTIVE.md](plans/ACTIVE.md): accounts, secrets, anything sent to the client.
4. **Stops:** an action that cannot be undone or that reaches outside the repo, a new paid service, a change to a
   rule in `CLAUDE.md`, or a question only the client can answer (it goes to
   [client-questions.md](client-questions.md), and the work continues on the default).

A builder that hits one of these ends its turn with `QUESTION <id>: ...`; the orchestrator answers from the specs
and decisions where it can, and asks the owner only when it cannot.

## From spec to merged PR

1. **Ready.** A row of a spec's PR plan is ready when the code it builds on is merged and its spec has no open
   decision without a default. The orchestrator reads ready rows from [plans/ACTIVE.md](plans/ACTIVE.md) and open
   work from GitHub (open PRs and branches), not from the tracker.
2. **One PR per spec,** or per group of rows that can ship together (the CLI rows, then the UI rows). Split only
   where a later row waits on another spec. There is no line target; a PR over 1,500 changed lines gets a separate
   reviewer (step 5). (Owner, 2026-11-28: small PRs are a waste of time.)
3. **Build.** The builder reads `CLAUDE.md`, [engineering.md](engineering.md), the spec and
   [plans/RECURRING.md](plans/RECURRING.md). For each unit: write the failing test, see it fail for the right
   reason, make it pass, commit. Run only the tests for the files changed, and ruff on changed files. No full suite
   locally; CI runs everything.
4. **Self-review** with the checklist at the end of [engineering.md](engineering.md), then open the PR from the
   template. The body ends with a `Categories:` line (below).
5. **Review by risk.** A separate reviewer takes any PR that touches gates or approvals, masking, a schema file,
   `install.sh`, stored actor values, API keys or what leaves the machine, and any PR over 1,500 lines. Each spec
   marks its high-risk rows. Everything else is finished by its builder.
6. **Bots and CI, once.** Wait for the whole CI run and for the review bots to post, or 20 minutes after the last
   push, whichever is first. PRs so far merged after a median of 13 minutes while a bot review takes 5 to 14, so
   findings arrived after the merge and came back as fix PRs. Triage every finding once: fix the valid ones in one
   push, list the declined ones with a reason in the body, never reply on a bot thread.
7. **Merge** in the queue's turn, on every check green, with `--match-head-commit`. The PR itself marks its row
   `done (#n)` in [plans/ACTIVE.md](plans/ACTIVE.md) and adds product decisions to
   `docs/decisions/<spec>.md` as readings ([decisions/README.md](decisions/README.md)).

## The loop that removes defects

Every defect is a signal: a bot or reviewer finding, a red CI run, a Sonar issue, a failure in a client's logs, a
correction from the owner. The loop turns a repeated signal into a check, so the next builder cannot make the
mistake, instead of a sentence the next builder has to remember.

1. **Tag.** Each PR body ends with `Categories:` and the kinds of defect that were found in it by anyone (the
   author's own review, a bot, CI), or `none`. Use these words so they can be counted:
   `missing-as-zero`, `unit-or-sign`, `stale-version`, `gate-input`, `masking`, `escaping`, `path`,
   `temp-race`, `guard-bypass`, `cli-contract`, `error-code`, `complexity`, `docs-drift`,
   `test-left-behind`, `weak-test`, `ci-config`, `install`, `accessibility`, `other:<word>`.
2. **Retro, at the end of each spec.** The orchestrator runs it; the owner is not needed.
   - Count the categories of the spec's PRs (`node tools/tally_categories.mjs`, from SPEC-00), and list anything
     that reached `main` or a client: a fix PR, a red run on `main`, a new Sonar issue.
   - A category seen in two PRs, or anything that escaped, gets a row in
     [plans/RECURRING.md](plans/RECURRING.md) with what now prevents it. Prefer, in order: a type, a lint rule, a CI
     check, a test. A rule in [engineering.md](engineering.md) only where no check can do it.
   - Open one guardrail PR per spec with those checks. When a check replaces a sentence in
     [engineering.md](engineering.md), the sentence's "Enforced by" names the check.
   - Add the spec's row to the measures table in [plans/RECURRING.md](plans/RECURRING.md).
3. **Client runs.** Whatever a client sends back is read into a dated file under [test-run/](test-run/), with
   names and figures left out. Each failure becomes a synthetic fixture and an eval check first, then a spec row.
4. **Owner corrections** about how to build go into [plans/RECURRING.md](plans/RECURRING.md) with the owner's words
   and date, and get a check where one is possible.

**Phase report.** When a spec or a set of specs is done, the orchestrator sends one message: PRs merged, rows
skipped and why, decisions made as readings (with links), the retro's new checks, the measures against the last
round, and human tasks waiting.

## Values for the orchestrate briefs

| Brief field | Value |
|---|---|
| Repo | `abhij1306/fin-analyst` |
| Setup | `uv sync --frozen` (add `--extra ui` for UI rows) |
| Tests | `uv run pytest tests/unit/<files for the change> -x`, `uv run ruff check <files>`, `uv run ruff format <files>`; no integration tests locally |
| Migrations | None. One schema file per database; no migration files, no compatibility code |
| High risk | The list in step 5 |
| Owner docs | `CLAUDE.md`, [specs/README.md](specs/README.md) owner decisions, [decisions/](decisions/) |
| Models | Opus medium for builders and reviewers (owner, 2026-11-28: "use opus builders"); never Opus high |
