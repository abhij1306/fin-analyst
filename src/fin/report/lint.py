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

from fin.audit.errors import NarrativeHasTypedNumbers

PERIOD = r"FY\d{2}(?:-(?:Q[1-4]|H[12]|9M))?"
REFERENCE = re.compile(r"\{\{(metric|fact):([a-z][a-z0-9_]*):(" + PERIOD + r")\}\}")
FISCAL_YEAR = re.compile(r"\b" + PERIOD + r"\b")
DIGIT = re.compile(r"\d")  # any Unicode digit
HEADING = re.compile(r"^## (.+)$")


def references(text: str) -> set[tuple[str, str, str]]:
    """The references in `text` outside HTML comments, as (kind, name, period)."""
    return {match.groups() for match in REFERENCE.finditer(strip_comments(text))}


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


def typed_number_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line with a digit outside references and FYnn labels."""
    found = []
    for number, line in enumerate(strip_comments(text).splitlines(), start=1):
        if DIGIT.search(FISCAL_YEAR.sub("", REFERENCE.sub("", line))):
            found.append((number, line.strip()))
    return found


def lint(text: str, name: str) -> None:
    """Raise E602 listing every line of the narrative `name` with a hand-typed number."""
    lines = typed_number_lines(text)
    if lines:
        listing = "\n".join(f"  line {number}: {line}" for number, line in lines)
        raise NarrativeHasTypedNumbers(
            f"{name} has hand-typed numbers on {len(lines)} line(s); write each number as a "
            "reference such as {{metric:ebitda_margin:FY25}} or {{fact:revenue:FY25}}:\n" + listing
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
