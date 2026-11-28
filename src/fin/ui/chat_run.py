"""One Claude turn of the UI chat: `claude -p` with stream-json output, read line by line.

One process per conversation at a time. Each turn is a process (`--session-id` the first time,
`--resume` after), so the conversation survives a restart of `fin ui`. The reader thread turns the
stream into events for the page (text deltas, finished blocks, done) and into the items stored in
the conversation. Tests run a fake `claude` (tests/fake_claude.py); nothing here needs a network."""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from fin.audit.errors import ChatBusy, ChatNotLoggedIn, ChatUnavailable
from fin.ui import chat_args, chat_store
from fin.ui.setup import HERE

REPO = HERE.parents[2]  # the repo folder: CLAUDE.md, skills and hooks are found from here
LOGIN_HINTS = ("not logged in", "/login", "authentication_failed", "invalid api key")
STDERR_TAIL = 600
SUMMARY_LENGTH = 90

Item = dict[str, Any]  # a stream-json message or a stored chat item, as parsed


def summary_of(tool: str, tool_input: dict[str, object]) -> str:
    """A short line for a tool call: its command, path, pattern or url."""
    for key in ("command", "file_path", "path", "pattern", "url", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return " ".join(value.split())[:SUMMARY_LENGTH]
    return ""


class Turn:
    """What one running (or just finished) turn has produced, for readers of the stream."""

    def __init__(self) -> None:
        self.events: list[Item] = []
        self.finished = False
        self.stopped = False
        self.process: subprocess.Popen[str] | None = None
        self.cond = threading.Condition()

    def emit(self, event: Item) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()


class Reader:
    """Turns the lines of one process's stdout into events and stored items."""

    def __init__(self, turn: Turn) -> None:
        self.turn = turn
        self.items: list[Item] = []
        self.streamed = False  # this message's text came as deltas: do not show it twice
        self.result: Item | None = None
        self.login_failed = False
        self.session = False  # claude made the session (init or result seen): later turns resume it

    def block(self, item: Item) -> None:
        self.items.append(item)
        self.turn.emit({"name": "block", "item": item})

    def line(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return  # not a stream message (a stray log line)
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind in ("system", "result"):
            self.session = True
        if kind == "stream_event":
            self.partial(message.get("event") or {})
        elif kind == "assistant":
            self.assistant(message)
        elif kind == "result":
            self.result = message

    def partial(self, event: Item) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self.streamed = False
        elif (
            kind == "content_block_start"
            and (event.get("content_block") or {}).get("type") == "text"
        ):
            self.streamed = True
            self.turn.emit({"name": "block", "item": {"kind": "text", "text": ""}})
        elif (
            kind == "content_block_delta" and (event.get("delta") or {}).get("type") == "text_delta"
        ):
            self.turn.emit({"name": "delta", "text": event["delta"].get("text", "")})

    def assistant(self, message: Item) -> None:
        if message.get("error") == "authentication_failed":
            self.login_failed = True
        for part in (message.get("message") or {}).get("content") or []:
            if part.get("type") == "text" and part.get("text"):
                item: Item = {"kind": "text", "text": part["text"]}
                self.items.append(item)
                if not self.streamed:
                    self.turn.emit({"name": "block", "item": item})
            elif part.get("type") == "tool_use":
                tool_input = part.get("input") or {}
                self.block(
                    {
                        "kind": "tool",
                        "name": part.get("name", "tool"),
                        "summary": summary_of(part.get("name", ""), tool_input),
                        "input": json.dumps(tool_input, indent=1),
                    }
                )

    def denials(self) -> None:
        """Each permission denial of the result is shown with Allow once / Deny; a decision command
        is only explained (it is a button on a screen)."""
        for denial in (self.result or {}).get("permission_denials") or []:
            tool, tool_input = denial.get("tool_name", "tool"), denial.get("tool_input") or {}
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            decision = (
                tool == "Bash" and isinstance(command, str) and chat_args.is_decision(command)
            )
            rule = None if decision else chat_args.once_rule(tool, tool_input)
            self.block(
                {
                    "kind": "permission",
                    "id": uuid.uuid4().hex[:8],
                    "tool": tool,
                    "summary": summary_of(tool, tool_input),
                    "rule": rule,
                    "decision": decision,
                    "state": "pending" if rule else "refused",
                }
            )

    def finish(self, code: int | None, stderr: str) -> None:
        """The end of the stream: permission requests, then an error if the turn failed."""
        self.denials()
        said = str((self.result or {}).get("result") or "")
        failed = self.result is None or bool((self.result or {}).get("is_error"))
        if self.turn.stopped:
            self.block({"kind": "note", "text": "Stopped."})
        elif failed:
            text = said or stderr.strip()[-STDERR_TAIL:] or f"claude ended with exit code {code}"
            if self.login_failed or any(h in text.lower() for h in LOGIN_HINTS):
                error = ChatNotLoggedIn(
                    "Claude Code is not logged in: run `claude` in a terminal and log in (/login)"
                )
            else:
                error = ChatUnavailable(text)
            self.block({"kind": "error", "code": error.code, "message": error.message})


class Chat:
    """The running turns of this server, one per conversation."""

    def __init__(self, root: Path, command: list[str] | None = None) -> None:
        self.root = root
        self.command = command  # None: look for `claude` when a turn starts
        self.turns: dict[str, Turn] = {}
        self.lock = threading.Lock()

    def locate(self) -> list[str]:
        return self.command or chat_args.locate()

    def running(self, conv_id: str) -> Turn | None:
        turn = self.turns.get(conv_id)
        return turn if turn and not turn.finished else None

    def start(self, conv: chat_store.Conversation, text: str, once: str | None = None) -> Turn:
        """Start a turn. E011 when one is running; E009 when `claude` cannot start."""
        conv_id = chat_store.clean_id(str(conv["id"]))  # never an option, whatever the file holds
        argv = chat_args.turn_argv(self.locate(), conv_id, bool(conv["started"]), once)
        with self.lock:
            if self.running(conv_id):
                raise ChatBusy("this conversation is still working: wait for it or press Stop")
            env = {**os.environ, "FIN_DATA_DIR": str(self.root)}
            try:
                # Sonar S6350 reads the Allow once rule from the conversation file as tainted:
                # turn_argv passes it only through chat_args.checked_rule (the exact shape
                # once_rule makes, never a decision) and as one --allowedTools= argument, so it
                # cannot add an option.
                process = subprocess.Popen(  # NOSONAR
                    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", cwd=REPO, env=env,
                )  # fmt: skip
            except OSError as error:
                raise ChatUnavailable(f"cannot start claude: {error}") from error
            turn = Turn()
            turn.process = process
            self.turns[conv_id] = turn
        threading.Thread(target=self.read, args=(conv_id, turn, text), daemon=True).start()
        return turn

    def read(self, conv_id: str, turn: Turn, text: str) -> None:
        process = turn.process
        assert process is not None and process.stdin and process.stdout and process.stderr
        errors: list[str] = []
        drain = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)  # type: ignore[union-attr]
        drain.start()
        reader = Reader(turn)
        try:
            try:
                process.stdin.write(text)
                process.stdin.close()
            except OSError:
                pass  # claude ended before reading: its exit is reported below
            for raw in process.stdout:
                reader.line(raw)
            code = process.wait()
            drain.join(timeout=2)
            reader.finish(code, "".join(errors))
            with chat_store.LOCK:  # one change: load, add the turn's items, save
                conv = chat_store.load(self.root, conv_id)
                chat_store.items(conv).extend(reader.items)
                conv["started"] = bool(conv["started"]) or reader.session
                chat_store.save(self.root, conv)
        finally:
            with turn.cond:
                turn.finished = True
                turn.events.append({"name": "done"})
                turn.cond.notify_all()

    def stop(self, conv_id: str) -> None:
        """End the running turn of the conversation (Stop button): a no-op when none runs."""
        turn = self.running(conv_id)
        if turn and turn.process:
            turn.stopped = True
            turn.process.terminate()
            try:
                turn.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                turn.process.kill()

    def stop_all(self) -> None:
        for conv_id in self.turns.copy():
            self.stop(conv_id)
