import subprocess
from pathlib import Path
from typing import Any

import pytest

import fin.dashboard
from fin.audit.errors import DashboardBuildFailed, InvalidMapping
from fin.audit.logger import Run
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.map.review import propose, read_pending, save_pending
from fin.paths import company_db_path, dictionary_db_path

PENDING: Any = {
    "company": "bluepeak",
    "items": [
        {"id": 1, "label": "Trade receivables", "statement": "balance_sheet", "section": "assets"}
    ],
}
GOOD = {
    "label": "Trade receivables",
    "account": "trade_receivables",
    "confidence": 0.9,
    "reason": "x",
}


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with the seeded dictionary and bluepeak's pending item."""
    root = tmp_path_factory.mktemp("review")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "bluepeak"), "company")
    save_pending(root, "bluepeak", PENDING)
    return root


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", None),
        ("account", ["trade_receivables"]),
        ("reason", None),
        ("statement", ["balance_sheet"]),
        ("section", None),
    ],
)
def test_a_proposal_value_that_is_not_text_is_e304(root: Path, field: str, value: Any) -> None:
    with pytest.raises(InvalidMapping) as caught:
        propose(Run("map propose", {}, root), "bluepeak", [{**GOOD, field: value}])
    assert str(caught.value) == f"E304: proposal 1: {field!r} must be text"
    assert read_pending(root, "bluepeak")["items"] == PENDING["items"]  # nothing stored


def _npm_exits(monkeypatch: pytest.MonkeyPatch, code: int | None) -> None:
    """`npm run dev` ends with exit `code`, or is interrupted with Ctrl-C when `code` is None."""
    monkeypatch.setattr(fin.dashboard, "_refresh_sources", lambda root: Path("snapshot"))

    def dev(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if code is None:
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(fin.dashboard, "_npm", dev)


@pytest.mark.parametrize("code", [0, 130, -2, None])
def test_serve_stopped_with_ctrl_c_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int | None
) -> None:
    _npm_exits(monkeypatch, code)
    fin.dashboard.serve(tmp_path)


def test_serve_with_a_failing_dev_server_is_e603(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _npm_exits(monkeypatch, 1)
    with pytest.raises(DashboardBuildFailed) as caught:
        fin.dashboard.serve(tmp_path)
    assert str(caught.value) == "E603: `npm run dev` failed (exit 1)"
