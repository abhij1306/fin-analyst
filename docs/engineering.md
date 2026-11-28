# Engineering guidelines

How code is written in this repo, drawn from the defects found so far: 693 SonarCloud findings, 389 review-bot
comments on 113 merged PRs, the review rounds in [decisions/](decisions/) and the first client run. A rule is here
because its failure happened more than once. `CLAUDE.md` outranks this file. What to build is in
[specs/](specs/README.md); how a spec becomes a merged PR is in [process.md](process.md).

Each rule names what enforces it. "To add" means the check is a row of
[SPEC-00](specs/SPEC-00-guardrails.md); until it exists the author's self-review applies the rule. When a mistake
recurs, add the strongest check that can catch it, in this order: a type, a lint rule, a CI check, a test. A
sentence in this file comes last ([plans/RECURRING.md](plans/RECURRING.md)).

## What the findings were

| Source | Count | What it was |
|---|---|---|
| Sonar, SQL files read as Oracle PL/SQL | 357 | False positives. The `.sql` files are DuckDB; they are excluded from analysis now. |
| Sonar, test style (split assertions, one call in `pytest.raises`) | 174 | 88 still open, all in tests. See rule 20. |
| Sonar, function complexity over 15 | 51 | Took three clean-up PRs. One open (`ui/chat_routes.py`, 70). |
| Sonar, CI and install supply chain (unpinned actions, unlocked installs, install scripts) | 53 | Fixed; rule 18. |
| Sonar, regular expressions (backtracking, complexity) | 11 | Fixed; rule 17. |
| Sonar, everything else | 47 | Nested conditionals, too many parameters, small idioms, one path traversal, one cookie flag. |
| Review bots | 389 | Logic error 63, API mismatch 44, wrong condition 37, security 27, incomplete 24, comment mismatch 16, race 12. |

Sonar found style. The bots and the review rounds found the defects that change a figure or leak a name; rules 1 to
10 are those.

## Data correctness

1. **Missing is not zero.** A missing or unreadable input stays NULL and carries a warning to the total it would
   have fed; it is never counted as 0 or dropped without a line saying so. (Blank payroll counted as zero, #78;
   missing vouchers reported as zero interest, #100; rows with unparseable dates dropped, #69, #75.)
   *Enforced by:* a test with incomplete input for every new aggregate view (review checklist).
2. **A figure has one unit, and it is in the column.** Money is stored in rupees; per-share, ratio, days and count
   values are never given a money unit; currencies are never summed. Conversion for display happens in SQL in one
   place. (#76, #80, #85.) *Enforced by:* to add with SPEC-06 (a test that fails on division of a stored value in
   Python or a template).
3. **Always the latest version.** A query that reads documents, matches or approvals goes through the view or helper
   that picks the current version. (#81 used the oldest archived workbook; #76 combined two engagements.)
   *Enforced by:* a test that loads the same file twice for every new reader (review checklist).
4. **A gate covers everything it depends on.** Each gate names its input tables and columns in one registry, and
   changing any of them reopens it. (#77, #84, #85 and #105 twice.) *Enforced by:* to add (gate registry test).
5. **Every text column has a masking decision.** A new column that can hold a name is either masked or recorded as
   "as filed" in the masking registry. (#69, #73, #82, #101, #112.) *Enforced by:* to add (masking registry test).
6. **Every number shown comes from SQL.** No arithmetic in Python formatting, templates or prose
   (`CLAUDE.md`). *Enforced by:* the report linter (E602, E972) for narrative; to add for templates.

## Boundaries

7. **Escape at output, through one helper.** Text from a document reaches Markdown, HTML and Excel only through the
   shared escaping functions. (HTML injection in reports, #102, #104, #105; Excel formula injection, #81.)
   *Enforced by:* to add (`fin.output.escape` and its test).
8. **A path from a file, a request or a database row is checked against its root** after resolving symlinks.
   (#37, #108.) *Enforced by:* to add (`fin.paths.inside`); Sonar S2083.
9. **Temporary files have unique names and replace atomically.** No fixed `*.new` or `*.tmp` path shared by two
   runs. (#13, #54, #64, #90.) *Enforced by:* to add (`fin.paths.atomic_write`, lint ban).
10. **Enforce a rule in the function, not by matching the command.** A guard that inspects a shell command or a SQL
    string can be walked around (#45, #51, #69, #102). The refusal belongs in the Python function that does the
    work, where every interface meets it. Hooks are a second line, not the rule.
    *Enforced by:* a test per refusal at the function; known bypasses kept as test cases.
11. **Validate at the edge, then trust the types.** CLI arguments, config, uploaded files, database rows and model
    output are parsed into typed values once. Inside, no re-checking and no `dict` of optionals passed three
    levels down. *Enforced by:* to add (type check in CI).

## Code shape

12. **Thin commands.** A CLI command or UI handler parses, calls one function and renders. Logic lives in a function
    both interfaces call ([SPEC-02](specs/SPEC-02-services-and-jobs.md)). The UI's action functions repeat CLI glue
    today; do not add a third copy.
13. **A function stays under complexity 15 and 13 parameters.** Extract named steps; replace a chain of branches
    with a table; pass a dataclass instead of the 14th parameter. No nested conditional expressions.
    *Enforced by:* Sonar (S3776, S107, S3358); to add in ruff (`C901`, `PLR0913`) so it fails before the push.
14. **A file stays under 6,000 tokens.** Split along a seam before the limit, not at it. Five files are within
    1,000 tokens of it (`ingest.py`, `audit/errors.py`, `map/commands.py`, `map/review.py`,
    `validate/gates.py`): a PR that adds to one splits it first.
    *Enforced by:* `tools/check_file_size.mjs` (pre-commit and CI).
15. **An option the chosen mode ignores is refused.** A command never accepts a flag and does nothing with it, and
    never exits 0 when the work did not happen. (#13, #54, #93, #107.) *Enforced by:* to add (CLI test).
16. **Every failure has an error class and a code,** registered in `docs/errors.md` first. No bare `except`, no
    silent default. An exception with no class is a defect. *Enforced by:* ruff `BLE`, the error-table test.
17. **Regular expressions are simple and linear.** No nested quantifiers over the same characters; split a pattern
    over complexity 20 into steps. *Enforced by:* Sonar (S8786, S5843).

## CI and install

18. **Pinned and locked.** Actions by commit SHA; `uv sync --frozen`; `npm ci --ignore-scripts`; downloads over
    HTTPS with a pinned version. *Enforced by:* Sonar (S7637, S8544, S6505).
19. **A test that never runs is not a test.** A test that skips when an optional extra is missing must be named in
    a CI job that installs the extra. (24 UI tests skip in every CI job today.) *Enforced by:* to add (SPEC-00 PR 1).

## Tests

20. **Test behaviour with exact values.** Call the code as its user does and assert the exact result. A test that
    would still pass if the function returned a wrong value is rewritten or deleted. One call inside
    `pytest.raises`: set up outside the block, so the test proves which call raised (S5778). Splitting composite
    assertions (S9073) is not worth a PR; the rule is to be switched off in the Sonar profile
    ([specs](specs/README.md) Q8).
21. **A behaviour change updates its tests in the same PR.** Most red CI runs so far were a test left behind by a
    change somewhere else (masking, doctor order, the error table). Search for the old behaviour's tests before
    pushing. If editing a test turned CI green, that test is suspect: rewrite it to assert behaviour.
22. **Synthetic fixtures only.** A defect found on a client's documents is rebuilt as a synthetic fixture and an
    eval check ([eval-kinds.md](eval-kinds.md)) before it is fixed.

## Docs

23. **Docs say what the code does today.** `docs/product.md` changes in the PR that changes behaviour; a spec says
    what will be built and is not edited to match the code afterwards. Do not restate code in docs or comments;
    a comment says why. (16 comment-mismatch findings; five docs-fix PRs.) *Enforced by:* to add (CLI and docs test).

## Review checklist

The author runs this on the diff before opening a PR. It is the whole review for a small, low-risk PR.

- [ ] Each acceptance criterion of the spec rows maps to a test or command that ran.
- [ ] Rules 1 to 5 for any new view, reader, gate input or text column.
- [ ] Rules 7 to 10 for any text that reaches output, any path, any temporary file, any refusal.
- [ ] Each new test fails when the code returns a wrong value.
- [ ] Tests of the old behaviour were found and updated.
- [ ] `docs/product.md`, `docs/errors.md` and `docs/development.md` match the change; nothing else was edited.
- [ ] Nothing outside the rows' scope; no option, guard or helper without a caller.
- [ ] [plans/RECURRING.md](plans/RECURRING.md) read; the PR body ends with its `Categories:` line.
