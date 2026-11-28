# SPEC-11: the remaining screens

Builder's readings ([README](README.md)); the owner confirms or changes them.

### 1. The navigation groups
- **Decision:** Home stays a link of its own; the other twelve screens sit in four Basecoat dropdown menus: Data
  (Files, Mappings, Statements), Analysis (Questions, Reports, Dashboards), Diligence (Engagement, Queries, Gates, Bridge, Findings)
  and System (Settings, Health, Log). The open screen is `aria-current="page"` and its group's button is highlighted; a
  group's button carries the sum of its screens' waiting counts.
- **Why:** the spec asks for grouped navigation; the research chose Basecoat's dropdown menu. The order follows the work: data in, analysis, diligence, then the system.
- **How to change it:** `src/fin/ui/nav.py` (`NAV`). References joins Diligence when its screen arrives (SPEC-10).

### 2. The dashboard is served by `fin ui`
- **Decision:** the Evidence site is built under the base path `/dashboard-site`, and `fin ui` serves the last good
  build (`<data>/dashboard/build`) at that path; the Dashboards screen shows it in a frame. The dev server moves to
  `http://localhost:3000/dashboard-site`.
- **Why:** the research found Evidence needs its basePath set to be served below another site; one path for the
  build and the dev server keeps one configuration. Serving it from `fin ui` keeps the page behind the UI's token.
- **How to change it:** `dashboards/evidence.config.yaml` (`deployment.basePath`) and `fin.dashboard.BASE_PATH`
  together.

### 3. Settings show the display, not edit it
- **Decision:** Settings shows the display settings the company's numbers use (unit, decimals, negatives, caption,
  and whether the company has its own), the PDF add-on with an Install button per add-on, the data folder and the
  assistant. Changing `[display]` stays a config.toml edit; the Statements screen already picks a unit per view.
- **Why:** SPEC-06 (#127) made the display a config.toml table with per-company overrides and added no writer; a
  form that rewrites config.toml would be a second writer of the file beside `fin parser use`. The assistant row
  only says Claude Code: SPEC-16 is on hold.
- **How to change it:** `src/fin/ui/system_screens.py` (`settings`), `templates/_settings.html`.

### 4. Reports: sections, findings and coverage
- **Decision:** the Reports screen lists each narrative under `reports/<company>/` with its section headings; the
  findings and the data coverage are in the rendered report (HTML opens in the browser), not repeated on the screen.
  Each report kind (health, diligence, FDD) is one button that runs `fin report` as a job: the first run writes the
  stub, the next renders.
- **Why:** the report already renders findings and coverage from fin's tables; a second rendering on the screen
  would be a second source of truth.
- **How to change it:** `src/fin/ui/system_screens.py` (`narratives`), `templates/_reports.html`.

### 5. Questions are the saved `fin sql` runs
- **Decision:** the Questions screen lists the audit `queries` rows of the company that are reads with a result
  (newest 100). **Run again** runs the same SQL with a new evidence page, recorded as a new query with `rerun_of`.
  A new question is asked in chat (`/ask`): the UI does not write SQL.
- **Why:** `fin sql --question` already saves the question, SQL and evidence page; Claude writes the SQL.
- **How to change it:** `src/fin/services/questions.py`.

### 6. Log filters and the trail
- **Decision:** the company filter is the top bar's company picker; the Log adds filters by command and by actor
  (the run's recorded `actor`, "not recorded" for runs before SPEC-02) and a trail form: a fact id, or a metric and
  period, shows `fin audit`'s trail. A figure on the Statements screen opens its source in the drawer #127 added.
- **Why:** one company picker for every screen; the trail reuses `fin.audit.trace`.
- **How to change it:** `src/fin/ui/system_screens.py` (`log_screen`, `trail`).

### 7. Terminal-only commands
- **Decision:** `fin delete` (asks for the company's name typed in the terminal), `fin init` (install.sh runs it),
  `fin ui` and `fin ui setup` (the UI itself), `fin eval init|run|bundle` (the client's golden evaluation runbook)
  and `fin provisions confirm|reject`, `fin netdebt item accept|reject` (the user's decisions: denied to Claude in
  chat, and no screen has them yet) are terminal only. Every other command is on a screen or done through chat.
- **Why:** criterion 1 of the spec; a decision Claude may not run needs a screen before it can leave the terminal.
- **How to change it:** the table "Where each command is" in `docs/product.md`; `tests/unit/test_command_coverage.py`
  checks it.

### 8. FDD drafts in the UI
- **Decision:** a draft finding has **Edit** (every text and choice field, through `fin fdd finding update`'s
  service, so a typed figure is refused with E970 as in the CLI) and **Drop** (confirmation dialog). Marking final
  stays its own click with a confirmation, as before.
- **Why:** the spec: drafts are edited and dropped in the UI; only the user marks one final.
- **How to change it:** `src/fin/services/fdd.py`, `templates/_finding_edit.html`.
