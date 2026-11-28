"""Reference documents (SPEC-10, `fin reference`): a third party's figures beside the company's.

- `store.py`: loads a databook with fin's own reader (or the assistant's reading) into
  `reference_files` and `reference_values`, never into facts; each new line is mapped by the
  dictionary or waits in `reference_lines`.
- `lines.py`: the assistant proposes an account or a metric for a waiting line; the user confirms
  or rejects it.
- `compare.py`: each mapped figure beside fin's (the view `reference_comparison`), the
  differences at or above the threshold first.
- `handoff.py`: from a difference, a draft query or a proposed adjustment with both sources.
"""
