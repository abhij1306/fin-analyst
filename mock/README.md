# Mock document set

A clean, fictitious company (Corvane Analytics Private Limited) with three financial years (FY23 to
FY25) and a six-month stub (FY26-YTD-SEP), sent by four sources (the company, its auditor, its bank
and an adviser), with a few documents in two versions. Parsing is not what it tests: every file is
well formed. It is the input of the end-to-end runbook for storing documents (versions, sources,
duplicates, tracing) and for diligence on them (reconciliations, differences, queries, adjustments,
the bridge, gates and findings).

- `dataroom/`: the documents, in one folder per source.
- `zipped/corvane_dataroom.zip`: the same tree as one archive (`fin dataroom scan corvane mock/zipped`).
- `extra/`: the adviser's walk sheet (`fin adjust import`), a management reply (evidence) and, in `extra/bank/`,
  two statements of a second bank account with planted cases for the bank forensic tests (SPEC-19; not in the books
  or the data room: load them only for those tests).
- `EXPECTED.md`: the answer key: what fin should store per file, which version is current, the
  headline figures, and each deliberate diligence item with its amount and where fin shows it.
- `generator/`: the Python that writes all of the above from one data definition
  (`generator/data.py`). The figures tie across documents by construction.

Regenerate after changing `generator/data.py` (xlwt writes the one .xls file; it is not a
dependency of fin):

    uv run --with xlwt python -m mock.generator

The output is byte-for-byte the same on every run. `tests/unit/test_mock_documents.py` checks
that the committed files hold what the generator makes.
