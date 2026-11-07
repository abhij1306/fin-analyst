"""`fin dashboard` (PLAN.md Phase 10): Evidence dashboards over shared/analytics.duckdb.

Evidence reads its DuckDB source at `npm run sources` time. So that it never holds the live
analytics file (an ingest rewrites it), `snapshot_analytics` first copies the analytics tables into
`<data>/dashboard/analytics.duckdb` with one brief read-only open, and Evidence is pointed at that
copy through EVIDENCE_SOURCE__analytics__filename. Nothing outside this module touches the live
file for the dashboard, so an ingest never waits for it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import typer

from fin.audit.errors import DashboardBuildFailed, DependencyMissing
from fin.db.conn import connect
from fin.metrics.export import TABLES
from fin.paths import analytics_db_path

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dashboards"
DEV_URL = "http://localhost:3000"


def dashboard_dir(root: Path) -> Path:
    return root / "dashboard"


def last_good_build_dir(root: Path) -> Path:
    return dashboard_dir(root) / "build"


def snapshot_analytics(root: Path) -> Path:
    """Copy the analytics tables to a private file and return its path. The live file is open
    read-only only while the rows are copied."""
    folder = dashboard_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "analytics.duckdb"
    partial = folder / "analytics.duckdb.partial"
    partial.unlink(missing_ok=True)
    with connect(analytics_db_path(root)) as con:
        con.execute("ATTACH ? AS snap (READ_WRITE, STORAGE_VERSION 'v1.0.0')", [str(partial)])
        for table in TABLES:
            con.execute(f"CREATE TABLE snap.{table} AS SELECT * FROM main.{table}")
        con.execute("DETACH snap")
    partial.replace(snapshot)
    return snapshot


def _npm(snapshot: Path, script: str, *extra: str) -> subprocess.CompletedProcess[str]:
    npm = shutil.which("npm")
    if npm is None:
        raise DependencyMissing("npm is not installed; run ./install.sh (installs node)")
    if not (PROJECT_DIR / "node_modules").is_dir():
        raise DependencyMissing("dashboard packages are missing; run `npm ci --prefix dashboards`")
    # Evidence resolves the filename relative to the source folder.
    filename = os.path.relpath(snapshot, PROJECT_DIR / "sources" / "analytics")
    env = {**os.environ, "EVIDENCE_SOURCE__analytics__filename": filename}
    return subprocess.run(
        [npm, "run", script, *extra],
        cwd=PROJECT_DIR,
        env=env,
        capture_output=script != "dev",
        text=True,
        check=False,
    )


def _refresh_sources(root: Path) -> Path:
    snapshot = snapshot_analytics(root)
    result = _npm(snapshot, "sources")
    if result.returncode != 0:
        raise DashboardBuildFailed(
            f"`npm run sources` failed (exit {result.returncode}):\n{_tail(result)}"
        )
    return snapshot


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])


def serve(root: Path) -> None:
    """Refresh the sources, then run the dev server until it is stopped (Ctrl-C)."""
    snapshot = _refresh_sources(root)
    typer.echo(f"dashboard: {DEV_URL}  (Ctrl-C to stop; run `fin dashboard` again to refresh data)")
    _npm(snapshot, "dev", "--", "--port", "3000")


def build(root: Path) -> Path:
    """Refresh the sources and build the static site. On success the site is copied to
    `<data>/dashboard/build` and that path is returned. On failure (E603) the previous copy is
    left untouched."""
    snapshot = _refresh_sources(root)
    result = _npm(snapshot, "build")
    built = PROJECT_DIR / "build"
    if result.returncode != 0 or not built.is_dir():
        raise DashboardBuildFailed(
            f"`npm run build` failed (exit {result.returncode}); "
            f"the last good build is kept in {last_good_build_dir(root)}:\n{_tail(result)}"
        )
    target = last_good_build_dir(root)
    staging = target.with_name("build.new")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(built, staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target
