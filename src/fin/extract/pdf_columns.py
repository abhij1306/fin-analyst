"""Column headers, period-label spreads and row splitting (see fin.extract.pdf)."""

import re
from dataclasses import replace
from itertools import combinations, pairwise
from typing import Any

from fin.audit.errors import PeriodsNotDetected
from fin.extract.pdf_lines import line_text, period_end_in
from fin.extract.pdf_model import (
    _DANGLING,
    _DASH,
    _NUMBER,
    _YEAR,
    COLUMN_TOLERANCE,
    FIGURE_TEXT,
    HEADER_GAP,
    HEADER_LINES,
    LABEL_LINES,
    WORD_GAP,
    WRAP_FACTOR,
    Column,
    Line,
    Word,
    _Row,
)
from fin.extract.periods import PHRASE, months_in, period_of, resolve_months


def _is_number(text: str) -> bool:
    """A figure ("1,234.56", "(56)", ".82") or a nil dash."""
    return bool(_NUMBER.match(text) or _DASH.match(text))


def _is_value_like(line: Line) -> bool:
    """True when a line ends in a figure (not a bare year), so it cannot be part of a header."""
    last = line[-1]["text"]
    return _is_number(last) and not _YEAR.match(last)


def _runs(line: Line) -> list[list[Word]]:
    """Runs of close words on a line (gaps of at most WORD_GAP)."""
    runs: list[list[Word]] = []
    run: list[Word] = []
    for word in line:
        if run and word["x0"] - run[-1]["x1"] > WORD_GAP:
            runs.append(run)
            run = []
        run.append(word)
    if run:
        runs.append(run)
    return runs


def _stacks(block: list[Line]) -> list[list[Word]]:
    """Group a header block's words into column stacks: runs of close words on a line, merged with
    runs on the other lines that overlap them horizontally."""
    stacks: list[tuple[float, float, list[Word]]] = []
    for run in (run for line in block for run in _runs(line)):
        x0, x1, words = run[0]["x0"], run[-1]["x1"], list(run)
        keep = []
        for sx0, sx1, swords in stacks:
            if sx0 <= x1 and x0 <= sx1:
                x0, x1, words = min(x0, sx0), max(x1, sx1), swords + words
            else:
                keep.append((sx0, sx1, swords))
        stacks = [*keep, (x0, x1, words)]
    return [sorted(words, key=lambda w: (w["top"], w["x0"])) for _, _, words in stacks]


def block_columns(block: list[Line]) -> list[Column]:
    """The period columns a header block names, left to right; [] if fewer than two. Two columns
    can end on the same date (a quarter and a year ending 31 March)."""
    columns = []
    for stack in _stacks(block):
        period = period_end_in(" ".join(w["text"] for w in stack))
        if period is not None:
            columns.append(Column(period, min(w["x0"] for w in stack), max(w["x1"] for w in stack)))
    columns.sort(key=lambda c: c.x0)
    return columns if len(columns) >= 2 else []


def header_starting(lines: list[Line], i: int) -> tuple[list[Column], int] | None:
    """The column header that starts at line i, with its period lengths (`complete_header`), and
    the index of the line after it; None when no header starts there. A header found at i that
    ends where the one found at i + 1 ends starts at i + 1 instead (line i is a title above it)."""
    found = header_at(lines, i)
    if found is None:
        return None
    later = header_at(lines, i + 1)
    if later is not None and later[1] == found[1]:
        return None
    return complete_header(lines, i, found[1])


def header_at(lines: list[Line], i: int) -> tuple[list[Column], int] | None:
    """The shortest column header starting at line i, and the index of the line after it."""
    for end in range(i, min(i + HEADER_LINES, len(lines))):
        line = lines[end]
        if _is_value_like(line):
            return None
        if end > i and line[0]["top"] - lines[end - 1][0]["top"] > HEADER_GAP:
            return None
        columns = block_columns(lines[i : end + 1])
        if columns:
            return columns, end + 1
    return None


def complete_header(lines: list[Line], start: int, end: int) -> tuple[list[Column], int]:
    """The columns of the header found at lines[start:end], and the index of the line after it.
    The period-length labels ("Quarter ended") in the header or in the few lines above it are
    spread over the columns (`spread`)."""
    columns = block_columns(lines[start:end])
    region = start
    while region > max(start - LABEL_LINES, 0) and not _is_value_like(lines[region - 1]):
        region -= 1
    runs = [run for line in lines[region:end] for run in _runs(line)]
    lengths = _labels(runs, PHRASE, columns, months_in)
    if lengths is not None:
        columns = [replace(c, months=months) for c, months in zip(columns, lengths, strict=True)]
    return columns, end


def _labels(
    runs: list[list[Word]], pattern: re.Pattern[str], columns: list[Column], value: Any
) -> list[Any] | None:
    """The value of the label over each column, from the runs that are such a label; None when
    there are none, or more labels than columns."""
    found = []
    for run in runs:
        text = line_text(run).strip()
        center = (run[0]["x0"] + run[-1]["x1"]) / 2
        if pattern.match(text) and columns[0].x0 - 3 * COLUMN_TOLERANCE <= center:
            found.append((center, value(text)))
    found.sort(key=lambda item: item[0])
    if not found or len(found) > len(columns):
        return None
    return spread(columns, found)


def spread(columns: list[Column], labels: list[tuple[float, Any]]) -> list[Any]:
    """Give each column the value of the label over it. Labels (x center, value), left to right,
    each head a run of adjacent columns; the runs chosen are those whose centers lie closest to
    their labels (least squared distance)."""
    n, k = len(columns), len(labels)
    best: tuple[float, tuple[int, ...]] | None = None
    for cuts in combinations(range(1, n), k - 1):
        bounds = (0, *cuts, n)
        cost = sum(
            (center - (columns[a].center + columns[b - 1].center) / 2) ** 2
            for (a, b), (center, _) in zip(pairwise(bounds), labels, strict=True)
        )
        if best is None or cost < best[0]:
            best = (cost, bounds)
    assert best is not None
    values: list[Any] = []
    for (a, b), (_, label) in zip(pairwise(best[1]), labels, strict=True):
        values.extend([label] * (b - a))
    return values


def split_row(line: Line, columns: list[Column]) -> tuple[Line, dict[str, Word]]:
    """Split a line into its leading words and its values {column key: word}.

    Values are the trailing number words, each under a different period column. A column owns the
    band from halfway to its left neighbour to halfway to its right one; the first starts
    COLUMN_TOLERANCE left of its header and the last runs to the page edge. A line is a value row
    when every column has a value, or some do and it is plainly a row (`_is_partial_row`); a column
    with no value is left out. Otherwise the values are {}.
    """
    cut, values = _trailing_values(line, columns)
    if len(values) < len(columns) and not _is_partial_row(line[:cut], values, columns):
        return line, {}
    return line[:cut], {c.key: values[c.key] for c in columns if c.key in values}


def _trailing_values(line: Line, columns: list[Column]) -> tuple[int, dict[str, Word]]:
    """The index where a line's trailing number words start, and those words {column key: word},
    each under a different column (see `split_row`)."""
    values: dict[str, Word] = {}
    cut = len(line)
    for i in range(len(line) - 1, -1, -1):
        word = line[i]
        if not _is_number(word["text"]):
            break
        center = (word["x0"] + word["x1"]) / 2
        bounds = [(a.center + b.center) / 2 for a, b in pairwise(columns)]
        column = columns[sum(1 for bound in bounds if center > bound)]
        if column is columns[0] and word["x1"] < column.x0 - COLUMN_TOLERANCE:
            break
        if column.key in values:
            break
        values[column.key] = word
        cut = i
    return cut, values


def _is_partial_row(
    label: Line, values: dict[str, Word], columns: list[Column], *, integers: bool = False
) -> bool:
    """True for a line with figures under some columns and nothing printed under the others: it
    has a label of its own, wholly left of the columns, and each figure has a comma or decimal
    point or is a nil dash (so a page number or a registration number is not a row). `integers`:
    plain integers count too (`_read_line` keeps such a row only inside the table body)."""
    if not values or not label:
        return False
    if max(w["x1"] for w in label) >= columns[0].x0 - COLUMN_TOLERANCE:
        return False
    return integers or all(
        FIGURE_TEXT.search(w["text"]) or _DASH.match(w["text"]) for w in values.values()
    )


def _is_note(word: Word) -> bool:
    """A 'Note' / 'Notes' / 'Note No.' column heading word."""
    return word["text"].rstrip(".") in ("Note", "Notes", "NOTE", "NOTES")


def _is_tail(row: _Row, line: Line, text: str) -> bool:
    """True when a text line just below a value row ends that row's label: the label wrapped around
    the row's figures, or it ends in a hyphen or a word like 'and' / 'of', or the line starts in
    lower case."""
    gap = line[0]["top"] - row.top
    if row.wrapped and gap < WRAP_FACTOR * row.height:
        return True
    dangling = row.label.endswith("-") or _DANGLING.search(row.label) is not None
    return gap < 2 * row.height and (dangling or text[:1].islower())


def _join(head: str, tail: str) -> str:
    return head + tail if head.endswith("-") else f"{head} {tail}"


def resolve_columns(
    columns: list[Column], statement: str, page: int, *, results: bool, start_month: int
) -> list[Column]:
    """Name each column's period for this statement (`fin.extract.periods`). Raises E202 when a
    column's period cannot be told, or two columns would hold the same period."""
    where = f"page {page}"
    resolved = []
    for column in columns:
        if results and statement != "balance_sheet" and column.months is None:
            raise PeriodsNotDetected(
                f"{where}: results statement column ending {column.period_end.isoformat()} does "
                "not say whether it is a quarter, half year or year"
            )
        months = resolve_months(
            statement, column.period_end, column.months, start_month, where=where
        )
        period = period_of(column.period_end, months, start_month, where=where)
        resolved.append(replace(column, months=months, period=period))
    names = [c.period for c in resolved]
    if len(set(names)) < len(names):
        listed = ", ".join(c.period for c in resolved if c.period)
        raise PeriodsNotDetected(f"{where}: two columns hold the same period ({listed})")
    return resolved
