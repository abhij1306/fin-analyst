# FDD F8: FDD findings report

Builder's readings of [fdd.md](../fdd.md) F8, not owner decisions. Scope readings are in
[fdd-scope.md](fdd-scope.md).

## A finding's amount comes only from linked evidence

- **Decision**: a finding has no amount field. Its amount is the linked adjustment's amount, the linked net debt item's
  amount, or a linked reconciliation run's lines at or above the threshold (the sum of their absolute differences in
  rupees: the line's rupee difference, so a foreign-currency line with no rupee value is left out); at most one of
  these. With none, it is the linked query's amount; with no link, it has no amount. The report shows the
  source next to the amount (for example "adjustment #3 (proposed)"), whatever the linked item's status.
- **Why**: the plan says "amount (only from linked evidence)", and CLAUDE.md forbids typed numbers. For a run, signed
  differences would cancel out, so the absolute sum of the lines that need a query is the size of the issue (as the
  F6 tax section does). Showing a proposed adjustment's amount with its status keeps the user's decision visible
  instead of hiding the figure.
- **How to change it**: `db/schema/fdd_findings.sql`: view `fdd_finding_rows`.

## Finding text is held to the report's narrative rules

- **Decision**: a finding's title, observation and implication may not hold a digit (outside `FY25`-style labels and
  name tokens such as `CUST-0001`, which are identities, not numbers) or valuation, investment-rating or buy/don't-buy
  language (E970); the FDD narrative lint allows tokens the same way; evidence may hold page numbers and file names. Names
  in all of them are replaced by their tokens.
- **Why**: the findings are printed in the report, so they follow the narrative rule (numbers only as references).
  Findings have no references, so the amount column carries the number. This also refuses standard names with digits
  ("Ind AS 115", "section 43B"), as the narrative lint already does.
- **How to change it**: `fin/fdd/findings.py`: `_check`.

## What counts as valuation, rating or buy/don't-buy language

- **Decision**: the FDD lint (E972) refuses phrases, not single words: "valuation" (except actuarial, inventory or stock
  valuation), enterprise, equity, purchase, intrinsic or fair market value, "valued at", over/under/fairly valued,
  EV/EBITDA, P/E, earnings/EBITDA/revenue multiples, investment/buy/sell/hold rating or recommendation, investment
  grade, good/attractive/poor/... investment, "worth buying", should (not)/do not buy, acquire or invest, "proceed with
  the deal", recommend/suggest/advise buying, acquiring or investing, and the word "buy" (not "buy-back"). "Buyer", "fair value", "price" as a deal consideration, "recommend"
  and severity words pass.
- **Why**: the owner allowed severity, implications and deal considerations (2026-11-25) but not valuation, an
  investment rating or buy/don't-buy language. A word list ("rating", "value", "sell") would refuse normal FDD text
  (credit rating, fair value of investments, sell-side), so the lint names the judging phrases. It cannot catch every
  wording; the user reads the report before it is used.
- **How to change it**: `fin/report/lint.py`: `JUDGING`.

## Final is the user's, and only final findings are in the report

- **Decision**: findings are draft, final or dropped. Claude proposes, updates and drops drafts only; the user marks a
  draft final, or returns a final one to draft (`fin fdd finding final --undo`) before it can change. The gate-guard
  hook asks the user before `finding final`. The report shows final findings; `--drafts` adds the drafts and labels the
  copy "Draft copy". Dropped findings stay in the table, out of the report. A final finding's amount, source, link
  status and response are snapshotted when it is made final ([fdd-review-fixes.md](fdd-review-fixes.md)).
- **Why**: the plan says only the user marks a finding final and only final findings appear unless `--drafts`. A final
  finding that Claude could edit would no longer be the one the user approved.
- **How to change it**: `fin/fdd/findings.py`: `set_status`, `update`; `.claude/hooks/gate_guard.py`.

## Management response, severity order and sections

- **Decision**: the management response is the linked query's answer (with its source), "no answer yet" with the
  query's status, or "no query raised". Key findings are grouped high, medium, low, then by id. Each finding has one
  area, which is the report section it belongs to; the report lists all findings under key findings, not again in
  each section. A section with no data prints "Not loaded" with the command that loads it.
- **Why**: the plan takes the response from the query's answer. One list keeps the findings in one place; the
  sections keep the figures.
- **How to change it**: `fin/report/fdd_tables.py`: `key_findings`, `_finding`.

## Incomplete inputs are shown, not hidden

- **Decision**: the FDD tables keep what the workstreams flag: a contract term Claude did not read (change of control,
  an unknown end date) shows "not read", never "no"; the receivables ageing shows the invoices in no bank run (not
  aged); the gratuity table shows the employees not computed and states the [fdd] assumptions with their values; the
  visibility table shows the items left out and the notes `fin visibility` prints. Free text from readings, findings
  and answers is HTML-escaped so it shows as text.
- **Why**: a total shown without what it leaves out reads as complete, which the evidence does not support.
- **How to change it**: `fin/report/fdd_tables.py`.

## Figures the FDD narrative can reference

- **Decision**: the diligence report's figures plus `net_debt`, `net_debt_cash`, `net_debt_debt`,
  `net_debt_debt_like` (per period end of the engagement and the stub, view `net_debt`) and `effective_tax_rate`
  (`tax_charge`, the report's scope). Other FDD tables (ageing, gratuity, visibility) are shown but not referable.
- **Why**: these are the per-period headline FDD figures; the others are per bucket, employee or source, and a
  reference has only a name and a period. NEEDS CLIENT CONFIRMATION (which other FDD figures the narrative should
  quote).
- **How to change it**: `fin/report/fdd_data.py`: `FDD_FIGURES`, `collect`.

## Scope and limitations text

- **Decision**: the report states that fin did not do an actuarial valuation (gratuity is indicative and undiscounted),
  leave encashment, a legal review of contracts or litigation, site visits, or MIS/KPI reconciliation (inquiries only),
  and that Claude's readings are not verified by fin.
- **Why**: the plan lists actuarial valuation, legal review and site visits; the others are the scope readings in
  [fdd-scope.md](fdd-scope.md).
- **How to change it**: `templates/report/fdd.qmd`: "Scope and limitations".
