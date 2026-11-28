# Specs

What fin builds next, as specs a builder can turn into pull requests. Each spec says what the user gets, what is out
of scope, how "done" is checked, and its PR plan. How specs become merged PRs: [process.md](../process.md). How the
code is written: [engineering.md](../engineering.md). Live status: [plans/ACTIVE.md](../plans/ACTIVE.md).

## Where these come from

- The first client run: [test-run/2026-11-27-client.md](../test-run/2026-11-27-client.md) (observation numbers are
  cited as "obs. N").
- Owner, 2026-11-28: the web UI is now the main interface and has priority over the CLI; the CLI stays as it is.
  A user should be able to add a ChatGPT, Gemini or other API key; that spec comes last, is optional, and is not
  built until the owner says so.
- The client, 2026-11-28 (through the owner): three pieces their Claude named as missing: data-room intake,
  statements rebuilt from the books, and one list of all differences.
- The client's answers to the 14 standing questions, 2026-11-27 (passed on by the owner, 2026-11-28):
  [decisions/client-answers.md](../decisions/client-answers.md).
- A review of the UI and of what ties fin to Claude Code (same date).

A spec describes what will be built. [product.md](../product.md) describes what fin does today, and changes in the
PR that changes the behaviour.

## Index

Specs 00 to 12 are numbered in build order. Specs 13 to 15 are what the client asked for on 2026-11-28; their CLI
rows depend on little and start early (see [plans/ACTIVE.md](../plans/ACTIVE.md)). Spec 16 is optional and on hold:
no row of it is built until the owner says so; it stays the last to be built. Spec 17 holds the changes from the
client's answers.

| # | Spec | Answers | Depends on |
|---|---|---|---|
| 00 | [Guardrails](SPEC-00-guardrails.md) | recurring defects in [engineering.md](../engineering.md) | none |
| 01 | [Fixes from the first client run](SPEC-01-client-run-fixes.md) | obs. 5, 13, 17, 18 | none |
| 02 | [Shared services and background jobs](SPEC-02-services-and-jobs.md) | UI as main interface | 00 |
| 03 | [Files and ingest in the UI](SPEC-03-ingest-in-ui.md) | obs. 4, 6 | 02 |
| 04 | [Ingest outcomes on real documents](SPEC-04-ingest-outcomes.md) | obs. 4 | 01 |
| 05 | [Mapping review and dictionary coverage](SPEC-05-mapping.md) | obs. 7 to 10 | 02 |
| 06 | [Statements, display units and layout](SPEC-06-statements-and-units.md) | obs. 11 | 02 |
| 07 | [Questions: schema guide and saved metrics](SPEC-07-questions.md) | obs. 12, 14 | none |
| 08 | [Figures the user states](SPEC-08-user-figures.md) | obs. 19 | none |
| 09 | [Diligence in the UI](SPEC-09-diligence-in-ui.md) | obs. 15, 17, 20 | 02, 06, 08 |
| 10 | [Reference documents](SPEC-10-reference-documents.md) | obs. 16 | 06 |
| 11 | [Remaining screens](SPEC-11-remaining-screens.md) | UI as main interface | 02, 06 |
| 12 | [Install and first run](SPEC-12-install.md) | obs. 1 to 3 | none |
| 13 | [Data-room intake](SPEC-13-dataroom-intake.md) | client request 1 | none (screen: 03) |
| 14 | [Rebuilt statements](SPEC-14-rebuilt-statements.md) | client request 2 | 06 for the layout |
| 15 | [One list of differences](SPEC-15-discrepancies.md) | client request 3 | 10, 14 |
| 16 | [AI providers and API keys](SPEC-16-ai-providers.md) (**on hold**) | owner, 2026-11-28 | 02, 03, 05 |
| 17 | [Changes from the client's answers](SPEC-17-client-answers.md) | client answers 1, 6, 9, 10, 12 | none (PR 5: 15) |

Specs 00, 01, 07, 08 and 12 depend on nothing and can start together.

## Owner decisions

Each spec takes a default so that building is not blocked. These are the defaults that change what the user sees or
what leaves the machine. An answer goes into the spec and, when it is a product decision, into
[decisions/](../decisions/README.md) with the owner's words and date.

| # | Question | Default taken | Spec |
|---|---|---|---|
| Q1 | No file in the client run loaded without a Claude reading. Does "keep extraction simple" (`CLAUDE.md`) still hold? | Yes. No new parsing heuristics; the fallbacks become first-class (ask for the unit, read in the UI, offer an add-on at install). | 04 |
| Q2 | Read `.xls` files? It needs one new dependency (`xlrd`, read-only, pure Python). | Yes. | 04 |
| Q3 | May a user state a figure the documents do not show (a nil line), inside a diligence engagement too? | Yes, as a user figure with its own label, never written by the AI without the user's instruction, listed in every report's data coverage. | 08 |
| Q4 | May low-confidence mapping proposals be confirmed in bulk? | No. Below 0.8 each one is confirmed on its own. | 05 |
| Q5 | Are a third party's databook figures company data? | No. They are stored apart and never become facts. | 10 |
| Q6 | Where do API keys live? | In a key file in the data folder that only the user can read, or the environment; never in `config.toml`, a log or the database. | 16 |
| Q7 | With another provider, documents and figures go to that provider under its own retention terms. The client's zero-retention arrangement covers Claude only. | Off by default; turning a provider on shows what will be sent and where, once, and records the consent in the audit log. | 16 |
| Q8 | The open SonarCloud findings are two test-style rules (88 of 97). Fix them or switch the rules off? | Switch them off in the Sonar profile (a human task: it needs the Sonar account). | 00 |
| Q10 | What is the largest archive a data room may hold? | 2 GB unpacked per archive, set in `config.toml`. | 13 |
| Q12 | USD orders need an exchange rate. Where does it come from? | The user states it per engagement and as-at date; fin never fetches or assumes one. | 17 |
| Q13 | The client's credit terms are 90 days; fin's default was 30. | The default becomes 90, as the client said; an engagement can set its own. | 17 |
| Q11 | In a cash flow rebuilt from balances, some lines cannot be told apart (tax paid, interest paid). Estimate them? | No. They are shown as "not derivable" with the reason. | 14 |

## Not specified yet

Seen in the client run but too thin to design from; raise with the client first.

- **Deal folders.** The client keeps a deal as a folder per target with banker data, an information memorandum,
  databooks and peer lists. fin knows companies, not deals.
- **Peers.** The client's notes name "direct peers"; fin's peer comparison (`fin report --peers`) was not used.
- **The golden evaluation** did not run. [client-handoff.md](../client-handoff.md) needs a second attempt once
  specs 01, 03 and 04 are in.
