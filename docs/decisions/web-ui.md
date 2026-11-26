# Web UI (PR 1: foundation, PR 2: read-only screens)

Builder readings of the owner's 2026-11-26 decisions in [../web-ui-plan.md](../web-ui-plan.md) ("browser is fine", "use shadcn ui to avoid building components yourself", "keep it optional", "yes seitch to HTML", "use a similar library like shadcn for html to avoid writing and maintating code"); the owner confirms or changes them. Format: [README.md](README.md).

## 1. Home shows only what `fin status` already counts

- **Decision**: the Home screen shows four counts (mappings waiting, open findings on filed data, accepted findings, the next gate that is not approved with its state), the periods loaded and gaps, and a "Waiting for you" list: mappings to review, draft queries, proposed adjustments, open findings, inbox files not loaded. Every number is what `fin status` returns; the UI adds none. The mockup's "documents loaded" and "draft FDD findings" cards are left out: `fin status` does not count them, and a second count would be a second source of truth.
- **Why**: numbers come from fin; the mockup data was fake. Those two counts arrive with the Files and Findings screens (PR 2), which read them from their own tables.
- **How to change it**: `src/fin/ui/views.py` (`home`, `waiting`).

## 2. All eight screens are tabs (PR 2: read-only screens)

- **Decision**: PR 1 showed the screens not built yet as disabled tabs; PR 2 builds them all, so every tab opens. The top bar is a Basecoat tab list (`role="tablist"`, `aria-selected`, `aria-controls="screen"`, the screen is the `tabpanel`); the tabs are links, so a screen has its own address. The chosen company is in every tab link (Home too) and the company picker reloads the current screen with the new company.
- **Why**: the review notes of #106 (tab semantics; keep the company between screens).
- **How to change it**: `src/fin/ui/server.py` (`SCREENS`), `templates/base.html`.

## 3. The UI is read-only in this release, and the chat is a placeholder

- **Decision**: no route changes data; the chat panel says it is not connected and points to the terminal.
- **Why**: PR 3 adds decisions with their run records and confirmations; PR 4 adds the chat.
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

- **Decision**: the Reports screen lists the .html, .docx, .pdf and .xlsx files under `reports/<company>/` (health, diligence and FDD reports, the Excel export). HTML opens in a new tab; the rest download. The file route serves only a name that is in that list, so a path can never leave the folder (E001 otherwise). Making a report (the narrative, `fin report`) stays with Claude and the CLI until chat (PR 4). The plan's "dashboard link" is left out: the Evidence dashboards are served by their own command (`fin dashboard`), and the plan puts replacing them out of scope.
- **Why**: read-only screens; a report is Claude's work and its numbers come from fin.
- **How to change it**: `src/fin/ui/screens.py` (`report_files`).
