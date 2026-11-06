"""The report narrative: lint (E602), references and sections.

Claude writes the narrative (`reports/<company>/<scope>/narrative.md`); it never types a number.
Every number is a reference that the renderer replaces with a value from the company database:

    {{metric:<metric>:<FY>}}   e.g. {{metric:ebitda_margin:FY25}}  -> 18.0%
    {{fact:<account>:<FY>}}    e.g. {{fact:revenue:FY25}}          -> ₹123.45 Cr

The lint rule is strict: after removing HTML comments, references and fiscal-year labels written as
`FY` plus two digits (`FY25`), any digit left on a line is a hand-typed number (E602). That includes
ordered-list markers ("1."), percentages, years such as 2025 and digits inside words.
"""

import re

from fin.audit.errors import NarrativeHasTypedNumbers

REFERENCE = re.compile(r"\{\{(metric|fact):([a-z][a-z0-9_]*):(FY\d{2})\}\}")
FISCAL_YEAR = re.compile(r"\bFY\d{2}\b")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
DIGIT = re.compile(r"\d")  # any Unicode digit
HEADING = re.compile(r"^## (.+?)\s*$")


def strip_comments(text: str) -> str:
    """Remove HTML comments, keeping their line breaks so line numbers stay the same."""
    return COMMENT.sub(lambda match: "\n" * match.group().count("\n"), text)


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
            key = by_title.get(heading.group(1).lower())
            if key is None:
                unknown.append(heading.group(1))
                current = None
            else:
                current = sections.setdefault(key, [])
            continue
        if current is not None:
            current.append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}, unknown
