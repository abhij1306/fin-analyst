"""Reading one statement page into value rows, and evidence crops (see fin.extract.pdf)."""

from dataclasses import dataclass, field
from pathlib import Path

from fin.audit.errors import (
    DataFolderNotWritable,
    EvidenceSourceMissing,
    ParseError,
    PeriodsNotDetected,
)
from fin.extract.pdf_columns import (
    _is_note,
    _is_partial_row,
    _is_tail,
    _join,
    _trailing_values,
    header_starting,
    resolve_columns,
    split_row,
)
from fin.extract.pdf_lines import line_text, open_pdf
from fin.extract.pdf_model import (
    _NOTE_REF,
    _NOTES_BLOCK,
    _WORDY,
    COLUMN_TOLERANCE,
    FIGURE_TEXT,
    INDENT,
    SUB_HEADING,
    WRAP_FACTOR,
    Column,
    Line,
    PdfValue,
    Word,
    _Row,
)
from fin.extract.periods import FY_START_MONTH, fiscal_year_of
from fin.extract.units import NIL, parse_figure, to_rupees


@dataclass
class _Reader:
    """What reading a page has found so far, top to bottom."""

    rows: list[_Row] = field(default_factory=list)
    note_x: float | None = None  # left edge of the "Note" column heading
    section: str = ""  # the heading the next row sits under
    before_heading: str = ""  # the section before the last heading line, to undo a wrapped label
    last_text: Line | None = None  # the line just read, when it was a text line
    # Partial rows of plain integers, kept once a full row below shows they are in the table body
    # (a page number or footer below the table is not a row).
    pending: list[_Row] = field(default_factory=list)


def read_page(
    lines: list[Line],
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
    columns: list[Column],
    *,
    results: bool = False,
    start_month: int = FY_START_MONTH,
) -> tuple[list[PdfValue], list[Column]]:
    """Values of one statement page, and the columns in force at its end ([] if none).

    `columns` are the columns carried over from the page before (a continued statement).
    `results`: the page is a quarterly or half-year results statement, so a flow column must name
    its length. Raises PeriodsNotDetected (E202) when a header's periods cannot be told.
    """
    state = _Reader()
    i = 0
    while i < len(lines):
        found = header_starting(lines, i)
        if found is not None:
            header, end = found
            state.note_x = state.note_x or _note_x([w for ln in lines[i:end] for w in ln])
            i = end
            columns = _header_columns(
                state, header, statement, page, results=results, start_month=start_month
            )
            continue
        below = lines[i + 1] if i + 1 < len(lines) else None
        if _read_line(state, lines[i], below, columns, statement, page):
            break
        i += 1
    found_values = [
        _value(r, column, r.values[column.key], page, statement, scope, unit, start_month)
        for r in state.rows
        for column in r.columns
        if column.key in r.values
    ]
    return found_values, columns


def _header_columns(
    state: _Reader,
    header: list[Column],
    statement: str,
    page: int,
    *,
    results: bool,
    start_month: int,
) -> list[Column]:
    """The columns a header row names. On a notes page, a header whose periods cannot be told
    gives no columns ([]) rather than raising PeriodsNotDetected (E202)."""
    try:
        columns = resolve_columns(header, statement, page, results=results, start_month=start_month)
    except PeriodsNotDetected:
        if statement != "notes":
            raise
        return []
    state.last_text = None
    return columns


def _read_line(
    state: _Reader,
    line: Line,
    below: Line | None,
    columns: list[Column],
    statement: str,
    page: int,
) -> bool:
    """Take in one line that is not a header: a row of figures, a heading or text. `below`: the
    next line, if any. True when the line ends the table (a "Notes:" line)."""
    words, row = split_row(line, columns) if columns else (line, {})
    pending = _integer_row(line, columns) if columns and not row else None
    if pending is not None:
        words, row = pending
    elif not row:
        if columns and _unplaced_figures(line, columns, statement, page):
            state.last_text = None
            return False
        return _text_line(state, line, columns, statement)
    words, note = _strip_note(words, state.note_x)
    height = max(w["bottom"] - w["top"] for w in line)
    label, wrapped = _row_label(state, words, line, height)
    if _is_nil_heading(row, label, line, below):
        # A heading printed with nil dashes ("Current assets - -"), items indented below it.
        state.last_text, state.before_heading, state.section = words, state.section, label
        return False
    new = _Row(label, row, columns, state.section, note, line[0]["top"], height, wrapped)
    state.last_text = None
    if pending is not None:
        state.pending.append(new)
        return False
    kept = [new]
    if len(row) == len(columns):  # a full row: the integer rows above it are in the table body
        kept = [*state.pending, new]
        state.pending.clear()
    for r in kept if statement != "notes" else []:
        _check_figures(r.label, r.values, page)
    state.rows = sorted([*state.rows, *kept], key=lambda r: r.top)
    return False


def _integer_row(line: Line, columns: list[Column]) -> tuple[Line, dict[str, Word]] | None:
    """(label words, values) for a line with a label of its own and plain integers under some
    columns (`_is_partial_row` with integers); None otherwise."""
    cut, values = _trailing_values(line, columns)
    if not _is_partial_row(line[:cut], values, columns, integers=True):
        return None
    return line[:cut], values


def _unplaced_figures(line: Line, columns: list[Column], statement: str, page: int) -> bool:
    """True for a line that is not a row but ends in figures (with a comma or decimal point) under
    the columns: never a heading. On a statement page fin cannot tell which column they belong
    to, so it raises E204 rather than guess; on a notes page the line is passed over."""
    found = _trailing_values(line, columns)[1]
    if not any(FIGURE_TEXT.search(w["text"]) for w in found.values()):
        return False
    if statement != "notes":
        raise ParseError(
            f"page {page}: the figures of {line_text(line)[:60]!r} cannot be placed under the "
            "period columns"
        )
    return True


def _is_nil_heading(row: dict[str, Word], label: str, line: Line, below: Line | None) -> bool:
    """True for a row whose figures are all nil dashes, with the next line indented below it."""
    if not all(w["text"] in NIL for w in row.values()) or SUB_HEADING.match(label):
        return False
    return below is not None and below[0]["x0"] > line[0]["x0"] + INDENT


def _note_x(words: Line) -> float | None:
    """The left edge of the first 'Note' column heading word among `words`, or None."""
    return next((w["x0"] for w in words if _is_note(w)), None)


def _text_line(state: _Reader, line: Line, columns: list[Column], statement: str) -> bool:
    """Take in a line with no row of figures: a heading, the tail of a wrapped label or heading, or
    text in the figure columns (ignored). True when it is a "Notes:" line that ends the table."""
    text = line_text(line)
    if not state.rows:
        state.note_x = state.note_x or _note_x(line)
    if columns and statement != "notes" and _NOTES_BLOCK.match(text):
        return True
    if columns and line[0]["x0"] >= columns[0].x0 - COLUMN_TOLERANCE:
        return False  # column sub-heading ("(Restated)") or margin text, not a heading
    if state.rows and state.last_text is None and _is_tail(state.rows[-1], line, text):
        state.rows[-1].label = _join(state.rows[-1].label, text)
        return False
    if state.last_text is not None and text[:1].islower():  # a heading wrapped onto two lines
        if state.section == line_text(state.last_text):
            state.section = _join(state.section, text)
        state.last_text = state.last_text + line
        return False
    state.last_text = line
    if _WORDY.search(text):
        state.before_heading = state.section
        if not SUB_HEADING.match(text):
            state.section = text
    return False


def _strip_note(words: Line, note_x: float | None) -> tuple[Line, str | None]:
    """A row's label words without the note reference at their end (under the "Note" column at
    `note_x`), and that reference."""
    if note_x is None:
        return words, None
    words = list(words)
    notes: list[str] = []
    while words and _NOTE_REF.match(words[-1]["text"]):
        if words[-1]["x0"] < note_x - COLUMN_TOLERANCE:
            break
        notes.insert(0, words.pop()["text"])
    return words, " ".join(notes) or None


def _row_label(state: _Reader, words: Line, line: Line, height: float) -> tuple[str, bool]:
    """A row's label, and whether it was taken from the line above. A row with no label words
    takes the text line just above it (a label wrapped around its figures), or else the heading it
    sits under (an unlabelled subtotal)."""
    label = line_text(words)
    if label:
        return label, False
    if state.last_text is not None and line[0]["top"] - state.last_text[-1]["top"] < (
        WRAP_FACTOR * height
    ):
        state.section = state.before_heading
        return line_text(state.last_text), True
    return state.section, False


def _check_figures(label: str, row: dict[str, Word], page: int) -> None:
    """Raise E204 when a row's figures look misread: its label holds a figure (the page's figures
    do not line up with their labels), or a figure has an unpaired bracket (its sign is unknown)."""
    if FIGURE_TEXT.search(label):
        raise ParseError(
            f"page {page}: the label {label[:60]!r} holds a figure; the figures do not line up "
            "with their labels"
        )
    for word in row.values():
        raw = word["text"]
        if raw.startswith("(") != raw.endswith(")"):
            raise ParseError(f"page {page}: figure {word['text']!r} has an unpaired bracket")


def _value(
    row: _Row,
    column: Column,
    word: Word,
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
    start_month: int,
) -> PdfValue:
    assert column.period is not None and column.months is not None
    value = parse_figure(word["text"])
    assert value is not None  # split_row takes only figures
    return PdfValue(
        label=row.label,
        fiscal_year=fiscal_year_of(column.period_end, start_month),
        period=column.period,
        months=column.months,
        period_end=column.period_end,
        raw=word["text"],
        value=value,
        rupees=to_rupees(value, unit),
        unit=unit,
        page=page,
        bbox=(word["x0"], word["top"], word["x1"], word["bottom"]),
        statement=statement,
        scope=scope,
        section=row.section,
        note=row.note,
    )


CROP_MARGIN = 24.0  # points above and below the value in an evidence crop
CROP_RESOLUTION = 150  # dpi of an evidence crop


def render_crop(
    path: Path, page: int, bbox: tuple[float, float, float, float] | None, out: Path
) -> Path:
    """Write a PNG of the full-width band around `bbox` on `page` (1-based), bbox highlighted; the
    whole page when `bbox` is None (a figure read by Claude has no position).

    The band spans the page width so the row label shows next to the value. E604 when the PDF has
    no such page (a reading can name any page), E003 when the PNG cannot be written.
    """
    with open_pdf(path, text_layer=False) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise EvidenceSourceMissing(f"archived source {path.name} has no page {page}")
        p = pdf.pages[page - 1]
        if bbox is None:
            image = p.to_image(resolution=CROP_RESOLUTION)
        else:
            _, top, _, bottom = bbox
            region = (
                0.0,
                max(0.0, top - CROP_MARGIN),
                float(p.width),
                min(float(p.height), bottom + CROP_MARGIN),
            )
            image = p.crop(region).to_image(resolution=CROP_RESOLUTION)
            image.draw_rect(bbox, stroke=(220, 0, 0), fill=(255, 230, 0, 60), stroke_width=2)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            image.save(out)
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot write {out}: {exc}") from exc
    return out
