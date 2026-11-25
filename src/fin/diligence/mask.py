"""Masking names (docs/diligence.md §2 rule 4 and §3, "Masking").

What fin prints goes to Claude, and from Claude Code to Anthropic; the diligence user's rule is to
anonymise names before any third-party processing. So on load, each masked value is stored once in
`mask_values` (its kind, the real value and a stable token) and the data tables get the token:
`CUST-0001` for a customer, `VEND-0001` vendor, `USER-0001` handler or submitted-by,
`EMP-0001` employee name, `ECODE-0001` employee code, `LEDG-0001` a ledger fin cannot classify,
`PRTY-0001` a disclosure counterparty (fin.disclosures) no loaded file had.
The same value (ignoring case and repeated spaces) gets the same token in every file, whatever
kind it was first masked as.

`Masker.text` masks free text: a value that is a known name becomes its token, and every known name
inside it, as whole words (so a short name never changes part of another word), is replaced by its
token. An employee code is never searched for in free text (a code such as "101" or "7" would
rewrite amounts and dates); `known` still finds it, and payroll stores it as a token directly.
`remask` re-runs `text` over the stored free-text columns when a load added names, so a name
learned from a later file is masked in the earlier files' rows too. Nothing in fin prints
`mask_values`; only the accountant export (D8) will read it.
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
    "ledger": "LEDG",
    "party": "PRTY",  # a counterparty in a disclosure reading (fin.disclosures) no loaded file had
}


# The stored free text that can hold a name (register_lines' FREE_TEXT fields, bank remarks, ledger
# particulars, a contract's billing terms): masked again when a later load learns a name.
FREE_TEXT_COLUMNS = (
    ("bank_lines", "remarks"),
    ("bank_lines", "reference"),
    ("ledger_entries", "particulars"),
    ("register_lines", "location"),
    ("register_lines", "order_type"),
    ("register_lines", "product"),
    ("register_lines", "service_type"),
    ("contracts", "billing_terms"),
    ("contracts", "sla_terms"),
    ("contracts", "penalty_terms"),
    ("contracts", "escalation_terms"),
    ("contracts", "renewal_terms"),
    ("walkthroughs", "evidence"),
    ("recon_items", "note"),  # the walkthrough reconciliation copies the evidence into its notes
    ("disclosures", "nature"),
    ("disclosures", "relationship"),
    ("fdd_findings", "title"),
    ("fdd_findings", "observation"),
    ("fdd_findings", "implication"),
    ("fdd_findings", "evidence"),
)


class Masker:
    """Tokens for one load, in the load's transaction (`con`). `file_id` (the source file the
    load adds) is set before the first new token is made."""

    def __init__(self, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
        self.con, self.run_id = con, run_id
        self.file_id: int | None = None
        rows = con.execute("SELECT kind, match_key, token, real_value FROM mask_values").fetchall()
        self.by_kind = {(kind, key): token for kind, key, token, _ in rows}
        self.by_key = {key: token for _, key, token, _ in rows}
        self.names = {token: real for _, _, token, real in rows}
        self.codes = {token for kind, _, token, _ in rows if kind == "employee_code"}
        self.added = 0
        self._pattern: re.Pattern[str] | None = None
        self._tokens: dict[str, str] = {}  # normalised real value -> token, for `text`

    def token(self, kind: str, value: str) -> str:
        """The token for `value`, made (and stored, as `kind`) the first time it is seen. A value
        already masked as another kind keeps its token (a ledger masked as `LEDG` in one year
        and found under Sundry Debtors in the next stays one ledger)."""
        key = norm(value)
        found = self.by_kind.get((kind, key)) or self.by_key.get(key)
        if found is not None:
            return found
        assert self.file_id is not None
        count = sum(1 for k, _ in self.by_kind if k == kind)
        token = f"{PREFIX[kind]}-{count + 1:04d}"
        real = " ".join(value.split())
        self.con.execute(
            "INSERT INTO mask_values (token, kind, real_value, match_key, first_file_id, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [token, kind, real, key, self.file_id, self.run_id],
        )
        self.by_kind[(kind, key)] = token
        self.by_key.setdefault(key, token)
        self.names[token] = real
        if kind == "employee_code":
            self.codes.add(token)
        self.added += 1
        self._pattern = None
        return token

    def known(self, value: str) -> str | None:
        """The token of a value that is a known masked name, else None."""
        return self.by_key.get(norm(value))

    def text(self, value: str | None) -> str | None:
        """Free text with every known masked name replaced by its token."""
        if not value or not self.names:
            return value
        exact = self.known(value)
        if exact is not None and exact not in self.codes:
            return exact
        if self._pattern is None:
            names = sorted(
                ((t, r) for t, r in self.names.items() if t not in self.codes),
                key=lambda item: -len(item[1]),
            )
            if not names:
                return value
            self._tokens = {norm(real): token for token, real in names}
            alternatives = "|".join(r"\s+".join(map(re.escape, real.split())) for _, real in names)
            self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
        return self._pattern.sub(lambda m: self._tokens[norm(m.group(0))], value)

    def remask(self) -> int:
        """Mask the known names in the free text already stored (`FREE_TEXT_COLUMNS`), when this
        load added names. Runs in the load's transaction, changing only values that differ.
        Returns the number of rows changed."""
        if self.added == 0:
            return 0
        changed = 0
        for table, column in FREE_TEXT_COLUMNS:
            distinct = self.con.execute(
                f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for (value,) in distinct:
                masked = self.text(value)
                if masked != value:
                    rows = self.con.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ? RETURNING 1",
                        [masked, value],
                    ).fetchall()
                    changed += len(rows)
        return changed
