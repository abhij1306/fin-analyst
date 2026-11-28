# SPEC-17: Changes from the client's answers

## Summary

The client answered the 14 standing questions on 2026-11-27 ([client-answers.md](../decisions/client-answers.md)).
Nine answers confirm a default. One (amounts in INR million) is SPEC-06. This spec holds the four that change fin
and one that changes what is shown: mask bank account numbers, value USD orders, read the client's own inquiry
list, keep credit terms on the engagement, and show the recast against the books.

## Scope

1. **Mask bank account numbers** (answer 1). On load, a bank statement's account number is stored once in
   `mask_values` under a new kind and replaced by its token in the data tables, `fin` output, logs, exports and
   reports, like the fields masked today ([masking.md](../decisions/masking.md), option 3). The same number gets
   the same token in every file, so statements of one account still join. The saved parse of a bank PDF
   (`cache/bank/*.json`) holds the token too, not the number. The original PDF and its evidence crops show the
   number as filed; that limit is stated in the docs. No migration (greenfield, the owner, 2026-11-28): a company whose bank
   statements were loaded before is deleted (`fin delete <co>`, which also removes its saved parses) and its files
   ingested again.
2. **USD orders are valued** (answer 10). An orderbook order in USD is converted at a rate the user states for the
   engagement and its as-at date (`fin engagement rate <co> USD <rate> --as-at <date>`), recorded with the actor in
   the audit log. Converted amounts are marked with the rate used. With no rate stated the order stays "not
   converted", as today ([fdd-f7.md](../decisions/fdd-f7.md)). fin never fetches or assumes a rate. Any other
   currency stays "not converted" until a rate is stated the same way.
3. **The client's own inquiry list** (answer 12). `<data folder>/templates/inquiries.toml`, when present, is used
   instead of `templates/fdd/inquiries.toml`; same format, same checks (E910). `fin query checklist` says which
   list it read. A list received from a third party stays in the data folder and is never committed.
4. **Credit terms are 90 days and belong to the engagement** (answer 6). The default of
   `[diligence] invoice_due_days` becomes 90. `fin engagement open --due-days N` sets another value for one
   engagement; it is shown in `fin engagement status` and on the engagement screen (SPEC-09);
   overdue and ageing read the engagement's value. Changing it reopens the gate over the figures it feeds.
5. **Recast against the books** (answer 9). The company books revenue on billing; the recast spreads it over the
   service days. Per period, the difference between revenue in the books and recast revenue is one headline row of
   the differences list (SPEC-15), labelled as a recognition difference, with the invoices behind it as children.

Out of scope: masking locations or product names (not asked for); fetching exchange rates; a gratuity change
(answer 3); new GST logic (answer 8).

## Open issues

1. Answer 5 says the books are before GST; fin's note says receivables in the books include GST. Check on the
   client's receivable ledger at the second run (human task H3) before changing anything.
2. Unanswered parts, defaults kept: part years for gratuity (3), set-offs and reverse charge (8), the prior-year
   sales register (9), open or all orders and an order number in the register (10), totals over the threshold (11).

## Acceptance criteria

- [ ] After loading the synthetic bank statements, no table other than `mask_values`, no `fin` output, log line,
      saved parse, export or report holds an account number; the masking registry test ([engineering.md](../engineering.md)
      rule 5) covers the new kind.
- [ ] Two statements of one account carry one token and still pass the continuity checks.
- [ ] A USD order shows no rupee amount without a stated rate, and the stated rate times its value with one; the
      rate and who stated it are in the audit log.
- [ ] With a list in the data folder, `fin query checklist` drafts from it and names it; with a bad one it stops
      with E910.
- [ ] Two engagements with different `--due-days` give different overdue counts on the same synthetic register.
- [ ] The recognition difference of a period equals books revenue minus recast revenue (SQL test).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Mask bank account numbers | `diligence/mask.py`, `diligence/bank.py`, schema file, docs | criteria 1, 2 |
| 2 | Value USD orders at a rate the user states | `diligence/orderbook.py`, engagement, schema file | criterion 3 |
| 3 | Read the inquiry list from the data folder when present | `diligence/inquiries.py` | criterion 4 |
| 4 | Make credit terms 90 days by default and settable per engagement | engagement, bank matching, ageing | criterion 5 |
| 5 | Show the recast against the books in the differences list | SPEC-15 view | criterion 6 |

Risk: PR 1 (masking, schema), PR 2 (a figure the user states, schema) and PR 4 (gate inputs) are high: a separate
reviewer. PR 5 waits for SPEC-15 PR 1.
