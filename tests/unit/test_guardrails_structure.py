"""Structural checks over src/fin (SPEC-00): code that goes around the shared escaping and
atomic-write helpers fails here. Each check is a function over a file's text, so a broken example is
tested too."""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "fin"
# Escaping goes through fin.output.escape; ui/format.py marks escaped text safe for Jinja.
RAW_ESCAPE = re.compile(
    r"^\s*import html\b|^\s*from html import|\bhtml\.escape\(|from markupsafe import .*\bescape\b",
    re.MULTILINE,
)
# A fixed temporary name: two runs would share it (fin.paths.temp_name gives a unique one).
FIXED_TEMP = re.compile(r"""["'][^"'\n]*\.(?:tmp|temp|part|partial|new)["']""")


def sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def raw_escapes(text: str) -> list[str]:
    return RAW_ESCAPE.findall(text)


def unescaped_workbook(text: str) -> bool:
    """A workbook made here and saved without escape.excel_book."""
    return "Workbook()" in text and ".save(" in text and "escape.excel_book(" not in text


def fixed_temp_names(text: str) -> list[str]:
    return FIXED_TEMP.findall(text)


@pytest.mark.parametrize(
    ("check", "broken"),
    [
        (raw_escapes, "import html\nx = html.escape(text)\n"),
        (raw_escapes, "from markupsafe import Markup, escape\n"),
        (unescaped_workbook, "book = Workbook()\nbook.save(path)\n"),
        (fixed_temp_names, 'path.with_suffix(".tmp")\n'),
    ],
)
def test_each_check_finds_its_broken_example(check: object, broken: str) -> None:
    assert callable(check)
    assert check(broken)


def test_text_is_escaped_only_through_fin_output() -> None:
    found = {
        rel(p): raw_escapes(p.read_text(encoding="utf-8"))
        for p in sources()
        if not rel(p).startswith("output/")
    }
    assert {name: hits for name, hits in found.items() if hits} == {}


def test_every_saved_workbook_goes_through_excel_escaping() -> None:
    found = [rel(p) for p in sources() if unescaped_workbook(p.read_text(encoding="utf-8"))]
    assert found == []


def test_no_fixed_temporary_names() -> None:
    found = {
        rel(p): fixed_temp_names(p.read_text(encoding="utf-8"))
        for p in sources()
        if rel(p) != "paths.py"  # temp_name itself
    }
    assert {name: hits for name, hits in found.items() if hits} == {}
