"""`fin update` (PLAN.md §10): bring the code up to date without losing data.

1. Refuse if the repo has local changes.
2. Back up every database to `backups/<run_id>/`.
3. `git pull --ff-only`, `uv sync --frozen`, and `npm ci` (only if `dashboards/package-lock.json`
   exists).
4. `fin migrate`, then `fin doctor`, run as new processes so they use the freshly pulled code.

Each step is logged as an event. A failing step stops the update with E005.
"""

import shutil
import subprocess
from pathlib import Path

from fin.audit.errors import EXIT_FAILURES, UpdateFailed
from fin.audit.logger import Run
from fin.db.migrate import targets
from fin.query.write import backup

REPO = Path(__file__).resolve().parents[2]

# Module constants so tests can swap the slow steps for harmless ones.
GIT = "git"
UV_SYNC = ["uv", "sync", "--frozen"]
NPM_CI = ["npm", "ci", "--prefix", "dashboards"]
FIN = ["uv", "run", "--frozen", "fin"]


def _run(
    run: Run, step: str, command: list[str], cwd: Path, *, must_succeed: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run one command and log it. Raises E005 if it cannot start, or exits non-zero and
    `must_succeed`."""
    program = shutil.which(command[0])
    if program is None:
        raise UpdateFailed(f"{step}: {command[0]} not found; run `uv run fin doctor`")
    result = subprocess.run(
        [program, *command[1:]], cwd=cwd, capture_output=True, text=True, check=False
    )
    output = (result.stdout + result.stderr).strip()[-500:]
    if must_succeed and result.returncode != 0:
        raise UpdateFailed(f"{step} failed (exit {result.returncode}): {output}")
    level = "info" if result.returncode == 0 else "error"
    run.event(
        "update", f"{step}: exit {result.returncode}", level=level, context={"output": output}
    )
    return result


def update(run: Run) -> None:
    """Run the update steps in order. Sets exit code 1 if `fin doctor` reports problems."""
    repo = REPO
    changes = _run(run, "git status", [GIT, "status", "--porcelain"], repo).stdout.strip()
    if changes:
        raise UpdateFailed(f"the repo has local changes; commit or discard them first:\n{changes}")

    saved = [backup(run.root, t.path, run) for t in targets(run.root) if t.path.is_file()]
    run.event("update", f"backed up {len(saved)} databases to backups/{run.run_id}/")

    _run(run, "git pull", [GIT, "pull", "--ff-only"], repo)
    _run(run, "uv sync", UV_SYNC, repo)
    if (repo / "dashboards" / "package-lock.json").is_file():
        _run(run, "npm ci", NPM_CI, repo)

    _run(run, "fin migrate", [*FIN, "migrate"], repo)
    doctor = _run(run, "fin doctor", [*FIN, "doctor"], repo, must_succeed=False)
    if doctor.returncode != 0:
        run.failed += 1
        run.exit_code = EXIT_FAILURES
