"""Error classes with codes from PLAN.md §9, and process exit codes.

Phase 0 defines only the codes it raises; Phase 1 adds the rest.
"""

# Exit codes (PLAN.md §9)
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_COMMAND_ERROR = 2
EXIT_BLOCKED = 3  # the CLI refused: the action needs the user's confirmation


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
    """E701: action needs user confirmation (guard). Raised when the CLI refuses without it."""

    code = "E701"
    exit_code = EXIT_BLOCKED
