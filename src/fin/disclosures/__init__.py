"""FDD F5 (docs/fdd.md): contingencies, commitments, notices and related parties.

`reading.py` reads Claude's reading CSV of the notes to accounts and legal and tax notices;
`load.py` stores it (`disclosures`, register parties as tokens) and lists the schedules;
`related.py` is the related party links Claude proposes and the user confirms
(`fin review ... --related`); `rpt.py` is the rpt_vs_books reconciliation; `data.py` is what the
FDD report (F8) reads. Tables and views: `db/schema/fdd_disclosures.sql`.
"""
