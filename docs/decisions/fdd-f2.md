# FDD F2: management inquiries

Owner source: the plan in `docs/fdd.md` F2 (2026-11-25). The entries are the builder's readings.

## An inquiry is a query with a topic, not a new object

- **Decision**: an inquiry is a normal query (same life cycle, export, queries gate G2 check) with a topic and no
  amount, period optional, and the fixed source note "management inquiry (no source document)". It is raised with
  `fin query raise --topic`, which refuses `--amount`, `--source`, `--finding` and `--items`.
- **Why**: the plan says inquiries follow the query life cycle and appear in the export and G4. An inquiry has no
  difference, so nothing in it can be "at or above the threshold": it is never raised automatically, only drafted by
  Claude and approved by the user.
- **How to change it**: `fin/diligence/inquiries.py: raise_inquiry`.

## The standard checklist is a prompt for Claude, never raised by fin

- **Decision**: `templates/fdd/inquiries.toml` holds a few standard questions per topic. `fin query checklist` shows
  them only for topics with no active inquiry (a withdrawn one does not count as covering the topic). fin raises
  nothing from it; Claude adapts the wording to the company and drafts, the user approves.
- **Why**: the wording needs the company's facts (policies, parties) and every query is the user's to approve. The
  question lists are general FDD practice, not tailored to the client. [Client question 12](../client-questions.md).
- **How to change it**: edit `templates/fdd/inquiries.toml`, or put the user's own list in the data folder as
  `templates/inquiries.toml` (SPEC-17 item 3, [spec-17.md](spec-17.md) §3: used instead when present); topics:
  `config.toml [fdd] inquiry_topics`.

## Topics and the gate

- **Decision**: ten topics (the plan's list), configurable. A topic with no inquiry does not stop a gate: the
  checklist lists it, but a draft inquiry is a draft query and so blocks G2 like any draft.
- **Why**: whether a topic is relevant is judgement; a gate that required one inquiry per topic would force queries
  that are not needed.
- **How to change it**: `fin/diligence/gates.py: _queries_checklist`.

## No golden eval kind

- **Decision**: no golden kind for inquiries.
- **Why**: the plan asks for a golden kind for each new figure a person can check; an inquiry has no figure.
- **How to change it**: add a count kind in `fin/evals/` if the client wants one.
