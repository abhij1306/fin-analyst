# Web UI (PR 1: foundation, PR 2: read-only screens, PR 3: decisions, PR 4: chat)

Builder readings of the owner's 2026-11-26 decisions in [../web-ui-plan.md](../web-ui-plan.md) ("browser is fine", "use shadcn ui to avoid building components yourself", "keep it optional", "yes seitch to HTML", "use a similar library like shadcn for html to avoid writing and maintating code"); the owner confirms or changes them. Format: [README.md](README.md).

## 1. Home shows only what `fin status` already counts

- **Decision**: the Home screen shows four counts (mappings waiting, open findings on filed data, accepted findings, the next gate that is not approved with its state), the periods loaded and gaps, and a "Waiting for you" list: mappings to review, draft queries, proposed adjustments, open findings, inbox files not loaded. Every number is what `fin status` returns; the UI adds none. The mockup's "documents loaded" and "draft FDD findings" cards are left out: `fin status` does not count them, and a second count would be a second source of truth.
- **Why**: numbers come from fin; the mockup data was fake. Those two counts arrive with the Files and Findings screens (PR 2), which read them from their own tables.
- **How to change it**: `src/fin/ui/views.py` (`home`, `waiting`).

## 2. All eight screens are tabs (PR 2: read-only screens)

- **Decision**: PR 1 showed the screens not built yet as disabled tabs; PR 2 builds them all, so every tab opens. The top bar is a Basecoat tab list (`role="tablist"`, `aria-selected`, `aria-controls="screen"`, the screen is the `tabpanel`); the tabs are links, so a screen has its own address. The chosen company is in every tab link (Home too) and the company picker reloads the current screen with the new company.
- **Why**: the review notes of #106 (tab semantics; keep the company between screens).
- **How to change it**: `src/fin/ui/server.py` (`SCREENS`), `templates/base.html`.

## 3. The chat (placeholder in PR 1-3; built in PR 4, see 9)

- **Decision**: until PR 4 the chat panel said it was not connected.
- **Why**: PR 3 added decisions with their run records and confirmations; PR 4 added the chat.
- **How to change it**: `src/fin/ui/templates/_chat.html`.

## 4. `fin ui setup`, and the UI add-on survives other syncs

- **Decision**: `fin ui setup` runs `uv sync --frozen --extra ui` plus the chosen PDF add-on (E005 on failure). `fin parser use`, `fin update` and `./install.sh` keep the `ui` extra when it is installed; without that, a sync for the PDF add-on would silently uninstall the UI. `fin doctor` reports the UI as installed or not, never as a failure.
- **Why**: "keep it optional ... he can still use just the cli" means choosing the UI must not break, and not choosing it must not cost anything. The plan named `fin ui setup`; with no build step it is only the sync.
- **How to change it**: `src/fin/ui/setup.py`, `src/fin/ui/extra.py`.

## 5. Local-only: wrong or missing token, or Host, is an error E007

- **Decision**: a request whose Host is not `127.0.0.1:<port>` or `localhost:<port>`, or that has no valid token cookie, is refused with E007 (400 or 401), including static files. The token is in the link `fin ui` prints and opens; it is not written to the run log.
- **Why**: a web page in the same browser must not be able to read or drive the server (plan: Local only and protected).
- **How to change it**: `src/fin/ui/security.py`.

## 6. What each read-only screen shows (PR 2)

- **Decision**: each screen shows what the matching CLI command lists, and no more: Files = documents, inbox files not loaded and findings on filed data (`fin findings list`); Mappings = open items of `pending.json`; Queries = queries and adjustments of the open engagement; Gates = `fin gate status`; Findings = the engagement's FDD findings except dropped ones; Log = the company's last 100 runs with their problems (`fin audit <company>`). Where a company has no open engagement, Queries, Gates and Findings say so (E803) as text, not as an error alert. The mockup's "documents loaded" and "draft FDD findings" cards stay out of Home (see 1).
- **Why**: numbers come from fin; a second way to count is a second source of truth. An empty engagement is a normal state, not a failure.
- **How to change it**: `src/fin/ui/screens.py`, the `_<screen>.html` templates.

## 7. Reports lists files that exist; it does not build them

- **Decision**: the Reports screen lists the .html, .docx, .pdf and .xlsx files under `reports/<company>/` (health, diligence and FDD reports, the Excel export). HTML opens in a new tab as a sandboxed page (`Content-Security-Policy: sandbox` without `allow-same-origin`, so it cannot use the token cookie); the rest download. The file route serves only a name that is in that list, so a path can never leave the folder (E001 otherwise). Making a report (the narrative, `fin report`) stays with Claude and the CLI until chat (PR 4). The plan's "dashboard link" is left out: the Evidence dashboards are served by their own command (`fin dashboard`), and the plan puts replacing them out of scope.
- **Why**: read-only screens; a report is Claude's work and its numbers come from fin.
- **How to change it**: `src/fin/ui/screens.py` (`report_files`).

## 8. Decisions and uploads (PR 3)

- **Decision** (builder's reading): the buttons are Mappings Confirm/Reject, Files Save as is, Queries Approve/Mark sent/Close/Withdraw, Adjustments Accept/Reject, Gates Approve, Findings Mark final/Return to draft. Dialogs: Gate approve and Mark final (as the plan says), plus Mark sent and Close (a query that was not sent must not be marked sent) and any action that needs a reason (Withdraw, Reject adjustment), because the CLI needs `--reason`. Mapping Confirm uses the proposed account and company scope (the CLI's defaults); a different account, name or parent stays in the CLI or chat. Answering a query and the other review kinds (ledgers, customers, tax, related, net debt, matches) are not in the plan's list and stay with the CLI and Claude.
- **Decision**: every action runs in a run named like the CLI command (`gate approve`, `review confirm`, ...) and calls the same function, so refusals are the CLI's (E801, E802, E805, E971, E406, E304). A refusal shows "Not done: <code>", the message and the run_id; its run ends "blocked" or "error" as in the CLI. A bad request (unknown action, missing reason, bad id) is a new code E008 and starts no run, as the CLI's usage errors do.
- **Decision**: CSRF is an Origin check (every non-GET must carry `Origin: http://127.0.0.1:<port>` or `http://localhost:<port>`) on top of the SameSite=Strict token cookie, rather than a per-page token: no state to keep, and a browser always sends Origin on cross-site POSTs. Refusal is E007 (403).
- **Decision**: uploads take .xlsx, .xlsm and .pdf (what `fin ingest` reads), 50 MB per file, 100 MB per request, plain names only, never overwrite (the user renames: a changed file is a new version by name, and silently replacing a file in the inbox would hide that). New dependency `python-multipart` in the `ui` extra: FastAPI needs it to read form posts.
- **Why**: only the person decides; the UI adds no second way to change data, and cannot do what the CLI refuses.
- **How to change it**: `src/fin/ui/actions.py` (the actions table), `uploads.py`, `csrf.py`, the `_actions.html` macro.

## 9. The chat (PR 4)

- **Decision** (builder's reading): permission requests are shown with Allow once / Deny by the simplest mechanism that needs no extra server: each turn is a `claude -p` process in the default permission mode, so a tool that is not already allowed is denied and listed in the `result` message's `permission_denials`. The chat shows each one; **Allow once** starts the next turn (`--resume`) with `--allowedTools` for that one exact command or file (Bash, Edit, Write, Read only; a value with a wildcard, bracket or backslash cannot be named exactly, so it is not offered); **Deny** just records it, since Claude was already told no. Not used: `--permission-prompt-tool` (needs an MCP server the UI would have to run and keep a request open while the person decides), bypass modes (never), `--bare` (it skips hooks and CLAUDE.md; the chat must not).
- **Decision**: the user's decisions are denied with `--disallowedTools` rules per command, as `fin ...` and `uv run fin ...` (also `python -m fin`), and the system prompt tells Claude they are buttons. A denial of one of them is explained in the chat, never offered as Allow once. This is a guard on top of the project's own hooks and the CLI's refusals (E801, `fin sql --write` on diligence tables), not a replacement.
- **Decision**: a turn is one process, prompt on stdin, `--session-id` first and `--resume` after (only once Claude made a session, so a first turn that failed to log in is retried as a first turn). Not one long-lived process with stream-json input: Stop and restart are simpler and a conversation survives `fin ui` restarting. Chat requests are not run records (no data changes in the UI; Claude's own `fin` commands make theirs); errors carry their code only. Bad chat requests (empty message, unknown conversation, a request answered twice) reuse E008.
- **Decision**: new codes E009 (Claude Code missing, cannot start, or stopped with an error), E010 (not logged in; detected from Claude's own error output, not by a pre-check), E011 (a turn is running). An `ANTHROPIC_API_KEY` in the environment is warned about in the panel, not removed.
- **Why**: owner decisions "i dont want to use claude api" and the plan's chat section; one person on one machine.
- **How to change it**: `src/fin/ui/chat_args.py` (denied commands, argv), `chat_run.py` (process and stream), `chat_routes.py`.
