"""`fin dashboard` (docs/product.md): Evidence dashboards over shared/analytics.duckdb.

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
from fin.paths import analytics_db_path, temp_name

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dashboards"
# The path the site is served under (`deployment.basePath` in dashboards/evidence.config.yaml), so
# the web UI can serve the build beside its own pages (`fin.ui.system_routes`).
BASE_PATH = "/dashboard-site"
DEV_URL = f"http://localhost:3000{BASE_PATH}"
# npm run dev exits 0, or with SIGINT (-2, or 130 through a shell), when the user stops it.
STOPPED_BY_CTRL_C = (0, -2, 130)


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
    partial = temp_name(snapshot)
    try:
        with connect(analytics_db_path(root)) as con:
            con.execute("ATTACH ? AS snap (READ_WRITE, STORAGE_VERSION 'v1.0.0')", [str(partial)])
            for table in TABLES:
                con.execute(f"CREATE TABLE snap.{table} AS SELECT * FROM main.{table}")
            con.execute("DETACH snap")
        partial.replace(snapshot)
    finally:
        partial.unlink(missing_ok=True)  # gone already when it replaced the snapshot
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
    try:
        return subprocess.run(
            [npm, "run", script, *extra],
            cwd=PROJECT_DIR,
            env=env,
            capture_output=script != "dev",
            text=True,
            check=False,
        )
    except OSError as exc:
        raise DependencyMissing(
            f"npm ({npm}) cannot be started: {exc}; run ./install.sh (installs node)"
        ) from exc


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
    try:
        result = _npm(snapshot, "dev", "--", "--port", "3000")
    except KeyboardInterrupt:
        return  # Ctrl-C is how the server is meant to stop
    if result.returncode not in STOPPED_BY_CTRL_C:
        raise DashboardBuildFailed(f"`npm run dev` failed (exit {result.returncode})")


def build(root: Path) -> Path:
    """Refresh the sources and build the static site. On success the site is copied to
    `<data>/dashboard/build` and that path is returned. On failure (E603) the previous copy is
    left untouched."""
    snapshot = _refresh_sources(root)
    result = _npm(snapshot, "build")
    built = PROJECT_DIR / "build"
    if (built / BASE_PATH.strip("/")).is_dir():  # Evidence may put the site under its base path
        built = built / BASE_PATH.strip("/")
    if result.returncode != 0 or not built.is_dir():
        raise DashboardBuildFailed(
            f"`npm run build` failed (exit {result.returncode}); "
            f"the last good build is kept in {last_good_build_dir(root)}:\n{_tail(result)}"
        )
    target = last_good_build_dir(root)
    swap_in(built, target)
    return target


def swap_in(built: Path, target: Path) -> None:
    """Copy `built` to `target`, never deleting the last good build before the new one is in
    place: the old one is renamed aside, the new one renamed in, and only then the old one removed.
    A failure puts the old one back (E603)."""
    staging, old = temp_name(target), temp_name(target)
    try:
        shutil.copytree(built, staging)
        if target.exists():
            target.replace(old)
        try:
            staging.replace(target)
        except OSError:
            if old.exists() and not target.exists():
                old.replace(target)
            raise
    except OSError as exc:
        raise DashboardBuildFailed(
            f"cannot put the new build in place at {target}: {exc}; the last good build is kept"
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)  # gone already when it became the build
        shutil.rmtree(old, ignore_errors=True)  # the previous build, once the new one is in
