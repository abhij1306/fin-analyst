"""Bank account numbers as tokens (SPEC-17 item 1, the client's answer of 2026-11-27;
docs/decisions/masking.md).

A bank statement's account number is stored once in `mask_values` (kind `bank_account`, token
`ACCT-0001`) and every table, `fin` output, log line and saved parse holds the token. The same
number gets the same token in every file, so the statements of one account still join. The number
is found by the bank profiles' `[header] account` pattern; wherever that number appears later as a
whole number (another statement's remarks or reference: a transfer between the company's own
accounts), it is replaced by its token too.

The saved parse of a bank PDF (`cache/bank/<sha256>.json`) is written with tokens
(`mask_parse`, before it is saved, in its own short transaction: the token outlives a load that
fails afterwards, so the saved parse can be read again); reading it turns the tokens back into the
numbers in memory only (`unmask_parse`), so the profile's pattern finds the account. The original
file in the archive and its evidence crops show the number as filed.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.db.conn import connect, transaction
from fin.diligence.bank_raw import Pages
from fin.diligence.mask import ACCOUNT, Masker
from fin.extract.profiles import Profile

TOKEN = re.compile(r"(?<![\w-])ACCT-\d{4,}(?![\w-])")


def mask_text(text: str, tokens: dict[str, str]) -> str:
    """`text` with each known account number (a whole number, not part of a longer one) as its
    token. `tokens`: number -> token."""
    for number, token in tokens.items():
        if number in text:
            text = re.sub(rf"(?<!\d){re.escape(number)}(?!\d)", token, text)
    return text


def _map_pages(pages: Pages, fn: Callable[[str], str]) -> Pages:
    """The parse with `fn` applied to every table cell and text line."""
    return {
        key: page
        | {
            "tables": [[[fn(c) for c in row] for row in grid] for grid in page["tables"]],
            "texts": [fn(t) for t in page["texts"]],
        }
        for key, page in pages.items()
    }


def numbers_in(pages: Pages, profiles: list[Profile]) -> set[str]:
    """The account numbers the profiles' `[header] account` patterns find in a parse (each table
    row as one line, and the text lines)."""
    lines = [
        " ".join(" ".join(c.split()) for c in row if c.strip())
        for page in pages.values()
        for grid in page["tables"]
        for row in grid
    ] + [t for page in pages.values() for t in page["texts"]]
    patterns = [p.header["account"] for p in profiles if p.header.get("account") is not None]
    return {m["value"] for pattern in patterns for line in lines for m in pattern.finditer(line)}


def mask_parse(run: Run, db: Path, pages: Pages, profiles: list[Profile]) -> Pages:
    """The parse as it is saved: each account number found made a token (stored in `mask_values`
    in one transaction) and replaced by it."""
    numbers = numbers_in(pages, profiles)
    if not numbers:
        return pages
    with connect(db, write=True) as con, transaction(con):
        masker = Masker(con, run.run_id)
        tokens = {n: masker.token(ACCOUNT, n) for n in sorted(numbers)}
    return _map_pages(pages, lambda text: mask_text(text, tokens))


def unmask_parse(db: Path, pages: Pages) -> Pages:
    """A saved parse with its tokens as the account numbers again, in memory, to be read."""
    with connect(db) as con:
        numbers = {token: key for key, token in Masker(con, "").accounts().items()}

    def real(text: str) -> str:
        return TOKEN.sub(lambda m: numbers.get(m.group(0), m.group(0)), text)

    return _map_pages(pages, real)


def masked_copy(db: Path, pages: Pages) -> Pages:
    """The parse with the masked account numbers as tokens, to list its lines in a message."""
    with connect(db) as con:
        tokens = Masker(con, "").accounts()
    return _map_pages(pages, lambda text: mask_text(text, tokens))


def remask_lines(con: duckdb.DuckDBPyConnection, tokens: dict[str, str]) -> int:
    """Replace newly masked account numbers in the remarks and reference of the bank lines
    already loaded (a transfer to an account loaded later); how many lines changed."""
    changed = 0
    for number, token in tokens.items():
        found: list[Any] = con.execute(
            'SELECT file_id, sheet, "row", remarks, reference FROM bank_lines '
            "WHERE contains(remarks, ?) OR contains(reference, ?)",
            [number, number],
        ).fetchall()
        for file_id, sheet, row, remarks, reference in found:
            new = [mask_text(t, {number: token}) if t else t for t in (remarks, reference)]
            if new != [remarks, reference]:
                con.execute(
                    "UPDATE bank_lines SET remarks = ?, reference = ? WHERE file_id = ? "
                    'AND sheet = ? AND "row" = ?',
                    [*new, file_id, sheet, row],
                )
                changed += 1
    return changed
