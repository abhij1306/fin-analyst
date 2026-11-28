# SPEC-11: Remaining screens

## Summary

After specs 03, 05, 06 and 09 a user still needs chat or a terminal to produce a report, export, change a setting,
check the install or see what happened. This spec closes the list so that a non-technical user can do everything
that is a button, and chat is for judgement: questions, readings, proposals, narrative.

## Scope

| Screen | What the user can do |
|---|---|
| Reports | Generate a health, diligence or FDD report (a job); see its sections, findings and data coverage; open or download HTML, Word, PDF. Narrative is drafted in chat and linted before rendering. |
| Findings | Existing, plus FDD findings: see drafts with evidence, edit, drop; final and draft stay user buttons. |
| Questions | The saved questions with their SQL, result and evidence page; re-run; download. |
| Dashboards | Build and open the dashboard (a job), reversing the earlier decision to leave it out ([web-ui.md](../decisions/web-ui.md)). |
| Settings | Display unit (SPEC-06), PDF add-on, data folder path (read-only), assistant (SPEC-16, on hold). |
| Health | `fin doctor` results with the fix for each failed check; `fin update`. |
| Log | Existing, plus filters by company, command and actor, and a link from any figure to its trail (`fin audit`). |

- **Navigation.** With about 12 screens the top bar groups them: Data (Files, Mappings, Statements), Analysis
  (Questions, Reports, Dashboards), Diligence (Engagement, Queries, Bridge, Gates, Findings, References), System.
- **`fin ui` is installed by default** (SPEC-12) and the README leads with it.
- **Accessibility:** every control has an accessible name that contains its visible label; keyboard reachable.

Out of scope: anything the CLI cannot do; multi-user; remote access.

## Acceptance criteria

- [ ] A table in `docs/product.md` lists every CLI command with its screen or "chat" or "terminal only", and a test
      fails when a command is missing from it.
- [ ] Every "terminal only" entry has a reason.
- [ ] An end-to-end test on the synthetic company walks: files, ingest, mappings, statements, report download.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Generate and download reports from the Reports screen | UI, services | UI test |
| 2 | Add Settings and Health screens | UI, `doctor.py` service | UI test |
| 3 | Add the Questions screen and Log filters | UI | UI test |
| 4 | Add FDD finding drafts and the dashboard build | UI, services | UI test |
| 5 | Group the navigation and add the command coverage table with its test | templates, docs, tests | criteria 1 to 3 |
