"""Words to lines, and the title, scope, unit and period of a page (see fin.extract.pdf)."""

import calendar
import re
from datetime import date
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import PdfminerException

from fin.audit.errors import FileEncrypted, FileUnreadable, ScannedPdf
from fin.extract.pdf_model import (
    _DAY_FIRST,
    _FIGURE,
    _FIGURE_PART,
    _FY_RANGE,
    _MONTH_FIRST,
    _NUMBERED,
    _NUMERIC_DATE,
    _RESULTS_TITLE,
    LINE_TOLERANCE,
    SPLIT_GAP,
    SPREAD_STRADDLE,
    STATEMENT_TITLES,
    TITLE_LINES,
    TOUCHING,
    Line,
    Word,
)
from fin.extract.periods import MONTHS, STATEMENTS
from fin.extract.units import unit_in


def open_pdf(path: Path, *, text_layer: bool = True) -> Any:
    """Open a PDF with pdfplumber. Raises E101 (unreadable), E102 (encrypted), and E104 (scanned)
    unless `text_layer` is False (an evidence crop of a page the PDF add-on read by OCR)."""
    try:
        pdf = pdfplumber.open(path)
    except PdfminerException as exc:
        if isinstance(exc.args[0] if exc.args else None, PDFEncryptionError):
            raise FileEncrypted(f"{path.name}: the PDF is password protected") from exc
        raise FileUnreadable(f"{path.name}: not a readable PDF ({exc})") from exc
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc
    if text_layer and not any(page.chars for page in pdf.pages):
        pdf.close()
        raise ScannedPdf(f"{path.name}: no text layer")
    return pdf


def page_parts(page: Any) -> list[list[Line]]:
    """A page's lines, or the lines of each half of a landscape page printed as two portrait pages
    side by side (no words cross its middle). Raises E101."""
    try:
        words = page.extract_words(extra_attrs=["fontname"])
    except PSException as exc:
        raise FileUnreadable(f"page {page.page_number} cannot be parsed ({exc})") from exc
    middle = float(page.width) / 2
    straddling = sum(1 for w in words if w["x0"] < middle < w["x1"])
    if page.width > page.height and words and straddling <= SPREAD_STRADDLE:
        left = [w for w in words if (w["x0"] + w["x1"]) / 2 < middle]
        right = [w for w in words if (w["x0"] + w["x1"]) / 2 >= middle]
        return [_lines(half) for half in (left, right) if half]
    return [_lines(words)]


def _lines(words: list[Word]) -> list[Line]:
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[Line] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][0]["top"]) < LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [_join_split_numbers(sorted(line, key=lambda w: w["x0"])) for line in lines]


def _join_split_numbers(line: Line) -> Line:
    """Rejoin a figure the PDF stores as touching pieces ('76,189.2' + '1'), or as pieces a little
    apart that only together make a figure ('9,906.' + '15', '1' + ',806.06', '(' + '0.11)')."""
    joined: Line = []
    for word in line:
        previous = joined[-1] if joined else None
        gap = word["x0"] - previous["x1"] if previous is not None else 0.0
        if (
            previous is not None
            and gap < SPLIT_GAP
            and _FIGURE_PART.match(previous["text"])
            and _FIGURE_PART.match(word["text"])
            and (
                gap < TOUCHING
                or (
                    _FIGURE.match(previous["text"] + word["text"]) is not None
                    and not (_FIGURE.match(previous["text"]) and _FIGURE.match(word["text"]))
                )
            )
        ):
            joined[-1] = {
                **previous,
                "text": previous["text"] + word["text"],
                "x1": word["x1"],
                "bottom": max(previous["bottom"], word["bottom"]),
            }
        else:
            joined.append(word)
    return joined


def line_text(line: Line) -> str:
    return " ".join(w["text"] for w in line)


def _title(lines: list[Line], numbered: bool = False) -> tuple[str, re.Match[str]] | None:
    """(statement, title match) from the page's top lines. `numbered`: first look for a primary
    statement titled as a numbered note ("1. Balance Sheet as on")."""
    if numbered:
        found = _numbered_title(lines)
        if found is not None:
            return found
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        for statement, title in STATEMENT_TITLES:
            match = title.search(text)
            if match:
                return statement, match
    return None


def _numbered_title(lines: list[Line]) -> tuple[str, re.Match[str]] | None:
    """(statement, title match) for a primary statement titled as a numbered note, or None."""
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        if not _NUMBERED.match(text):
            continue
        for statement, title in STATEMENT_TITLES:
            match = title.search(_NUMBERED.sub("", text))
            if match and statement in STATEMENTS:
                return statement, match
    return None


def statement_of(lines: list[Line], numbered: bool = False) -> str | None:
    """The statement a page holds, from its title lines, or None (cover, auditor's report).
    `numbered`: also read a statement numbered as a note ("1. Balance Sheet as on"), as results
    documents print them."""
    found = _title(lines, numbered)
    return found[0] if found else None


def scope_of(lines: list[Line], numbered: bool = False) -> str | None:
    """'standalone' or 'consolidated' from the title; None when it does not say."""
    found = _title(lines, numbered)
    words = [g for g in found[1].groups() if g] if found else []
    return words[0].lower() if words else None


def is_results(lines: list[Line]) -> bool:
    """True for a page titled as quarterly or half-year results."""
    found = _title(lines)
    return found is not None and _RESULTS_TITLE.search(found[1].group(0)) is not None


def unit_of(lines: list[Line]) -> str | None:
    """The unit named on the page ("lakh", "crore", ...), or None."""
    for line in lines:
        unit = unit_in(line_text(line))
        if unit is not None:
            return unit
    return None


def period_end_in(text: str) -> date | None:
    """The period end a column heading names: '31 March 2024', 'March 31, 2025', '31st March,
    2025', '31.03.2025', '31-Mar-2025', '30-Sep-25' or '2023-24' (year ended 31 March 2024). None
    if it names no date, or a date that is not a month end (such as '1 April 2023', an opening
    balance)."""
    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        return _month_end(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    for pattern, day_group, month_group in ((_DAY_FIRST, 1, 2), (_MONTH_FIRST, 2, 1)):
        for match in pattern.finditer(text):
            month = MONTHS.get(match.group(month_group).lower())
            if month is not None:
                year = int(match.group(3))
                year = year + 2000 if year < 100 else year
                return _month_end(year, month, int(match.group(day_group)))
    fy_range = _FY_RANGE.search(text)
    if fy_range:
        start, end = int(fy_range.group(1)), int(fy_range.group(2))
        if end % 100 == (start + 1) % 100:
            return date(start + 1, 3, 31)
    return None


def _month_end(year: int, month: int, day: int) -> date | None:
    if not 1 <= month <= 12 or day != calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)
