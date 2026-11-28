# SPEC-09: Diligence in the UI

## Summary

The client's real work was a diligence engagement: open it, bring in the adjustments from their own walk sheet, see
the bridge, approve gates ([obs. 15, 17, 20](../test-run/2026-11-27-client.md)). The UI has the decision buttons
(approve a query, accept an adjustment, approve a gate) and nothing else; opening an engagement, proposing
adjustments and seeing the bridge are chat-only. The rules in `CLAUDE.md` do not change: differences are queried,
never fixed; only the user approves, accepts or marks final.

## Scope

1. **Engagement screen.** Open an engagement (periods, threshold, stub), see its status and close it.
2. **Adjustments from a file.** `fin adjust import <co> <file.csv|xlsx> [--sheet]` proposes one adjustment per row
   (period, scope, category, account, revenue effect, cost effect, description, source reference). Each row keeps the
   file and cell as evidence; a bad row refuses the whole file and names the row. The UI offers the same as an upload.
3. **Bridge screen.** Reported EBITDA to adjusted EBITDA per period, in the display unit (SPEC-06), with each
   adjustment's status and evidence, and proposed adjustments shown apart from accepted ones.
4. **Adjusted statements.** The P&L before and after accepted adjustments, side by side. The balance sheet and cash
   flow are shown as reported, with a note, until an adjustment kind that changes them exists.
5. **Gates.** The Gates screen shows the checklist, what is open and the action that would close it. When an action
   is refused for a gate (E801), the message links to that gate.
6. **Queries.** Raise, edit, answer and export from the Queries screen. Sending stays outside fin.

Out of scope: FDD findings beyond the existing final and draft buttons (SPEC-11); changing gate rules.

## Acceptance criteria

- [ ] With no terminal and no chat, on the synthetic diligence fixture: open an engagement, import a walk sheet,
      approve G1 and G2, accept the adjustments, and read the bridge.
- [ ] An import with one bad row changes nothing and names the row.
- [ ] Every approve, accept and mark-final in the log from that walk-through has actor `user`.
- [ ] Adjusted P&L totals equal reported totals plus accepted adjustments, checked in SQL by a test.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Import adjustments from a sheet | `src/fin/diligence`, CLI, docs | criterion 2 |
| 2 | Add the engagement and bridge screens | `src/fin/ui`, services | UI tests |
| 3 | Add the adjusted P&L view and screen | `diligence.sql` views, UI | criterion 4 |
| 4 | Complete the Queries and Gates screens | `src/fin/ui` | criterion 1 |

Risk: high (gates, approvals). Every PR here gets a separate reviewer.
