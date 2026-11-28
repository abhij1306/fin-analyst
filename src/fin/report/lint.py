"""The report narrative: lint (E602), references and sections.

Claude writes the narrative (`reports/<company>/<scope>/narrative.md`); it never types a number.
Every number is a reference that the renderer replaces with a value from the company database:

    {{metric:<metric>:<period>}}   e.g. {{metric:ebitda_margin:FY25}}  -> 18.0%
    {{fact:<account>:<period>}}    e.g. {{fact:revenue:FY25}}          -> ₹123.45 Cr

A period is a fiscal year (`FY25`) or a part of one: `FY26-Q1`..`FY26-Q4`, `FY26-H1`, `FY26-H2`,
`FY26-9M`.

The lint rule is strict: after removing HTML comments, references and period labels written as
above (`FY25`, `FY26-Q1`), any digit left on a line is a hand-typed number (E602). That includes
ordered-list markers ("1."), percentages, years such as 2025 and digits inside words.
"""

import re

from fin.audit.errors import NarrativeHasTypedNumbers, NarrativeJudges

PERIOD = r"FY\d{2}(?:-(?:Q[1-4]|H[12]|9M))?"
REFERENCE = re.compile(r"\{\{(metric|fact):([a-z][a-z0-9_]*):(" + PERIOD + r")\}\}")
# The diligence report's references (fin.report.dd_render): a named figure of the engagement in a
# period, which may be the stub (FY26-YTD-SEP).
STUB = r"FY\d{2}-YTD-[A-Z]{3}"
FIGURE = re.compile(r"\{\{(figure):([a-z][a-z0-9_]*):(" + PERIOD + "|" + STUB + r")\}\}")
FISCAL_YEAR = re.compile(r"\b" + PERIOD + r"\b")
DIGIT = re.compile(r"\d")  # any Unicode digit
# A masked value (fin.diligence.mask.PREFIX: CUST-0001, EMP-0012, ACCT-0001, ...): an identity,
# not a number.
# The FDD report and its findings allow them (fin.report.lint.lint_fdd, fin.fdd.findings).
# A pattern, not a secret.
TOKEN = r"\b(?:CUST|VEND|USER|EMP|ECODE|ACCT)-\d+\b"  # noqa: S105
FDD_ALLOWED = re.compile(FIGURE.pattern + "|" + TOKEN)
FIGURE_EXAMPLE = "{{figure:adjusted_ebitda:FY25}}"
HEADING = re.compile(r"^## (.+)$")


def references(text: str, pattern: re.Pattern[str] = REFERENCE) -> set[tuple[str, str, str]]:
    """The references in `text` outside HTML comments, as (kind, name, period)."""
    found = (match.groups() for match in pattern.finditer(strip_comments(text)))
    return {(kind, name, period) for kind, name, period in found}


def strip_comments(text: str) -> str:
    """Remove HTML comments, keeping their line breaks so line numbers stay the same."""
    parts = []
    start = 0
    while (opening := text.find("<!--", start)) != -1:
        closing = text.find("-->", opening + 4)
        if closing == -1:
            break  # an unclosed comment is left as it is
        parts.append(text[start:opening])
        parts.append("\n" * text.count("\n", opening, closing + 3))
        start = closing + 3
    parts.append(text[start:])
    return "".join(parts)


def typed_number_lines(text: str, pattern: re.Pattern[str] = REFERENCE) -> list[tuple[int, str]]:
    """(line number, line) for every line with a digit outside references (of `pattern`) and FYnn
    labels."""
    found = []
    for number, line in enumerate(strip_comments(text).splitlines(), start=1):
        if DIGIT.search(FISCAL_YEAR.sub("", pattern.sub("", line))):
            found.append((number, line.strip()))
    return found


def lint(
    text: str,
    name: str,
    pattern: re.Pattern[str] = REFERENCE,
    example: str = "{{metric:ebitda_margin:FY25}} or {{fact:revenue:FY25}}",
) -> None:
    """Raise E602 listing every line of the narrative `name` with a hand-typed number; `example`
    is the reference the message suggests."""
    lines = typed_number_lines(text, pattern)
    if lines:
        listing = "\n".join(f"  line {number}: {line}" for number, line in lines)
        raise NarrativeHasTypedNumbers(
            f"{name} has hand-typed numbers on {len(lines)} line(s); write each number as a "
            f"reference such as {example}:\n" + listing
        )


# The FDD report may judge (severity, implications, deal considerations) but never value the
# company, rate it as an investment or say whether to buy it (CLAUDE.md, docs/decisions/fdd-f8.md).
# Phrases only, so ordinary words (buyer, share buy-back, fair value, actuarial valuation) pass.
JUDGING = re.compile(
    r"(?<!actuarial )(?<!inventory )(?<!stock )\bvaluations?\b"
    r"|\b(?:enterprise|equity|purchase|intrinsic|fair market) value\b|\bvalued at\b"
    r"|\b(?:over|under|fairly )valued\b|\bev\s*/\s*ebitda\b|\bp\s*/\s*e\b"
    r"|\b(?:ebitda|earnings|revenue|valuation) multiples?\b|\bprice[- ]to[- ]earnings\b"
    r"|\b(?:investment|stock|equity|buy|sell|hold) (?:rating|recommendation)s?\b"
    r"|\binvestment grade\b"
    r"|\b(?:good|bad|attractive|unattractive|sound|poor|strong|weak|compelling) investment\b"
    r"|\bworth (?:buying|acquiring|investing|paying)\b"
    r"|\b(?:should|shouldn't|should not|do not|don't|must not) (?:buy|acquire|invest)\b"
    r"|\bproceed with the (?:deal|transaction|acquisition|investment)\b|\bbuy\b(?!-back)"
    r"|\b(?:recommend|suggest|advise|favour|favor) (?:buying|acquiring|investing|purchasing)\b",
    re.IGNORECASE,
)


def phrase(text: str) -> str:
    """The phrase of `text` that the judging rule matched ('' when none)."""
    found = JUDGING.search(text)
    return found.group(0) if found else ""


def judging_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line of `text` (comments removed) with valuation,
    investment-rating or buy/don't-buy language."""
    return [
        (number, line.strip())
        for number, line in enumerate(strip_comments(text).splitlines(), start=1)
        if JUDGING.search(line)
    ]


def lint_fdd(text: str, name: str) -> None:
    """The FDD report's narrative: E602 for a hand-typed number (references are {{figure:...}};
    tokens such as CUST-0001 are names, not numbers), E972 for valuation, investment-rating or
    buy/don't-buy language."""
    lint(text, name, FDD_ALLOWED, FIGURE_EXAMPLE)
    lines = judging_lines(text)
    if lines:
        listing = "\n".join(
            f"  line {number}: {line} (matched: '{phrase(line)}')" for number, line in lines
        )
        raise NarrativeJudges(
            f"{name} has valuation, investment-rating or buy/don't-buy language on {len(lines)} "
            "line(s); the FDD report states findings, severity, implications and deal "
            "considerations, never a value, a rating or whether to buy:\n" + listing
        )


def split_sections(text: str, titles: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Split the narrative at its `## <title>` headings. `titles` maps section key to title.
    Returns (text per section key, headings that match no section). Text before the first heading
    is dropped."""
    by_title = {title.lower(): key for key, title in titles.items()}
    sections: dict[str, list[str]] = {}
    unknown: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            title = heading.group(1).rstrip()
            key = by_title.get(title.lower())
            if key is None:
                unknown.append(title)
                current = None
            else:
                current = sections.setdefault(key, [])
            continue
        if current is not None:
            current.append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}, unknown
