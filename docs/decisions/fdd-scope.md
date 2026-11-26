# FDD scope: orchestrator readings

Owner source: 2026-11-25, "whatever u can build you should build including fdd style findings report", in reply to
the gap list in `docs/diligence.md` §10. The choices below are the orchestrator's readings of that instruction, not
owner decisions; the owner or the client may change them.

## What is built and what is not

- **Decision**: every gap gets the part fin can compute from loaded data or record from Claude's reading of a
  document ([fdd.md](../fdd.md)). Not built: actuarial valuation (F3 gives an indicative, undiscounted gratuity figure
  only), leave encashment (no leave balance data), a legal opinion on contracts or litigation (fin records terms and
  status; the assessment is the user's), MIS/KPI reconciliation (an inquiry topic, F2, not a computation).
- **Why**: the gap list says some items are judgement and management work. fin keeps to the repeatable parts and
  gives Claude and the user a place to record the rest with evidence.
- **How to change it**: add a workstream to `docs/fdd.md`.

## The FDD report may judge

- **Decision**: the FDD report (F8) allows observations, severity (high, medium, low), implications and deal
  considerations (price, SPA protection, condition precedent, further work). It still has no valuation, no rating of
  the company as an investment and no buy/don't-buy language; numbers are references. The health and diligence
  reports keep "describe, don't judge".
- **Why**: an FDD report without findings and implications is not an FDD report (gap 10), and the owner asked for
  one. Severity and final status are the user's: Claude proposes, the user marks a finding final.
- **How to change it**: `CLAUDE.md` (Report narrative) and the FDD report lint (`fin/report/`, F8).

## Indicative figures use stated assumptions

- **Decision**: where data is missing (basic pay for gratuity), fin computes on a `config.toml [fdd]` assumption and
  labels the figure "indicative" with the assumption. Default `basic_share = 0.5`.
- **Why**: CLAUDE.md forbids filling gaps with silent assumptions; a stated, configurable, labelled assumption is not
  silent, and the wage-code test needs one anyway (wages at least 50% of CTC). [Client question 3](../client-questions.md).
- **How to change it**: `config.toml [fdd] basic_share`, or drop the figure in F3.
