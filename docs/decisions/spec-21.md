# SPEC-21: decisions for the forensic section of the FDD report

Builder's readings ([README](README.md)); the owner confirms or changes them.

## Which flags a forensic finding may rest on

- **Decision:** `--flags` takes flags of the open engagement in the latest run of their test and period, at any
  amount (below the threshold too) and of any period; a forensic finding needs at least one, and only a finding of area
  `forensic` takes flags. The staleness check of `fin query raise --flags` (E992) is not repeated: a finding is the
  user's decision on evidence and is read before it is made final. A link stays when the test runs again (the old
  flag keeps its row); `update --flags` replaces the links. Changing a forensic finding's area is refused while it has
  flags (drop it and propose another).
- **Why:** the spec says the finding links "the flags it rests on as evidence"; a query needs an amount at or above
  the threshold, a finding does not.
- **How to change it:** `src/fin/fdd/finding_flags.py: problems`.

## The Forensic screen's "to a finding" click

- **Decision:** the click opens the chat with a request for the assistant to run `fin fdd finding propose --area
  forensic --flags ...` (naming `--query N` when the ticked flags are in one open query), as the query hand-off does.
  It does not store a draft itself.
- **Why:** a finding's title and observation are words Claude proposes (CLAUDE.md: "you propose them with evidence");
  fin has no neutral way to write them. The assistant runs the same command, so the same function and checks apply.
  Only flags of a stale run are refused on the screen (E008); `propose` checks them again (E970).
- **How to change it:** `src/fin/ui/forensic_screen.py: finding_prompt`.

## The report section

- **Decision:** "Forensic tests" sits before "Reconciliations and queries" and has a narrative heading of its own, so
  Claude's words there go through the FDD lint (E602, E972). The fixed wording fin writes (test titles, states) is
  checked by a test to hold no judging phrase or "risk", "score" or "rating"; the section's data (ledger names as
  filed, reasons) is not linted at render time, since a ledger named as filed could match a phrase and would then
  stop the report. Stale runs show their last outcome marked stale and their flags "(stale run)". The flags shown are
  those at or above the threshold (a voucher gap counts as at or above), at most 20 per test across its periods.
- **Why:** SPEC-21 scope 2 and 3; the masking rule keeps ledgers as filed and parties as tokens.
- **How to change it:** `src/fin/report/forensic_section.py` (`TOP`, `section`).

## The mock answer key

- **Decision:** mock/EXPECTED.md lists the flags of each test and period with everything loaded (journal, bank,
  register and GSTR-2B), and the planted journals' flags in the order the section lists them, from
  `mock/generator/expected_report.py`. The unit test runs the journal tests on the mock and checks their counts and
  order; the bank and counterparty counts come from the lists their own mock tests already check.
- **Why:** loading the bank and register files as well would double the test's time for counts other tests check.
- **How to change it:** `tests/unit/test_fdd_forensic.py`.
