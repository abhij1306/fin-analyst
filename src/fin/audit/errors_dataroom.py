"""Data-room intake's error classes (E814, E815, E819; SPEC-13, fin.dataroom; the table is in
docs/errors.md). They are re-exported by `fin.audit.errors`, so import them from there like every
other error. Each stops the command: exit 2."""

from fin.audit.errors import FinError


class DataroomFolderInvalid(FinError):
    """E814. `fin dataroom scan`: the folder does not exist, is not a folder, or holds the data
    folder (fin would scan its own files). Nothing written."""

    code = "E814"


class DataroomPlanMissing(FinError):
    """E815. `fin dataroom plan`: the company has no data-room plan; run `fin dataroom scan`
    first. Nothing shown."""

    code = "E815"


class DataroomRowInvalid(FinError):
    """E819. `fin dataroom approve|propose`: no plan row with that id, or a row that cannot take
    it: an archive, a loaded file, a file fin cannot use with no proposed kind (approve), or a kind,
    profile or empty reason that is not valid, or a reference databook (propose). Nothing
    changed."""

    code = "E819"
