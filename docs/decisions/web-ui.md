# Web UI (PR 1: foundation)

Builder readings of the owner's 2026-11-26 decisions in [../web-ui-plan.md](../web-ui-plan.md) ("browser is fine", "use shadcn ui to avoid building components yourself", "keep it optional", "yes seitch to HTML", "use a similar library like shadcn for html to avoid writing and maintating code"); the owner confirms or changes them. Format: [README.md](README.md).

## 1. Home shows only what `fin status` already counts

- **Decision**: the Home screen shows four counts (mappings waiting, open findings on filed data, accepted findings, the next gate that is not approved with its state), the periods loaded and gaps, and a "Waiting for you" list: mappings to review, draft queries, proposed adjustments, open findings, inbox files not loaded. Every number is what `fin status` returns; the UI adds none. The mockup's "documents loaded" and "draft FDD findings" cards are left out: `fin status` does not count them, and a second count would be a second source of truth.
- **Why**: numbers come from fin; the mockup data was fake. Those two counts arrive with the Files and Findings screens (PR 2), which read them from their own tables.
- **How to change it**: `src/fin/ui/views.py` (`home`, `waiting`).

## 2. Screens not built yet are shown disabled

- **Decision**: the top bar lists all eight screens of the plan; only Home opens. The others are disabled tabs, not hidden.
- **Why**: the client sees the whole shape from the start, and no tab leads to an empty page.
- **How to change it**: `src/fin/ui/server.py` (`SCREENS`).

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
