"""Error classes with codes from PLAN.md §9, and process exit codes.

Phase 0 defines only the codes it raises; Phase 1 adds the rest.
"""

# Exit codes (PLAN.md §9)
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_COMMAND_ERROR = 2
EXIT_BLOCKED = 3


class FinError(Exception):
    """Base error. Every failure path raises a subclass carrying a §9 code."""

    code: str = ""
    exit_code: int = EXIT_COMMAND_ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class DependencyMissing(FinError):
    code = "E001"


class DependencyVersionMismatch(FinError):
    code = "E002"


class DataFolderNotWritable(FinError):
    code = "E003"


class GuardBlocked(FinError):
    code = "E701"
    exit_code = EXIT_BLOCKED
