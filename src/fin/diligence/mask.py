"""Masking names (docs/diligence.md §2 rule 4 and §3, "Masking"; scope: docs/decisions/masking.md).

The client's instruction (answers of 2026-11-24): anonymise payroll (employee name and code) and
mask the starred register fields (sales Customer Name, handler, submitted-by; purchase Vendor
Name). So on load each of those values is stored once in `mask_values` (its kind, the real value
and a stable token) and the data tables get the token: `CUST-0001` for a customer (the sales
register and the orderbook), `VEND-0001` vendor, `USER-0001` handler or submitted-by, `EMP-0001`
employee name, `ECODE-0001` employee code. The same value (ignoring case and repeated spaces) gets
the same token in every file, whatever kind it was first masked as. Nothing else is masked: the
books (trial balance, Ledger Vouchers), bank statements and free text keep names as filed (the
owner, 2026-11-26).

`Parties` matches a name in the books or in bank text to its register token, so a register customer
still meets its Tally ledger and its bank receipts. Nothing in fin prints `mask_values`; only the
accountant export (D8) reads the real values.
"""

import re

import duckdb

from fin.extract.profiles import norm

PREFIX = {
    "customer": "CUST",
    "vendor": "VEND",
    "user": "USER",
    "employee_name": "EMP",
    "employee_code": "ECODE",
}
PARTY_KINDS = ("customer", "vendor")  # the masked kinds a ledger or a bank line can name


class Masker:
    """Tokens for one load, in the load's transaction (`con`). `file_id` (the source file the
    load adds) is set before the first new token is made."""

    def __init__(self, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
        self.con, self.run_id = con, run_id
        self.file_id: int | None = None
        rows = con.execute("SELECT kind, match_key, token FROM mask_values").fetchall()
        self.by_kind = {(kind, key): token for kind, key, token in rows}
        self.by_key = {key: token for _, key, token in rows}
        self.added = 0

    def token(self, kind: str, value: str) -> str:
        """The token for `value`, made (and stored, as `kind`) the first time it is seen. A value
        already masked as another kind keeps its token (a customer who is also a vendor is one
        party)."""
        key = norm(value)
        found = self.by_kind.get((kind, key)) or self.by_key.get(key)
        if found is not None:
            return found
        assert self.file_id is not None
        count = sum(1 for k, _ in self.by_kind if k == kind)
        token = f"{PREFIX[kind]}-{count + 1:04d}"
        self.con.execute(
            "INSERT INTO mask_values (token, kind, real_value, match_key, first_file_id, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [token, kind, " ".join(value.split()), key, self.file_id, self.run_id],
        )
        self.by_kind[(kind, key)] = token
        self.by_key.setdefault(key, token)
        self.added += 1
        return token

    def known(self, value: str) -> str | None:
        """The token of a value that is a known masked name, else None."""
        return self.by_key.get(norm(value))


class Parties:
    """The customer and vendor tokens, to match the books and bank text (which carry names as
    filed) with the registers (which carry tokens). Read-only; never printed."""

    def __init__(
        self, con: duckdb.DuckDBPyConnection, kinds: tuple[str, ...] = PARTY_KINDS
    ) -> None:
        rows = con.execute(
            "SELECT match_key, token FROM mask_values WHERE list_contains(?, kind)", [list(kinds)]
        ).fetchall()
        self.by_key = dict(rows)
        names = sorted(self.by_key, key=len, reverse=True)  # the longest name wins
        alternatives = "|".join(r"\s+".join(map(re.escape, key.split())) for key in names)
        self.pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    def token(self, name: str | None) -> str | None:
        """The token of a ledger (or other name) that is a register customer or vendor."""
        return self.by_key.get(norm(name)) if name else None

    def party(self, name: str | None) -> str | None:
        """A ledger as the registers know it: its token, else the name as stored."""
        return self.token(name) or name

    def find(self, text: str | None) -> str | None:
        """The token of the first known name in free text (bank remarks), as whole words."""
        if not text or not self.by_key:
            return None
        found = self.pattern.search(text)
        return self.by_key[norm(found.group(0))] if found else None
