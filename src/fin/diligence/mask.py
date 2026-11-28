"""Masking names (docs/diligence.md §2 rule 4 and §3, "Masking"; scope: docs/decisions/masking.md).

The client's instruction (answers of 2026-11-24): anonymise payroll (employee name and code) and
mask the starred register fields (sales Customer Name, handler, submitted-by; purchase Vendor
Name). So on load each of those values is stored once in `mask_values` (its kind, the real value
and a stable token) and the data tables get the token: `CUST-0001` for a customer (the sales
register and the orderbook), `VEND-0001` vendor, `USER-0001` handler or submitted-by, `EMP-0001`
employee name, `ECODE-0001` employee code. The same value (ignoring case and repeated spaces) gets
the same token in every file, whatever kind it was first masked as. Nothing else is masked: the
books (trial balance, Ledger Vouchers), bank statements and free text keep names as filed (the
owner, 2026-11-26). A bank statement's account number is masked too (`ACCT-0001`, kind
`bank_account`; the client, 2026-11-27): `fin.diligence.bank_mask`. An account number is its own
kind: it never takes, nor gives, the token of a name or code with the same text.

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
    "bank_account": "ACCT",
}
ACCOUNT = "bank_account"
PARTY_KINDS = ("customer", "vendor")  # the masked kinds a ledger or a bank line can name


class Masker:
    """Tokens for one load, in the load's transaction (`con`). `file_id` (the source file the
    load adds) is set before the first new token is made, except for a bank account number masked
    when its PDF's parse is saved, before its file loads (`first_file_id` NULL)."""

    def __init__(self, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
        self.con, self.run_id = con, run_id
        self.file_id: int | None = None
        rows = con.execute("SELECT kind, match_key, token FROM mask_values").fetchall()
        self.by_kind = {(kind, key): token for kind, key, token in rows}
        self.by_key = {key: token for kind, key, token in rows if kind != ACCOUNT}
        self.added = 0

    def token(self, kind: str, value: str) -> str:
        """The token for `value`, made (and stored, as `kind`) the first time it is seen. A value
        already masked as another kind keeps its token (a customer who is also a vendor is one
        party)."""
        key = norm(value)
        found = self.by_kind.get((kind, key))
        if found is None and kind != ACCOUNT:
            found = self.by_key.get(key)
        if found is not None:
            return found
        assert self.file_id is not None or kind == ACCOUNT
        count = sum(1 for k, _ in self.by_kind if k == kind)
        token = f"{PREFIX[kind]}-{count + 1:04d}"
        self.con.execute(
            "INSERT INTO mask_values (token, kind, real_value, match_key, first_file_id, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [token, kind, " ".join(value.split()), key, self.file_id, self.run_id],
        )
        self.by_kind[(kind, key)] = token
        if kind != ACCOUNT:
            self.by_key.setdefault(key, token)
        self.added += 1
        return token

    def accounts(self) -> dict[str, str]:
        """The masked bank account numbers: number (its match key) -> token."""
        return {key: token for (kind, key), token in self.by_kind.items() if kind == ACCOUNT}

    def known(self, value: str) -> str | None:
        """The token of a value that is a known masked name, else None."""
        return self.by_key.get(norm(value))


# A party: a value masked as a customer or vendor, or one first masked as an employee or a user
# whose token a loaded register, orderbook or GSTR-2B line carries as its customer or vendor
# (Masker.token keeps the first token, so that line has no customer or vendor row of its own).
PARTY_TOKENS = """
WITH loaded AS (SELECT file_id FROM source_files WHERE status = 'loaded')
SELECT match_key, token FROM mask_values
WHERE list_contains(?, kind) OR (kind <> 'bank_account' AND token IN (
    SELECT customer FROM register_lines WHERE file_id IN (FROM loaded)
    UNION SELECT vendor FROM register_lines WHERE file_id IN (FROM loaded)
    UNION SELECT customer FROM orderbook_lines WHERE file_id IN (FROM loaded)
    UNION SELECT vendor FROM gstr2b_lines WHERE file_id IN (FROM loaded)))
ORDER BY token, match_key
"""


def party_tokens(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """(match key, token) of every party (PARTY_TOKENS)."""
    return con.execute(PARTY_TOKENS, [list(PARTY_KINDS)]).fetchall()


class Parties:
    """The party tokens (`party_tokens`), to match the books and bank text (which carry names as
    filed) with the registers (which carry tokens). Read-only; never printed."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.by_key = dict(party_tokens(con))
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
