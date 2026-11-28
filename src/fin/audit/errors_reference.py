"""Reference documents' error classes (E980-E983; SPEC-10, fin.reference; the table is in
docs/errors.md). They are re-exported by `fin.audit.errors`, so import them from there like every
other error. Each stops the command: exit 2."""

from fin.audit.errors import FinError


class ReferenceInvalid(FinError):
    """E980. `fin reference add`: no source name. Nothing loaded."""

    code = "E980"


class ReferenceLineInvalid(FinError):
    """E981. `fin reference propose|confirm|reject`: a proposal that is not an object with line,
    account or metric, confidence and reason; an unknown line id; an account of another statement
    or per share; a metric not in rupees; or a line not waiting for that decision. Nothing
    changed."""

    code = "E981"


class ReferenceDifferenceUnknown(FinError):
    """E982. `fin reference query|adjust`: no reference figure with that id, or it does not differ
    from fin's (it agrees, fin has no figure, or its line is not mapped). Nothing raised or
    proposed."""

    code = "E982"


class ReferenceNotQueryable(FinError):
    """E983. `fin reference query`: the difference is below the engagement's query threshold, or
    the figure is already in a query of the engagement that is not withdrawn. Nothing raised."""

    code = "E983"
