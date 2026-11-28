"""Audit fixes in the UI and its guards: Allow once is never offered for a fin command with a
shell expansion, the gate guard asks for one, the chat stores nothing for a turn that did not
start, the dashboard keeps the last good build when the swap fails, and a job a dead worker left
running is failed while the queue goes on."""

import importlib.util
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fin.audit.errors import ChatBusy, ChatUnavailable, DashboardBuildFailed, FinError
from fin.dashboard import swap_in
from fin.db.conn import connect, utcnow
from fin.paths import audit_db_path
from fin.services.jobs import LEFT_RUNNING, Jobs, list_jobs
from fin.services.result import ActionResult, Done, Progress
from fin.ui import chat_args, chat_store
from tests.engagement_root import engagement_root

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "gate_guard.py"


def gate_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_guard", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPANDED = [
    "a=approve; uv run fin gate $a acme G2",
    "uv run fin gate `echo approve` acme G2",
    "uv run fin gate status acme && uv run fin gate approve acme G2",
    "uv run fin status acme | tee out.txt",
    "uv run fin fdd finding $(printf final) acme 3",
]


@pytest.mark.parametrize("command", EXPANDED)
def test_a_fin_command_with_a_shell_expansion_is_never_allowed_once(command: str) -> None:
    assert chat_args.is_decision(command)
    assert chat_args.once_rule("Bash", {"command": command}) is None
    with pytest.raises(FinError):
        chat_args.checked_rule(f"Bash({command})")


def test_a_plain_fin_command_can_still_be_allowed_once() -> None:
    command = "uv run fin status acme"
    assert not chat_args.is_decision(command)
    assert chat_args.once_rule("Bash", {"command": command}) == f"Bash({command})"


@pytest.mark.parametrize(
    "command",
    [
        "a=approve; uv run fin gate $a acme G2",
        "uv run fin fdd finding `echo final` acme 3",
        "uv run python -c 'from fin.services.gates import approve_gate'",
        "uv run python -c 'from fin.services.fdd import mark_fdd_findings'",
    ],
)
def test_the_gate_guard_asks_for_hidden_approvals(command: str) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    output = gate_guard().decision(payload)
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_the_gate_guard_lets_a_plain_status_pass() -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "uv run fin gate status acme"}}
    assert gate_guard().decision(payload) is None


class Templates:
    def get_template(self, name: str) -> Any:
        class Page:
            def render(self, context: dict[str, Any]) -> str:
                return name

        return Page()


class FakeChat:
    def __init__(self, error: FinError | None) -> None:
        self.error, self.started = error, 0

    def locate(self) -> list[str]:
        return ["claude"]

    def running(self, conv_id: str) -> bool:
        return False

    def start(self, conv: Any, text: str, once: str | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.started += 1


def pending_request(root: Path) -> str:
    conv = chat_store.create(root, "acme")
    chat_store.items(conv).append(
        {
            "kind": "permission",
            "id": "p1",
            "state": "pending",
            "tool": "Bash",
            "summary": "uv run fin status acme",
            "rule": "Bash(uv run fin status acme)",
        }
    )
    chat_store.save(root, conv)
    return str(conv["id"])


@pytest.mark.parametrize(
    "error", [ChatBusy("still working"), ChatUnavailable("cannot start claude")]
)
def test_allow_once_stores_nothing_when_the_turn_does_not_start(
    tmp_path: Path, error: FinError
) -> None:
    pytest.importorskip("fastapi")
    from fin.ui.chat_routes import ChatRoutes

    conv = pending_request(tmp_path)
    routes = ChatRoutes(tmp_path, Templates(), FakeChat(error))  # type: ignore[arg-type]

    routes.allow(conv=conv, item="p1")

    stored = chat_store.items(chat_store.load(tmp_path, conv))
    assert [(i["kind"], i.get("state")) for i in stored] == [("permission", "pending")]


def test_allow_once_stores_the_answer_and_the_message_once_the_turn_started(
    tmp_path: Path,
) -> None:
    pytest.importorskip("fastapi")
    from fin.ui.chat_routes import ChatRoutes

    conv = pending_request(tmp_path)
    chat = FakeChat(None)

    ChatRoutes(tmp_path, Templates(), chat).allow(conv=conv, item="p1")  # type: ignore[arg-type]

    stored = chat_store.items(chat_store.load(tmp_path, conv))
    assert chat.started == 1
    assert [(i["kind"], i.get("state")) for i in stored] == [
        ("permission", "allowed"),
        ("user", None),
    ]


def make_build(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(text, encoding="utf-8")
    return folder


def test_the_dashboard_swap_puts_the_new_build_in_place(tmp_path: Path) -> None:
    target = make_build(tmp_path / "dashboard" / "build", "old")
    swap_in(make_build(tmp_path / "out", "new"), target)
    assert (target / "index.html").read_text(encoding="utf-8") == "new"
    assert [p.name for p in target.parent.iterdir()] == ["build"]


def test_a_failed_dashboard_swap_keeps_the_last_good_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = make_build(tmp_path / "dashboard" / "build", "old")
    built = make_build(tmp_path / "out", "new")
    original, failed = Path.replace, []

    def replace(self: Path, other: Any) -> Path:
        if Path(other) == target and not failed:  # the new build cannot be renamed in
            failed.append(self)
            raise OSError("the folder is in use")
        return original(self, other)

    monkeypatch.setattr(Path, "replace", replace)

    with pytest.raises(DashboardBuildFailed) as refused:
        swap_in(built, target)

    assert refused.value.code == "E603"
    assert failed
    assert (target / "index.html").read_text(encoding="utf-8") == "old"
    assert [p.name for p in target.parent.iterdir()] == ["build"]


def wait_for(root: Path, done: Any, seconds: float = 60) -> list[dict[str, Any]]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        jobs = list_jobs(root, "acme")
        if done(jobs):
            return jobs
        time.sleep(0.2)
    raise AssertionError(f"jobs did not get there: {list_jobs(root, 'acme')}")


def fine(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return Done(["ok"], "run-ok")


def test_a_job_a_dead_worker_left_running_is_failed_by_the_next_job(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    with connect(audit_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO jobs (company, action, arguments, state, queued_at, started_at) "
            "VALUES ('acme', 'fine', '{}', 'running', ?, ?)",
            [utcnow(), utcnow()],
        )

    Jobs(root, {"fine": fine}).submit("acme", "fine", {})

    jobs = wait_for(root, lambda js: js[0]["state"] == "done")
    assert [(j["state"], j["lines"]) for j in reversed(jobs)] == [
        ("failed", [LEFT_RUNNING]),
        ("done", ["ok"]),
    ]


def test_the_queue_goes_on_when_recording_a_failure_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = engagement_root(tmp_path)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the defect ends its worker
    original = Jobs._update

    def update(self: Jobs, job_id: int, **fields: Any) -> None:
        if fields.get("state") == "failed":
            raise OSError("the audit database is locked")
        original(self, job_id, **fields)

    monkeypatch.setattr(Jobs, "_update", update)

    def boom(root: Path, company: str, *, progress: Progress) -> ActionResult:
        raise RuntimeError("a defect")

    jobs = Jobs(root, {"boom": boom, "fine": fine})
    jobs.submit("acme", "boom", {})
    jobs.submit("acme", "fine", {})

    finished = wait_for(root, lambda js: len(js) == 2 and js[0]["state"] == "done")
    assert [(j["action"], j["state"]) for j in reversed(finished)] == [
        ("boom", "failed"),
        ("fine", "done"),
    ]
