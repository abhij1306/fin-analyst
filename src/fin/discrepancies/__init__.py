"""One list of differences (SPEC-15, `fin discrepancies`): the reconciliations joined into one
chain, from the headline figure down to the invoice.

`view.py` makes the temporary view `discrepancies` (per connection, as `rebuilt_statements`, in
`fin sql` too); `rows.py` reads it in the display unit for the CLI, the export sheet
(`sheet.py`) and the Differences screen. Nothing here changes a figure: a difference is queried
(`fin query raise --items`) or becomes an adjustment with evidence.
"""
