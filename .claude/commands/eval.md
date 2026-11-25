---
description: Golden evaluation: build a person-checked golden set, score fin, and make the results bundle
argument-hint: [company] [init|run|bundle]
---

Golden evaluation: $ARGUMENTS

Follow `docs/EVAL.md` (the full runbook). In short:

1. **Golden set.** `uv run fin eval init <company>` writes or extends `evals/<company>/golden.csv` in the data folder:
   one line per figure fin has, with its source; `expected` and `checked` are empty. It never changes a line that is
   there. Draft `expected` values by reading the document at each `source` (Read tool), exactly as printed, with
   the unit; never take them from fin's output (`fin sql`, `fin statements`, `fin audit`). Show the person the drafted
   lines. Write `yes` in `checked` only on the lines the person says they checked against the document. Never mark a
   line checked yourself: Claude does not verify its own reading.
2. **Score.** `uv run fin eval run <company>` prints the score and the report path (`evals/<company>/eval-<run_id>.md`).
   Report the score and every line that is `fail` or `missing` with both values and the source, from the report.
   A failure is a question for the person: fin's misreading or mapping (fix only through `/review`, `fin map set`,
   `fin ingest --force` when the person says so), a wrong golden value (correct it with the person), or a filing
   that does not add up (a finding).
3. **Decisions review.** Read `docs/decisions/*.md` and write `evals/decisions-review.md` in the data folder
   (`docs/EVAL.md` section 6): decision, suggestion, evidence from the eval (kinds, accounts, periods, codes,
   counts; never names, file names or figures), and whether the person agrees.
4. **Bundle.** `uv run fin eval bundle [company]` writes `evals/bundle-<run_id>.md` and `.json` and prints the paths
   only. Add `--with-figures` only when the person agrees to share figures. Show the person the bundle and the
   decisions review; the person sends them. Never send anything yourself.

If a command fails (E830 no golden set: run `fin eval init`; E831 a golden set line fin cannot read: fix the lines the
message lists; E832 no company database; E833 the bundle would carry a real name: nothing written), report the error
code and run_id and stop.
