---
description: A diligence engagement: accountant queries and EBITDA adjustments with evidence
argument-hint: <company> [what to do]
---

Diligence work on a company: $ARGUMENTS

Read the "Diligence" rules in `CLAUDE.md` first. In short: a difference in the company's data is queried, never
fixed; you draft and propose, the user decides; fin never sends anything.

1. **Where it stands.** Run `uv run fin engagement status <company>`. If it fails with E803 (no open engagement), ask
   the user which annual periods are in scope (e.g. FY24,FY25,FY26) and whether to use the default query threshold
   (`query_threshold` in `config.toml`), then run `uv run fin engagement open <company> --periods FY24,FY25,FY26
   [--threshold N]`. Report the engagement, the queries by status and the adjustments by status with their sums,
   from the command output.
2. **Queries from findings.** Run `uv run fin findings list <company> --json`. For each open or accepted finding in
   a period of the engagement, with a difference at or above the engagement's query threshold, draft a short,
   neutral question for the company's accountant: what the two figures are, where each is (file + sheet!cell or
   page), and what explains the difference. Do not suggest an answer. Raise it with
   `uv run fin query raise <company> --finding <id> --question "..."`: it copies the finding's period, scope,
   difference and sources; the finding is not changed. A finding below the threshold: list it for the user, do not
   raise it unless they ask.
   - A difference found another way (not a finding): `uv run fin query raise <company> --amount <rupees> --period
     <FY25> [--scope standalone|consolidated] --source "<file> <sheet!cell or page>" --question "..."`. The amount
     comes from `fin` output; never compute it in prose.
   - To change the wording of a draft: `uv run fin query edit <company> <id> --question "..."`.
3. **Show the drafts** (`uv run fin query list <company> --status draft`) with their amounts and sources, and ask the
   user to approve them. Only when the user approves: `uv run fin query approve <company> <ids...>`.
4. **Sending is the user's.** fin never sends anything. Run `uv run fin query sent <company> <ids...>` only when the
   user says they sent those queries.
5. **Answers.** When the user gives an accountant's answer: `uv run fin query answer <company> <id> --text "..."
   [--source "<email, file or page>"]`. Then propose what follows (close it, raise a follow-up, or an adjustment)
   and close it only when the user says: `uv run fin query close <company> <ids...>`. To drop a query before it is
   closed, when the user says: `uv run fin query withdraw <company> <ids...> --reason "..."`.
6. **Adjustments.** Propose an adjustment only with evidence: an answered query, a finding, or a source reference.
   `uv run fin adjust propose <company> --period <FY25> --scope standalone|consolidated --category <category>
   --amount <rupees> --description "..." [--account <account>] [--query <id>] [--finding <id>] [--source "..."]`.
   - The amount is the signed effect on EBITDA in rupees (positive raises it), and must come from `fin` output or
     the source; never do arithmetic in prose.
   - Categories are `adjustment_categories` in `config.toml`; the period must be one of the engagement's; an
     account must be in `uv run fin map accounts`.
   - Describe what the item is and the evidence; do not judge the company.
7. **Decisions are the user's.** Show the proposed adjustments (`uv run fin adjust list <company> --status
   proposed`) and accept or reject only the ones the user names: `uv run fin adjust accept <company> <ids...>`,
   `uv run fin adjust reject <company> <ids...> --reason "..."`.
8. **Never** approve, mark sent, close or withdraw a query, accept or reject an adjustment, or approve a gate on your
   own. Never change the company's data to make a difference go away; fixing fin's own reading (a wrong mapping, a
   misread figure) is still done through `/ingest` and `/review`. If a command fails (E803 no open engagement, E804
   an adjustment that is not valid, E805 an unknown id or a status change not allowed), report the error code and
   run_id and stop.
