# Decisions: d8-query-export

Product-level choices for the query export (docs/diligence.md §7 D8), written 2026-11-25 by the builder. Each is a builder's reading until the owner confirms or changes it.

## 1. Only approved queries are exported; export does not change status

- **Decision:** `fin query export` takes every approved query of the open engagement (E858 if none). Sent, answered and closed queries are not re-exported. It changes nothing: only the user's `fin query sent` moves a query on.
- **Why:** Plan: "for the approved queries". An approved query is one the user has not yet sent; re-sending is the user's call (approve is not repeatable, so a re-export means a new query).
- **How to change it:** `diligence/export_data.py:approved_issues` (the `status = 'approved'` filter).

## 2. What "marked-up in the source layout" shows

- **Decision:** One workbook: `Summary`; `Q<id>` per issue (its lines, and the count and value of each kind of difference); and `S<n> <sheet>` copies of the cited sheets from the archived source workbooks with each cited row highlighted whole (a cited cell: that cell) and "Q<id>: <kind>" written beside it. The rows highlighted are the ones the lines' source references name: a line names at most five rows per sheet, so further rows of one invoice are not highlighted. A source that is not a workbook (a bank PDF) is named in the lines, not marked. Queries from a finding or raised by hand have no lines: their sources text is marked the same way.
- **Why:** The plan asks for difference lines "highlighted in the source layout, with the count and value per difference". A copy of the cited sheets is the source layout the accountant knows, with real names as the company filed them.
- **How to change it:** `diligence/export_xlsx.py:source_marks` (what is marked), `_source_sheets` (copy and colour); the five-row limit is `reconcile/base.py:SHOWN_ROWS`.

## 3. How a difference is labelled, counted and valued

- **Decision:** Per issue, lines are counted and valued by kind: `left_only` is "only in the <first source>" (register, Ledger Vouchers or payroll), `right_only` "only in the <second source>" (books or trial balance), `difference` "amounts differ". The value is the sum of the lines' differences without their signs, as the query's amount is.
- **Why:** The same unsigned sum `fin query raise --items` already uses for the amount, so the figures in the workbook, email and brief agree with `fin query list`.
- **How to change it:** `diligence/export_data.py:label` and `approved_issues`.

## 4. The email and brief describe and ask; they judge nothing

- **Decision:** The email is fixed text (subject, one numbered entry per issue: period, amount, Claude's drafted question, count and value per kind, the sheet to look at, and "Please reply with what explains each, or the document that shows it") with no sign-off name. The brief (`--brief`, instead of the workbook and email) has the question, the count and value per kind, the first 20 lines and the sources; further lines are counted, not listed, to stay within two pages.
- **Why:** Plan wording ("a draft summary email as text", "a 1-2 page Word brief per issue"); the narrative rule is describe, don't judge.
- **How to change it:** `diligence/export_docs.py:email_text`, `write_brief` (`BRIEF_LINES`).

## 5. A token with no real name stops the export

- **Decision:** Tokens in a query's wording or lines are replaced by the real value from `mask_values`. A token with no real value (a mistyped one) is E861 and nothing is written, so a token is never sent as if it were a name.
- **Why:** The export is the one place real names appear; an unresolved token would reach the accountant looking like a name or leak that masking exists.
- **How to change it:** `diligence/export_data.py:Unmasker`.

## 6. Reply files: what fin reads

- **Decision:** `fin query answer --file` archives any file like a source. A plain-text reply (.txt, .eml, .md) is read and, with the names fin knows replaced by tokens, becomes the answer unless `--text` is given. Excel, Word and PDF replies are not read: `--text` must say what they answer (E860). Names fin does not know (for example the accountant's own signature) are not masked.
- **Why:** The plan says only "records a reply file (archived like any source)"; fin must not print real names, and reading arbitrary formats is extraction work the project avoids.
- **How to change it:** `diligence/answers.py:record_reply` (`TEXT_SUFFIXES`).
