# Decisions

One file per workstream (`d2.md`, `d3-bank.md`, ...), written by the builder. **Only product-level decisions go
here**: choices that can change the analysis or what the user sees. That means how a figure is computed, rolled up,
matched, thresholded or labelled; what counts as a finding, a query or a load failure; what a gate requires; what
gets masked; how ambiguous plan wording was read. File splits, module layout, names, libraries, tests and other
code choices do not belong here: they are in the code and `docs/development.md`.

A decision the client must confirm is marked NEEDS CLIENT CONFIRMATION (first: [masking.md](masking.md); the audit's client list: [final-fixes.md](final-fixes.md)); the client's agent asks about them ([client-handoff.md](../client-handoff.md), step 5).

Each entry has:

- **Decision**: what was chosen.
- **Why**: the reason, including how the plan was read where it was ambiguous.
- **How to change it**: the concrete edit, as `file: function`.

The owner reviews these. An entry is a builder's reading until the owner confirms or changes it (with a dated owner
source); it is not an owner decision.
