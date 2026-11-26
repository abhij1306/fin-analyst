"""The UI chat (docs/web-ui-plan.md, PR 4): a bridge to `claude -p` over stream-json and
server-sent events. Every test runs tests/fake_claude.py instead of Claude Code: no login, no
network. Needs the `ui` extra; skipped without it."""

import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from fin.ui import chat_args, chat_store  # noqa: E402
from fin.ui.chat_run import REPO, Chat  # noqa: E402
from fin.ui.server import create_app  # noqa: E402

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}
FAKE = [sys.executable, str(Path(__file__).parents[1] / "fake_claude.py")]


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The file the fake claude logs each start to."""
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return log


def started(calls: Path) -> list[dict]:
    return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]


def make_client(root: Path, claude: list[str] | None = FAKE) -> TestClient:
    app = create_app(root, TOKEN, PORT, claude)
    client = TestClient(app, base_url=f"http://{HOST}", follow_redirects=False)
    client.get(f"/?token={TOKEN}")
    return client


@pytest.fixture
def client(data_root: Path, calls: Path) -> TestClient:
    return make_client(data_root)


def chat_of(client: TestClient) -> Chat:
    return client.app.state.chat  # type: ignore[attr-defined]


def send(client: TestClient, text: str, conv: str = ""):
    data = {"conv": conv, "company": "acme", "text": text}
    return client.post("/chat/send", data=data, headers=ORIGIN)


def conv_id(root: Path) -> str:
    return str(chat_store.listing(root)[0]["id"])


def wait_idle(client: TestClient, conv: str) -> None:
    for _ in range(100):
        if not chat_of(client).running(conv):
            return
        time.sleep(0.1)
    raise AssertionError("the turn did not finish")


def run_turn(client: TestClient, root: Path, text: str, conv: str = "") -> str:
    """Send, read the whole event stream, and return the stream text."""
    page = send(client, text, conv)
    assert page.status_code == 200
    cid = conv or conv_id(root)
    streamed = client.get(f"/chat/stream?conv={cid}")
    wait_idle(client, cid)
    return streamed.text


def test_argv_denies_the_users_decisions_and_never_bypasses_permissions() -> None:
    argv = chat_args.turn_argv(["claude"], "sid", started=False)
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    for command in (
        "gate approve", "fdd finding final", "fdd finding draft", "query approve", "query sent",
        "query close", "query withdraw", "adjust accept", "adjust reject", "review confirm",
        "review reject", "findings accept",
    ):  # fmt: skip
        assert f"Bash(fin {command} *)" in denied
        assert f"Bash(uv run fin {command} *)" in denied
    assert "--bare" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--allow-dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert argv[argv.index("--output-format") :][:2] == ["--output-format", "stream-json"]
    assert "--include-partial-messages" in argv
    assert "--session-id" in argv and "--resume" not in argv
    assert "--resume" in chat_args.turn_argv(["claude"], "sid", started=True)
    assert (
        chat_args.turn_argv(["claude"], "sid", False, "Bash(ls)")[-1] == "--allowedTools=Bash(ls)"
    )


def test_a_turn_runs_in_the_repo_with_the_data_folder_and_the_denied_decisions(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    run_turn(client, data_root, "ask what is revenue")
    (call,) = started(calls)
    assert Path(call["cwd"]).resolve() == REPO.resolve()
    assert (REPO / "CLAUDE.md").is_file()
    assert call["data"] == str(data_root)
    assert "Bash(fin gate approve *)" in call["argv"][call["argv"].index("--disallowedTools") + 1]
    assert "--bare" not in call["argv"]
    assert call["prompt"] == "ask what is revenue"


def test_text_streams_as_deltas_and_the_stored_chat_is_shown_when_done(
    client: TestClient, data_root: Path
) -> None:
    page = send(client, "ask what is revenue")
    assert 'sse-connect="/chat/stream?conv=' in page.text
    assert "what is revenue" in page.text
    cid = conv_id(data_root)
    streamed = client.get(f"/chat/stream?conv={cid}").text
    assert "event: block" in streamed
    assert "event: delta" in streamed
    assert "event: done" in streamed
    assert 'id="chat"' in streamed  # done carries the whole panel
    assert "The answer is in the sheet." in streamed
    wait_idle(client, cid)
    items = chat_store.items(chat_store.load(data_root, cid))
    assert [i["kind"] for i in items] == ["user", "text"]
    assert items[1]["text"] == "The answer is in the sheet."


def test_tool_calls_are_shown_collapsed(client: TestClient, data_root: Path) -> None:
    streamed = run_turn(client, data_root, "tool please")
    assert "<details" in streamed and "fin status" in streamed
    panel = client.get("/chat/panel").text
    assert "<details" in panel and "Two companies are loaded." in panel


def test_the_conversation_list_and_resume_survive_a_restart(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    run_turn(client, data_root, "ask first")
    cid = conv_id(data_root)
    again = make_client(data_root)  # fin ui restarted: a new app on the same data folder
    panel = again.get("/chat/panel?company=acme").text
    assert "ask first" in panel
    assert f'value="{cid}"' in panel
    run_turn(again, data_root, "ask second", cid)
    first, second = started(calls)
    assert "--session-id" in first["argv"] and cid in first["argv"]
    assert "--resume" in second["argv"] and cid in second["argv"]
    assert [i["kind"] for i in chat_store.items(chat_store.load(data_root, cid))].count("user") == 2


def test_a_new_chat_is_a_separate_conversation(client: TestClient, data_root: Path) -> None:
    run_turn(client, data_root, "ask one")
    assert '<p class="chat-text">ask one' not in client.get("/chat/panel?new=1").text
    run_turn(client, data_root, "ask two")
    assert len(chat_store.listing(data_root)) == 2


def test_a_permission_request_is_allowed_once_for_that_command_only(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    streamed = run_turn(client, data_root, "denied fin ingest acme")
    assert "Allow once" in streamed and "Deny" in streamed
    cid = conv_id(data_root)
    item = next(
        i for i in chat_store.items(chat_store.load(data_root, cid)) if i["kind"] == "permission"
    )
    assert item["rule"] == "Bash(fin ingest acme)"
    answer = client.post("/chat/allow", data={"conv": cid, "item": item["id"]}, headers=ORIGIN)
    assert 'sse-connect="/chat/stream' in answer.text
    wait_idle(client, cid)
    second = started(calls)[1]
    assert second["argv"][-1] == "--allowedTools=Bash(fin ingest acme)"
    assert "--resume" in second["argv"]
    states = [
        i.get("state") for i in chat_store.items(chat_store.load(data_root, cid))
        if i["kind"] == "permission"
    ]  # fmt: skip
    assert states == ["allowed"]
    assert (
        client.post(
            "/chat/allow", data={"conv": cid, "item": item["id"]}, headers=ORIGIN
        ).text.count("already answered")
        == 1
    )  # E008 as an alert; nothing started
    assert len(started(calls)) == 2


def test_a_tampered_allow_rule_is_refused_and_starts_nothing(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    run_turn(client, data_root, "denied fin ingest acme")
    cid = conv_id(data_root)
    conv = chat_store.load(data_root, cid)
    item = next(i for i in chat_store.items(conv) if i["kind"] == "permission")
    item["rule"] = "--dangerously-skip-permissions"  # a conversation file edited by someone else
    chat_store.save(data_root, conv)
    answer = client.post("/chat/allow", data={"conv": cid, "item": item["id"]}, headers=ORIGIN)
    assert answer.status_code == 400 and "E008" in answer.text
    assert len(started(calls)) == 1


def test_deny_records_the_choice_and_starts_nothing(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    run_turn(client, data_root, "denied fin ingest acme")
    cid = conv_id(data_root)
    item = next(
        i for i in chat_store.items(chat_store.load(data_root, cid)) if i["kind"] == "permission"
    )
    panel = client.post("/chat/deny", data={"conv": cid, "item": item["id"]}, headers=ORIGIN)
    assert "Denied." in panel.text
    assert len(started(calls)) == 1


def test_a_decision_command_cannot_be_allowed_from_the_chat(
    client: TestClient, data_root: Path
) -> None:
    streamed = run_turn(client, data_root, "denied uv run fin gate approve acme G1")
    assert "Allow once" not in streamed
    assert "one of your decisions" in streamed


def test_stop_ends_the_running_process(client: TestClient, data_root: Path) -> None:
    send(client, "slow")
    cid = conv_id(data_root)
    for _ in range(50):  # wait until the process is running
        if chat_of(client).running(cid) and chat_of(client).turns[cid].process:
            break
        time.sleep(0.1)
    busy = send(client, "ask again", cid)  # one process per conversation
    assert "E011" in busy.text
    assert client.post("/chat/stop", data={"conv": cid}, headers=ORIGIN).status_code == 204
    wait_idle(client, cid)
    assert chat_of(client).turns[cid].process.poll() is not None
    items = chat_store.items(chat_store.load(data_root, cid))
    assert items[-1] == {"kind": "note", "text": "Stopped."}
    assert "ask again" not in client.get(f"/chat/panel?conv={cid}").text


def test_not_logged_in_is_e010(client: TestClient, data_root: Path) -> None:
    streamed = run_turn(client, data_root, "nologin")
    assert "E010" in streamed and "log in" in streamed
    cid = conv_id(data_root)
    assert chat_store.load(data_root, cid)["started"] is True


def test_a_failed_first_turn_is_not_resumed(
    client: TestClient, data_root: Path, calls: Path
) -> None:
    run_turn(client, data_root, "crash now")
    cid = conv_id(data_root)
    conv = chat_store.load(data_root, cid)
    assert conv["started"] is False  # claude never made a session: the next turn creates it
    run_turn(client, data_root, "ask again", cid)
    assert "--session-id" in started(calls)[1]["argv"]


def test_a_crash_shows_what_claude_said_as_e009(client: TestClient, data_root: Path) -> None:
    streamed = run_turn(client, data_root, "crash now")
    assert "E009" in streamed and "boom: cannot continue" in streamed


def test_a_missing_claude_is_e009_and_nothing_is_stored(
    data_root: Path, calls: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    client = make_client(data_root, claude=None)
    assert "E009" in client.get("/chat/panel").text  # said as soon as the panel opens
    page = send(client, "ask something")
    assert "E009" in page.text and "ask something" in page.text  # the text stays for a retry
    assert chat_store.listing(data_root) == []


def test_the_api_key_warning_shows_only_when_a_key_is_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "ANTHROPIC_API_KEY" not in client.get("/chat/panel").text
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert "ANTHROPIC_API_KEY is set" in client.get("/chat/panel").text


def test_the_panel_has_skill_chips_and_ask_buttons_prefill_it(
    client: TestClient, data_root: Path
) -> None:
    panel = client.get("/chat/panel?company=acme").text
    for skill in ("ask", "ingest", "diligence", "report", "status"):
        assert f"/{skill} acme " in panel
    prefilled = client.get("/chat/panel", params={"company": "acme", "prefill": "About <acme>: "})
    assert ">About &lt;acme&gt;: </textarea>" in prefilled.text
    (data_root / "inbox" / "acme").mkdir(parents=True)
    screen = client.get("/files?company=acme").text
    assert 'hx-get="/chat/panel"' in screen and "files screen: " in screen


def test_sending_needs_the_origin_and_a_message(client: TestClient) -> None:
    assert client.post("/chat/send", data={"text": "ask x"}).status_code == 403
    empty = send(client, "   ")
    assert empty.status_code == 400 and "E008" in empty.text
    assert client.get("/chat/panel?conv=not-a-uuid").status_code == 400


def test_stopping_the_server_ends_a_running_process(client: TestClient, data_root: Path) -> None:
    send(client, "slow")
    cid = conv_id(data_root)
    for _ in range(50):
        if chat_of(client).running(cid) and chat_of(client).turns[cid].process:
            break
        time.sleep(0.1)
    chat_of(client).stop_all()
    wait_idle(client, cid)
