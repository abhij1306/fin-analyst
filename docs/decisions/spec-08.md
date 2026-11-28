# SPEC-08: figures the user states

Builder's readings (2026-11-28), for the owner to confirm or change.

## A conflict records a finding and stores nothing

- **Decision**: `fin fact state` for an account, period and scope a loaded document already gives stores no stated
  figure. It records a finding (check `stated_figure`, finding `document_gives`) with both values, the document and
  the user's reason, and changes no figure.
- **Why**: the spec says a stated figure "never replaces a figure a document gives (that is a conflict and becomes a
  finding)". If the stated figure were stored too, it would sit unused and look like data. The finding has no
  document id, so it never holds or releases the document.
- **How to change it**: `src/fin/facts.py: state` (the `_finding` branch).

## A document loaded later takes over

- **Decision**: when a document that gives a stated account, period and scope is loaded later, `current_facts` uses
  the document's figure. The stated one stays listed (`fin fact list`, the report's coverage table) as "not in use".
- **Why**: the same rule as above, applied in the view, so no ingest code has to know about stated figures.
- **How to change it**: `src/fin/db/schema/company_views.sql: current_facts` (the `NOT EXISTS` on `from_documents`).

## G1 lists stated figures; they do not cover a period

- **Decision**: gate G1 lists every stated figure as a passing line ("figures stated by the user, not from a
  document: ...") with its reason. A stated figure alone does not satisfy "a loaded document has mapped figures for"
  a period. The stated figures (ids and reasons) are a G1 input, so stating or withdrawing one reopens G1.
- **Why**: the spec says a stated figure is allowed in an engagement (Q3) and is "an item the user sees before
  approving", not a blocker. A period with only stated figures has no filed document behind it, which is what G1's
  period line checks.
- **How to change it**: `src/fin/diligence/gates.py: _stated_line, _data_checklist, _data_inputs`.

## The reason is the user's words, not masked

- **Decision**: the reason is stored, shown and printed as typed (surrounding spaces trimmed; a report table cell
  puts it on one line). It is not masked.
- **Why**: it is the user's own explanation, and the report must show it next to the figure. Masking applies to
  the client's fields; a reason is not one.
- **How to change it**: `src/fin/facts.py: state`, and the readers in `src/fin/report/tables.py: stated_table`.

## E505 covers every figure table

- **Decision**: `fin sql --write` refuses `facts`, `raw_values` and `stated_figures` (E505). The spec names the
  first two; `stated_figures` is added so stated figures change only through `fin fact`. E806 and the E504
  statement checks are checked first, so a statement naming both a diligence table and a figure table reports E806.
- **Why**: a write to `stated_figures` could bypass the conflict check and the audit row, which is the same side door
  the spec closes.
- **How to change it**: `src/fin/query/write.py: FIGURE_TABLE`.

## A figure fin misread is corrected with fin fact correct

- **Decision** (reviewer's reading, 2026-11-28, from the orchestrator's brief): with E505 closing `fin sql --write`,
  a figure fin read wrongly is corrected with `fin fact correct <co> <fact_id> --value V --reason "..."`. It backs up,
  logs, keeps the printed cell and text, records the value before, the reason and the actor, marks the figure as
  corrected wherever a stated figure is marked, and reopens G1 (its figures and a "corrected figures" input change).
- **Why**: CLAUDE.md keeps "fixing fin's own reading errors (a misread figure)" possible, and a held document is
  "saved as is or fixed". The spec's "existing commands" for corrections were `fin sql --write`, now refused.
- **Guard**: a correction is for fin's misread, never to make figures tie. Inside an open engagement a figure under
  a query (via a finding on the fact) or an adjustment (via a finding, or the same account, period and scope) that
  is not closed, withdrawn or rejected is refused with E409: that difference is queried. fin cannot tell a misread
  from a wish to tie in other cases; the help, `/ingest` and this rule say so, and the actor is recorded.
- **How to change it**: `src/fin/facts.py: correct, _UNDER_QUERY_SQL`.

## tb_vs_statements compares a stated figure

- **Decision** (system review, 2026-11-28): `tb_vs_statements` reads every standalone figure of `current_facts`, a
  figure the user stated included, with the source `stated #<id>`, as `fin statements`, `fin statements --rebuilt`
  and `fin reference compare` do. Before, its source join dropped a stated figure, so the trial balance line showed
  as `left_only` (the statement "missing") in the reconciliation and the Differences list while the rebuilt view
  compared it with the stated figure.
- **Why**: one set of reported figures for every comparison; a stated figure is a figure in use (this file, above),
  and G1 lists it for the user before approving.
- **How to change it**: `src/fin/diligence/recon.py: statement_figures`.

## E505 covers documents too

## E505 covers documents and the reference tables too

- **OWNER DECISION** (owner, 2026-11-28): asked whether `fin sql --write` should refuse to change the `documents` table,
  the same as the figure tables, the owner answered: "ok and document it as decisions so client can know". The same
  day the owner said "ok" to adding the reference tables (`reference_*`) to the same guard, so a reference mapping
  cannot be confirmed by SQL past `fin reference confirm`.
- **Decision**: `fin sql --write` refuses any statement other than a SELECT that names `documents` (E505), before the
  backup. fin's own commands load, hold, release and remove documents (`fin ingest`, `fin findings accept`,
  `fin review confirm`, `fin fact correct`, `fin document delete`). A write to `findings` can still release a held
  document through the recheck after it; that is open (#139, Not fixed). It also refuses `reference_files`,
  `reference_lines` and `reference_values` (E505): only the user confirms or rejects a reference line.
- **Why**: `UPDATE documents SET status = 'loaded'` released a held document into `current_facts` without its
  findings being accepted, and the stored file paths were writable too (#136). `UPDATE reference_lines SET status =
  'confirmed'` stood in for the user's confirmation, and only confirmed lines are compared.
- **How to change it**: `src/fin/query/write.py: FIGURE_TABLE`.
