"""The README troubleshooting table must list every error code in fin.audit.errors."""

import inspect
import re
from pathlib import Path

from fin.audit import errors

README = Path(__file__).parents[2] / "README.md"


def defined_codes() -> set[str]:
    return {
        cls.code
        for _, cls in inspect.getmembers(errors, inspect.isclass)
        if issubclass(cls, errors.FinError) and cls.code
    }


def readme_codes() -> set[str]:
    return set(re.findall(r"^\| (E\d{3}) \|", README.read_text(encoding="utf-8"), re.MULTILINE))


def test_readme_lists_every_error_code() -> None:
    assert defined_codes() - readme_codes() == set()


def test_readme_lists_no_unknown_error_code() -> None:
    assert readme_codes() - defined_codes() == set()
