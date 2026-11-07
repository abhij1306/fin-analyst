---
description: List pending mapping items and confirm or reject them when asked
argument-hint: [company]
---

Review mapping items for the company in `$ARGUMENTS` (all companies if empty).

1. Run `uv run fin review list --json` (or `uv run fin review list <company> --json` for one company). Show each item: id, company, statement, section, label, number of facts, proposed account, confidence and reason. Flag items with no proposal and items with confidence below 0.7 (E302).
   - An item with `new_account` proposes an account that is not in the dictionary yet. Say so plainly and show its name, parent, sign, unit and description, and the reason no existing account fits. Confirming it adds the account to the shared dictionary for every company.
2. Only the user decides. Do not confirm or reject anything until the user says which items and, for confirm, the scope (`company`: this company only, `global`: every company; ask if they did not say).
3. When the user asks, run the command for the items they named. Claude Code will ask them to approve `confirm`; that is expected.
   - `uv run fin review confirm <company> <id>... [--account <account>] [--scope global|company]`
   - `uv run fin review reject <company> <id>...`
   - Use `--account` only when the user chose a different, existing account (for a new-account item this drops the new account).
   - For a new-account item, `--name <snake_case>` and `--parent <account>` change the proposed name or parent, only when the user asks.
4. Report the result and the facts updated, from the command output. If a command fails, report its error code and run_id and stop.
5. To share added accounts and global labels with someone else, `uv run fin map accounts --added` prints them as rows for `templates/dictionary/schedule_iii.csv` and `labels.csv`.
