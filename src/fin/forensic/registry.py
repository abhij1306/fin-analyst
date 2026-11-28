"""The forensic tests: what a test is, what it returns, and the registered tests.

A family is one module in `fin.forensic.families` holding `TESTS` (its `ForensicTest`s, in the order
they are listed) and `SETTINGS` (its `fin.forensic.settings.Setting`s). The modules are found by
name, so a new family (SPEC-19 bank, SPEC-20 counterparty) is a new module there: the runner, the
flags table, the status and the Forensic screen take it with no edit.
"""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fin.forensic.books import Context


@dataclass(frozen=True)
class Flag:
    """One flagged row: its lineage (file_id + sheet + row, or page), the voucher, the ledger as
    filed, the amount (None for a voucher gap, never 0) and the reason in words. The runner adds the
    account, the party token and whether the amount is at or above the query threshold."""

    file_id: int | None
    sheet: str | None
    row: int | None
    source: str
    reason: str
    amount: Decimal | None = None
    ledger: str | None = None
    voucher_type: str | None = None
    voucher_no: str | None = None
    entry_date: date | None = None
    page: int | None = None


@dataclass
class Outcome:
    """'ran' (with its flags, maybe none), 'too_few' (population below minimum; nothing flagged)
    or 'cannot_run' (needs: what is missing). near_misses: rows just outside a threshold."""

    outcome: str = "ran"
    flags: list[Flag] = field(default_factory=list)
    population: int | None = None
    minimum: int | None = None
    needs: str | None = None
    not_whole: int | None = None
    near_misses: int = 0

    @classmethod
    def too_few(cls, population: int, minimum: int, not_whole: int | None = None) -> "Outcome":
        return cls("too_few", population=population, minimum=minimum, not_whole=not_whole)

    @classmethod
    def cannot(cls, needs: str) -> "Outcome":
        return cls("cannot_run", needs=needs)


@dataclass(frozen=True)
class ForensicTest:
    """name: the code (je_round); title: what it flags, in words; settings: the keys it reads (a
    key with no value makes it "cannot run"); kinds: the source file kinds it reads (a period
    without them loaded makes it "cannot run"); run: the test on one period."""

    name: str
    title: str
    settings: tuple[str, ...]
    run: Callable[["Context"], Outcome]
    kinds: tuple[str, ...] = ("ledger_vouchers",)
    family: str = ""


@cache
def families() -> tuple[ModuleType, ...]:
    """The family modules of fin.forensic.families, by module name."""
    from fin.forensic import families as package

    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    return tuple(importlib.import_module(f"{package.__name__}.{name}") for name in names)


@cache
def tests() -> dict[str, ForensicTest]:
    """name -> test, family by family. A name in two families is a programming error."""
    found: dict[str, ForensicTest] = {}
    for module in families():
        family = module.__name__.rsplit(".", 1)[-1]
        for test in getattr(module, "TESTS", ()):
            if test.name in found:
                raise RuntimeError(f"forensic test {test.name} is registered twice")
            found[test.name] = ForensicTest(**{**test.__dict__, "family": family})
    return found


def describe() -> list[dict[str, Any]]:
    """Each test's name, family and title (the Forensic screen, `fin forensic status`)."""
    return [{"test": t.name, "family": t.family, "title": t.title} for t in tests().values()]
