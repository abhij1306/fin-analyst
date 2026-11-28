# SPEC-06: statements, display units and layout

Builder's readings ([README](README.md)); the owner confirms or changes them.

### The default display
- **Decision:** a new `config.toml` shows money in millions, one decimal, negatives in brackets, captioned "INR Mn".
- **Why:** the client asked for INR million laid out like a databook and confirmed "INR Mn" (client answers, answer
  14). One decimal and brackets are the databook convention the research chose (`#,##0.0;(#,##0.0);"–"`).
- **How to change it:** `config.py: DEFAULT_CONFIG_TOML`; a data folder changes its own `[display]`.

### A company of its own
- **Decision:** `[display.companies.<name>]` takes any of the `[display]` keys; a unit without a label gets that
  unit's standard label (`config.py: UNIT_LABELS`). A unit chosen on the Statements screen or with `--unit` also
  takes the standard label.
- **Why:** the spec asks for a per-company override; a picker's unit with the configured caption of another unit
  would mislabel every table.
- **How to change it:** `config.py: _display_values, Display.in_unit`.

### Rounding
- **Decision:** `display_value` rounds each figure on its own, half away from zero, with exact integer arithmetic
  (DuckDB divides DECIMALs as DOUBLE). A subtotal whose rounded lines (with their signs) do not add up to its own
  rounded value is marked * with a note.
- **Why:** spec scope 5: a total is the rounded total, not the sum of rounded lines.
- **How to change it:** `display.py: DISPLAY_MACRO`; `statements.py: _ROWS_SQL` (the `rounding` CTE).

### Earnings per share and zero
- **Decision:** earnings per share are shown in rupees per share with two decimals, never scaled by the display unit.
  With brackets, a zero shows as a dash, as the Excel format's third section does.
- **Why:** dividing EPS by a million would show 0.0; the dash keeps the text and the Excel export alike.
- **How to change it:** `display.py: PER_SHARE_DECIMALS, number`.

### % of revenue and YoY
- **Decision:** % of revenue is the P&L line over revenue from operations of the same period and scope; YoY is growth
  on the same line of the same-length period ending a year before. Both are computed in SQL from rupees (not the
  rounded figures), one decimal. The Excel export leaves them out (the screen and `fin statements` offer them).
- **Why:** computed from rupees they do not carry the display rounding; the export is the databook of record.
- **How to change it:** `statements.py: _ROWS_SQL` (the `ratios` CTE); `workbook.py: export_workbook`.

### Marks in the text output
- **Decision:** `fin statements` text keeps stated and corrected marks and adds "read by AI"; "unconfirmed mapping"
  is shown on the screen and in the export comment, not in the text (only confirmed mappings reach the statements,
  so it marks a figure whose label also has a proposal waiting).
- **Why:** keeps the text output as before for the common case; the screen is the main interface.
- **How to change it:** `statements.py: format_statements`.

### Money in reports
- **Decision:** report money is "<number> <unit_label>" (for example "1,340.9 INR Mn"); the FDD report's per-unit
  rates (contract rates) stay in rupees.
- **Why:** one caption everywhere; a rate per unit of work is not a statement figure.
- **How to change it:** `display.py: money`; `report/fdd_tables.py: RUPEES`.
