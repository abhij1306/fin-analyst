# Decisions: eval-kit (golden evaluation, `fin eval`)

Builder readings, not owner decisions. Owner source for the workstream: the owner's brief of 2026-11-25 (the client's
own agent installs fin, builds a golden set from real documents, runs the eval and shares the results).

## Only lines a person checked are scored; `fin eval init` leaves the value empty

- **Decision:** `fin eval init` lists what to check with its source and unit, but leaves `expected` and `checked`
  empty. A line is scored only when `checked` is `yes` and it has a value. The runbook lets the agent draft values
  from the document (never from fin's output); only the person marks them checked.
- **Why:** Claude does not verify its own reading. A template prefilled with fin's own values would invite a person to
  confirm fin's reading instead of the document's.
- **How to change it:** `src/fin/evals/scaffold.py: candidates` (fill `expected`); `docs/EVAL.md` step 4.

## Exact match in rupees, no tolerance

- **Decision:** The expected value is typed as printed with its unit, converted to rupees (rounded half up to the
  paisa, as ingest does), and must equal fin's value exactly.
- **Why:** The brief asks for exact match; fin stores the figure as printed, so any difference is a reading or
  mapping difference worth seeing.
- **How to change it:** `src/fin/evals/score.py: score_check`.

## Statement checks score current figures

- **Decision:** A `statement` line is compared with `current_facts` (loaded documents, one value per account,
  period and scope: what metrics and answers use). A figure only in a held document scores `missing`, with the
  marker `held`.
- **Why:** The eval measures what the user gets from fin; a held figure is not in answers until the user decides.
- **How to change it:** `src/fin/evals/score.py: _FIN_VALUE` (use `document_facts` to include held documents).

## What each diligence check compares

- **Decision:** `tb_ledger`: closing debit less closing credit of the ledger in the period's loaded trial balance
  (debit positive). `tb_vs_statements`: the account's difference (trial balance less statements) in the period's
  latest run. `stub`: the account's `stub_figures` value in the latest engagement with a stub.
- **Why:** These are the D2 outputs as fin stores them; debit-positive matches how a person reads a Tally trial
  balance.
- **How to change it:** `src/fin/evals/score.py: _FIN_VALUE`.

## The score counts missing as not passing; unchecked lines are left out

- **Decision:** Score = passes over (pass + fail + missing). Unchecked lines are counted and shown, never scored.
- **Why:** A figure fin did not load is as much a gap as a wrong one.
- **How to change it:** `src/fin/evals/score.py: summary`.

## Codes shown beside a line that does not pass are related, not causes

- **Decision:** A failing or missing line lists the codes of open and accepted findings on the same period (and
  scope), and the markers `held`, `not_reconciled` (no reconciliation for the period) and `unmapped_ledgers` (its
  latest reconciliation had ledgers not mapped).
- **Why:** fin does not record which failure caused a figure to be missing; these are the nearest facts it has.
- **How to change it:** `src/fin/evals/counts.py: related_codes`.

## Counts in an eval

- **Decision:** Error codes are counted from audit events of runs whose arguments name the company (`fin
  ingest-batch` runs, which name a folder, are not counted). Claude-read figures are counted by document status
  (loaded, held) and by the latest `fin verify` outcome on each (agree, differ, not verified): counts only, trust
  stays the user's decision. Unmapped labels are distinct printed lines with no confirmed account in loaded and held
  documents.
- **Why:** These are the signals the brief lists, from data fin already keeps.
- **How to change it:** `src/fin/evals/counts.py: counts`.

## The bundle carries no names and, by default, no figures

- **Decision:** Companies are `company-1`, `company-2`, ... in the order of all company names in the data folder (an
  alias changes when a company is added). A ledger shows only as its mask token; any other ledger as `ledger-1`, ...
  No file names, labels, source text or notes; the source shows only as its kind (`sheet_cell`, `sheet_row`,
  `page`). Before writing, fin looks for every masked party name, company name and file name in the text and
  refuses (E833) when one is there. Without `--with-figures` a failing line shows only its relative difference
  (fin less expected, over the expected value, as a percentage to two places).
- **Why:** The bundle leaves the client's Mac. Ledger names that are not tokens can still name people ("Loan from
  ..."); file names often name the company.
- **How to change it:** `src/fin/evals/bundle.py: _failures, _names, make_bundle`.

## `pytest --golden` is replaced by `fin eval`

- **Decision:** The private golden set is the per-company `golden.csv` scored by `fin eval run`, not a pytest option
  (which was planned but never built). The extraction backlog's open questions moved to `docs/EVAL.md` section 8 as
  what the eval should reveal; `fin eval init` notes each account where fin adds several printed lines, so the
  "which line is the total" question gets checked.
- **Why:** The client's agent runs fin, not the test suite, on real documents.
- **How to change it:** `docs/EVAL.md`; `src/fin/evals/scaffold.py: candidates` (the note).
