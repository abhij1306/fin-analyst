"""The next step for each ingest failure (docs/specs/SPEC-03): the plain words the Files screen
shows under a failed file, with the buttons it offers, and the chat messages those buttons send.

`offers` names the buttons: `reading` (the assistant reads the file and loads its reading with
`fin ingest --reading`), `addon` (choose a PDF add-on, `/parser`), `unit` (the user says which
unit the file is in; fin loads it with that answer, as `fin ingest --unit`). Every code a
document failure can have is in the table (a test checks it)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NextStep:
    text: str
    offers: tuple[str, ...] = ()


READ = "Have the assistant read it"
NEXT_STEPS: dict[str, NextStep] = {
    "E101": NextStep("The file cannot be opened. Upload a good copy and ingest again."),
    "E102": NextStep("The file has a password. Upload a copy without one and ingest again."),
    "E103": NextStep(
        "fin reads .xlsx, .xlsm, .xls and PDF files. Save it as .xlsx or as a PDF with text, "
        "upload that copy and ingest again."
    ),
    "E104": NextStep(
        f"The PDF is a scan with no text. {READ}, or choose a PDF add-on that reads scans.",
        ("reading", "addon"),
    ),
    "E105": NextStep("Already loaded. Re-ingest loads it again as a new version."),
    "E201": NextStep(
        f"No statement tables were found. {READ}, or choose a PDF add-on.", ("reading", "addon")
    ),
    "E202": NextStep(
        f"The period headers were not found. {READ}, or fix the headers and upload it again.",
        ("reading",),
    ),
    "E203": NextStep("Which unit is this file in?", ("unit",)),
    "E204": NextStep(
        f"fin could not parse the file. {READ}, or upload a fixed copy.", ("reading",)
    ),
    "E205": NextStep(f"The assistant's reading cannot be used. {READ} again.", ("reading",)),
    "E206": NextStep(
        f"The PDF add-on failed on some pages (the run log says why). {READ}.", ("reading",)
    ),
    "E207": NextStep(
        "The file names its own unit, so no answer was needed. Ingest it again without one."
    ),
    "E301": NextStep("Some labels have no mapping and loaded unconfirmed: see Mappings."),
    "E303": NextStep("A label maps to two accounts: decide on the Mappings screen."),
    "E890": NextStep(
        "A figure is too large to store, usually a wrong unit or a misread cell. Ask the company "
        "for a corrected file."
    ),
    "E891": NextStep("The database refused the rows. Report the code and the run_id."),
}
OTHER = NextStep("Report the code and the run_id.")


def next_step(code: str | None) -> NextStep:
    """The next step for a failure code; a code no ingest failure has gets `OTHER`."""
    return NEXT_STEPS.get(code or "", OTHER)


def reading_prompt(company: str, original: str, why: str, *, force: bool) -> str:
    """The chat message of "Have the assistant read it". `original` is the file's path in the data
    folder; `why` the failure or the pages fin did not read."""
    again = " --force" if force else ""
    return (
        f"Please do a Claude reading of {original} (in the data folder) for {company}. fin "
        f"could not read it: {why}. Follow the 'Claude reading' steps of .claude/commands/"
        "ingest.md: write the reading CSV "
        f"in review/{company}/ and load it with `uv run fin ingest {company} {original} --reading "
        f"<csv>{again}`. Do not verify your own reading; whether its figures are trusted is my "
        "decision."
    )
