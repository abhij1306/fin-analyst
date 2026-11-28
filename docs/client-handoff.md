# Client handoff: run fin on the client's documents and report back

For the client's Claude Code on their Mac. Goal: install fin, load the client's documents, score fin against figures
the client checked (the golden eval), ask the client the [client questions](client-questions.md), and send the
answers and the eval bundle to fin's owner. Read `CLAUDE.md` first: its rules apply (all data through `uv run fin`,
the client decides mappings, findings, queries and gates; on a failed command report the error code and run_id).

## 1. Install

Ask the client which folder fin should live in (default `~/fin-analyst`) and clone it there, never into your own
workspace or a temporary folder, which the app can delete (`install.sh` warns if it is in one):

```sh
git clone https://github.com/abhij1306/fin-analyst.git ~/fin-analyst   # or the folder the client named
cd ~/fin-analyst
./install.sh             # Homebrew tools, Quarto (no sudo), uv's own Python, uv sync with the web UI, ~/FinAnalysis, fin init, fin doctor
```

It needs no password, except Homebrew's own installer on a Mac that has no Homebrew yet. If it stops, it prints the failing command and the reason; a network failure is tried again
first. Its last line is the command that opens the web UI.

`uv run fin doctor` must show every line `ok` or `warn`. PDF statements that pdfplumber cannot read need an add-on:
`./install.sh --parser docling` (Apple silicon) or `--parser liteparse` (Intel); see the README, "Install".

## 2. Load the client's documents

Get the documents from the client's Google Drive and put each company's files in `~/FinAnalysis/inbox/<company>/`
(lower-case company name). Then `/ingest <company>` for statements and `/diligence <company>` for diligence sources
(Tally trial balance and Ledger Vouchers, registers, bank statements, payroll, GST summaries, orderbook); both commands
say what to load in which order. E807 means no column profile reads a layout: write one (docs/development.md, "Add a
column profile") and note it for the owner.

## 3. Build the golden set and run the eval

The `/eval` command walks through it:

1. `uv run fin eval init <company>` writes `~/FinAnalysis/evals/<company>/golden.csv`: one line per figure fin has,
   with its source; `expected` and `checked` empty.
2. Fill `expected` from the documents at `source`, as printed with the unit, never from fin's output. What to check
   first and what each kind's value is: [eval-kinds.md](eval-kinds.md).
3. Show the client the drafted lines. Write `yes` in `checked` only on lines the client says they checked against the
   document: Claude does not verify its own reading.
4. `uv run fin eval run <company>` prints the score and the report path. Show the client every line that does not
   pass, with both values and the source. Each is one of: fin misread or mis-mapped it (fix through `/review`,
   `fin map set` or `fin ingest --force` when the client says so; keep the report from before), a wrong golden value
   (correct it and run again), or a document that does not add up (a finding, not an eval failure). A `fail` on a
   figure fin loaded with no warning is the most important result.

## 4. Ask the client questions

Ask the client each question in [client-questions.md](client-questions.md) their documents touch, with an example from
their data where the eval shows one. Write the answer under the question in a copy at
`~/FinAnalysis/evals/client-questions.md`, in the client's words, with the date.

## 5. Send the results to the owner

```sh
uv run fin eval bundle                  # every company; or: uv run fin eval bundle <company>
uv run fin eval bundle --with-figures   # only if the client agrees to share figures
```

The bundle (`evals/bundle-<run_id>.md` and `.json`) holds no company, party or file names: fin version, doctor
summary, and per company the score, the lines not passing and counts. Add `~/FinAnalysis/evals/issues.md`: per
problem, what failed (kind, period, account or ledger token, error codes), the kind of source (Excel, PDF page, bank
PDF, Tally export; no file names), what the client decided, and whether it was fixed locally; include layouts that
needed a column profile and any step of this runbook that did not work. Send the answered questions, the bundle and
`issues.md` to fin's owner directly, the way the client chooses.
