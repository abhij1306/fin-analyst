"""The rebuilt statements' error class (E822; SPEC-14, fin.rebuilt; the table is in
docs/errors.md). Re-exported by `fin.audit.errors`, so import it from there like every other
error. It stops the command: exit 2."""

from fin.audit.errors import FinError


class RebuildNotPossible(FinError):
    """E822. `fin statements --rebuilt`: the company has no engagement, the period is not one of
    its periods, or no period (or not the one asked for) has a loaded ledger-level trial balance
    (none loaded, or group totals only). The message says what to ask the company for. Nothing
    shown."""

    code = "E822"
