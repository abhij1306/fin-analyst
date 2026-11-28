"""The forensic layer's error classes (E990-E993; SPEC-18, fin.forensic; the table is in
docs/errors.md). They are re-exported by `fin.audit.errors`, so import them from there like every
other error. Each stops the command: exit 2."""

from fin.audit.errors import FinError


class ForensicSettingInvalid(FinError):
    """E990. `fin forensic set` (or the Settings screen): an unknown setting, a value of the wrong
    kind or out of range, or a config.toml [forensic] table this cannot read or edit. Nothing
    changed."""

    code = "E990"


class ForensicTestUnknown(FinError):
    """E991. `fin forensic run|flags`: a test that is not registered, or a period not in the open
    engagement. Nothing run."""

    code = "E991"


class ForensicFlagUnknown(FinError):
    """E992. `fin query raise --flags`: a flag that does not exist, belongs to another engagement,
    is not in the latest run of its test and period, or whose run is stale (the data or a setting
    changed since). Nothing raised."""

    code = "E992"


class ForensicFlagNotQueryable(FinError):
    """E993. `fin query raise --flags`: a flag below the engagement's query threshold, one already
    in a query that is not withdrawn, or flags of several periods. Nothing raised."""

    code = "E993"
