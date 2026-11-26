# Web UI plan (optional `fin ui`)

Status: planned 2026-11-26. Built in 4 PRs, in order; each merges before the next starts.

## Goal

The client can work with fin entirely in a browser on their own machine: see what is loaded and waiting, make
every decision that is theirs (mappings, queries, adjustments, gates, final findings, findings on filed data), open
reports, upload documents, and chat with Claude, without typing `fin` commands. The CLI stays exactly as it is.

## Owner decisions (2026-11-26, owner's words)

- Browser UI is fine: "browser is fine".
- Chat in the UI, so the user never needs the CLI: "the objective is enable user to interact through web UI
  completely without the need for using cli".
- No Claude API: "i dont want to use claude api".
- Components: first "use shadcn ui to avoid building components yourself"; then, on hearing shadcn needs React, plain
  HTML instead: "yes seitch to HTML" (no React, no frontend build).
- Optional: "keep it optional install like cli remains like it exist today and this will be optional feature if
  user wants so that if the user doestn like it he can still use just the cli".
- Layout: "remove the sidebar and keep the options on the topbar", chat window "little wider" (mockup v2: top bar
  with company picker and screen tabs; chat panel on the right, about 42% of the width, min 460 px).

## Design (orchestrator's reading where not an owner decision above; owner may override)

```
Browser (HTML + htmx, server-rendered) <-> fin ui (Python server, 127.0.0.1 only)
                                              |- fin Python modules (screens, decisions)
                                              '- claude -p (installed Claude Code, user's own login) -> fin commands
```

- **Optional add-on**, like the PDF add-on: a `ui` extra in pyproject (`uv sync --extra ui`); `./install.sh --ui`
  installs it. Pages are HTML rendered by the server (Jinja2 templates) with htmx for partial updates and
  server-sent events for streaming; no React, no npm packages, no frontend build. Components: **Basecoat** (basecoatui.com, MIT,
  github.com/hunvreus/basecoat), the shadcn/ui design system as plain HTML classes (`btn`, `card`, `table`,
  `badge`, `tabs`, `dialog`, `alert`, `select`, `input`...) with small vanilla JS for the interactive
  ones. Use its prebuilt standalone `dist/basecoat.cdn.min.css` and `dist/js/all.min.js` (or only the component
  scripts used), pinned to one version; no Tailwind build. Owner, 2026-11-26: "use a similar library like shadcn for
  html to avoid writing and maintating code". So: use Basecoat's components and markup as documented; write no
  component CSS or JS of our own; our own stylesheet is only page layout (top bar, two-column split, chat panel)
  that Basecoat's bundled utilities don't cover, kept small. Icons: inline Lucide SVGs (ISC) where needed. Check that
  Basecoat's scripts initialise components inside htmx-swapped content. Third-party files (htmx, Basecoat) are vendored
  under `src/fin/ui/static/vendor/` with their licence and version noted; the file size check skips `vendor/`
  (third-party, not authored). Without it nothing changes: no new core dependencies, `fin` works as today, and `fin ui` says how
  to install it (error with a code). `fin doctor` reports the UI as installed or not, never as a failure.
- **Server**: a small Python web app (FastAPI + Jinja2 + uvicorn in the extra) started by `fin ui`; it serves the
  pages, partials and static files, opens the browser, stops on Ctrl-C. It calls the same Python functions the CLI commands call; no
  shelling out to `fin` for screens or decisions. Every API call that changes data creates a run record and logs
  through `src/fin/audit/logger.py` like the CLI, uses `src/fin/db/conn.py`, and returns errors with their code
  and run_id.
- **Local only and protected**: bind 127.0.0.1 only; reject requests whose Host is not localhost/127.0.0.1 (DNS
  rebinding); a random token made at start is put in the URL the server opens and set as an HttpOnly SameSite=Strict
  cookie, and every API call needs it; no CORS. A website in the same browser must not be able to drive the server.
- **Same rules as the CLI**: the UI shows unconfirmed mappings, open and accepted findings and missing data; numbers
  come from fin; decisions that only the user may take (approve a gate, mark a finding final or return it to draft,
  approve/close/withdraw a query, mark it sent, accept or reject an adjustment, confirm or reject a mapping, accept a
  finding on filed data) are buttons a person presses. The UI never drafts or proposes; Claude does that in chat.
- **Chat**: the server runs the installed Claude Code headless: `claude -p` with stream-json input and output,
  `--resume <session>` per conversation, in the repo folder so CLAUDE.md, skills and hooks apply. It uses the
  user's own Claude Code login (no ANTHROPIC_API_KEY; warn if one is set, since it would bill the API). Not the
  Agent SDK (its docs require an API key). Single user on their own machine: the credential policy allows a user to
  sign in to the unmodified Claude Code binary with their own subscription; it does not allow routing other
  people's requests through one subscription, so the UI is not for hosting or sharing.
- **Claude cannot take the user's decisions from the UI chat**: the chat runs Claude with those commands denied
  (e.g. `--disallowedTools` patterns for `fin gate approve`, `fin fdd finding final|draft`, query
  approve/sent/close/withdraw, adjust accept/reject, review confirm, findings accept); Claude points the user to the
  button instead. Other permission requests appear in the chat with Allow once / Deny.

## Screens (top bar tabs; mockup v2)

Home (counts and "waiting for you"), Files (upload, documents, findings on filed data), Mappings, Queries
(queries and adjustments), Gates (checklist, approve), Findings (FDD), Reports (health, FDD, Excel export,
dashboard link), Log (runs). Company picker on the top bar. Chat panel on the right on every screen; "Ask Claude"
buttons prefill the chat with the item.

## PRs

1. **ui-foundation**: this plan as `docs/web-ui-plan.md` plus `docs/decisions/web-ui.md`; the `ui` extra; the
   page layout (base template, vendored htmx and CSS), top bar, company picker, empty chat panel and the Home screen
   (status data); `fin ui`; `install.sh --ui`; local-only protections; `fin doctor` line; CI job that runs the
   server tests (pytest with the extra). Add `.html` templates to tools/check_file_size.mjs and skip `vendor/`.
2. **ui-screens**: read-only screens: Files, Mappings, Queries, Gates, Findings, Reports (open HTML, download
   Word/PDF/Excel), Log; pages and partials over existing modules; tests per page.
3. **ui-actions**: the decision buttons and uploads (to the company's inbox folder), each through the same functions
   as the CLI commands with run records and audit logs; confirmations for gate approve and mark final; errors shown
   with code and run_id; tests per action, including that each refuses what the CLI refuses (E801 etc).
4. **ui-chat**: the chat panel: claude -p bridge (stream-json over server-sent events), streaming text and tool
   calls, conversation list and resume, stop button, skill chips, "Ask Claude" prefill, permission prompts in the
   chat, decision commands denied, clear errors when Claude Code is missing or not logged in, API-key warning; docs
   (product.md, client-handoff.md, README). Tests with a fake `claude` executable; no live Claude in tests.

Each PR updates docs/product.md for the behaviour it adds and docs/errors.md for new codes.

## Out of scope

Hosting, multiple users, remote access, auth beyond the local token, mobile layout, replacing the Evidence
dashboards, any change to CLI behaviour, Windows.
