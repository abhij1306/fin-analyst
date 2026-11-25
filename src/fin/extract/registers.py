"""Read a billing-system registers workbook (a sales sheet and a purchase sheet) with a profile.

A `registers` profile (`fin.extract.profiles`) gives the headers of each sheet kind.
`read_registers` finds, for each kind, the one sheet that has all of that kind's headers (the column
header may be on any of the first rows) and returns its non-blank rows with sheet and row number, as
`fin.extract.ledger` does for Tally exports. What the rows mean is `fin.diligence.registers`'s.

E807 when no profile fits, with the title and header lines of every sheet listed (never data rows,
which hold names; known masked names replaced) so Claude can write a profile; also when a kind is
found on more than one sheet, or one sheet fits both kinds.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from fin.audit.errors import LayoutUnknown
from fin.extract.excel import open_workbook
from fin.extract.ledger import SheetRow, _header, _rows, _top_rows
from fin.extract.profiles import Profile, load_profile, profiles_for


@dataclass(frozen=True)
class RegisterTable:
    profile: Profile
    rows: dict[str, list[SheetRow]]  # sheet kind ('sales', 'purchase') -> rows, in sheet order


def read_registers(
    path: Path, profile_name: str | None = None, hide: Callable[[str], str] = str
) -> RegisterTable:
    """The rows of `path` read with the named `registers` profile, or the first one that fits."""
    if profile_name is not None:
        candidates = [load_profile(profile_name)]
        if candidates[0].kind != "registers":
            raise LayoutUnknown(
                f"column profile {profile_name!r} reads {candidates[0].kind}, not registers"
            )
    else:
        candidates = profiles_for("registers")
    book = open_workbook(path, data_only=True)
    problems = []
    for profile in candidates:
        found = {}
        for part, columns in profile.parts.items():
            sub = replace(profile, columns=columns)
            hits = [(s, h) for s in book.worksheets if (h := _header(s, sub)) is not None]
            if len(hits) > 1:
                names = ", ".join(repr(s.title) for s, _ in hits)
                problems.append(
                    f"{profile.name}: the {part} headers are on several sheets ({names})"
                )
            if len(hits) == 1:
                found[part] = hits[0]
        if len(found) == len(profile.parts):
            if len({sheet.title for sheet, _ in found.values()}) < len(found):
                problems.append(
                    f"{profile.name}: one sheet has both the sales and purchase headers"
                )
                continue
            rows = {
                part: _rows(sheet, first, cols) for part, (sheet, (first, cols)) in found.items()
            }
            if all(rows.values()):
                return RegisterTable(profile, rows)
            empty = ", ".join(part for part, r in rows.items() if not r)
            problems.append(f"{profile.name}: no rows below the {empty} column header")
    names = ", ".join(p.name for p in candidates) or "none"
    shown = "; ".join(
        f"sheet {s.title!r}: " + " / ".join(_top_rows(s, hide)) for s in book.worksheets
    )
    extra = f" Problems: {'; '.join(problems)}." if problems else ""
    raise LayoutUnknown(
        f"{path.name}: no registers column profile matches its headers (tried: {names}).{extra} "
        f"Title and header lines: {shown or 'none'}. Write a profile in templates/profiles/ "
        "(docs/CUSTOMISING.md) and ingest again with --profile"
    )
