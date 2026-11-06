---
description: List pending mapping items and confirm or reject them when asked
argument-hint: [company]
---

Review mapping items for the company in `$ARGUMENTS` (all companies if empty).

1. Run `uv run fin review list --json` (add `--company <company>` for one company). Show each item: id, company, statement, section, label, number of facts, proposed account, confidence and reason. Flag items with no proposal and items with confidence below 0.7 (E302).
2. Only the user decides. Do not confirm or reject anything until the user says which items and, for confirm, the scope (`company`: this company only, `global`: every company; ask if they did not say).
3. When the user asks, run the command for the items they named. Claude Code will ask them to approve `confirm`; that is expected.
   - `uv run fin review confirm <id>... --company <company> [--account <account>] [--scope global|company]`
   - `uv run fin review reject <id>... --company <company>`
   - Use `--account` only when the user chose a different account.
4. Report the result and the facts updated, from the command output. If a command fails, report its error code and run_id and stop.
5. Users who prefer a prompt loop can run `uv run fin review` themselves in a terminal.
