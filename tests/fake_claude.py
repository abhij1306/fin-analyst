"""A stand-in for the `claude` executable, for the UI chat tests: it prints canned stream-json and
touches nothing else (no network, no login). The tests start it as `python fake_claude.py`.

What it does is chosen by the first word of the prompt on stdin:
  ask      streams text in deltas, then a result
  tool     a tool call, then text
  denied   text, then a result with a permission denial (a Bash command: the rest of the prompt)
  slow     prints one delta and waits (the Stop button ends it)
  nologin  "Not logged in" error result, exit 1
  crash    exits 3 with a message on stderr
Anything else is echoed back. Every start appends its argv and prompt to $FAKE_CLAUDE_LOG
(JSON lines).
"""

import json
import os
import sys
import time
from pathlib import Path


def out(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def stream(event: dict) -> None:
    out({"type": "stream_event", "event": event})


def say(text: str, parts: int = 2) -> None:
    stream({"type": "message_start"})
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
    size = max(1, len(text) // parts)
    for i in range(0, len(text), size):
        piece = {"type": "text_delta", "text": text[i : i + size]}
        stream({"type": "content_block_delta", "index": 0, "delta": piece})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def result(**extra: object) -> None:
    out({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
         "session_id": "fake", "permission_denials": [], **extra})  # fmt: skip


def main() -> int:
    prompt = sys.stdin.read()
    entry = {"argv": sys.argv[1:], "prompt": prompt, "cwd": str(Path.cwd()),
             "data": os.environ.get("FIN_DATA_DIR")}  # fmt: skip
    with Path(os.environ["FAKE_CLAUDE_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")
    mode, _, rest = prompt.partition(" ")
    if mode == "crash":  # dies before it makes a session
        sys.stderr.write("boom: cannot continue\n")
        return 3
    out({"type": "system", "subtype": "init", "session_id": "fake"})
    if mode == "nologin":
        out({"type": "assistant", "error": "authentication_failed",
             "message": {"content": [{"type": "text", "text": "Not logged in"}]}})  # fmt: skip
        out({"type": "result", "subtype": "success", "is_error": True,
             "result": "Not logged in - Please run /login"})  # fmt: skip
        return 1
    if mode == "tool":
        call = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "fin status"}}
        out({"type": "assistant", "message": {"content": [call]}})
        say("Two companies are loaded.")
    elif mode == "denied":
        denial = {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": rest.strip()}}
        say("I need to run a command.")
        result(permission_denials=[denial])
        return 0
    elif mode == "slow":
        stream({"type": "message_start"})
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
        piece = {"type": "text_delta", "text": "working"}
        stream({"type": "content_block_delta", "index": 0, "delta": piece})
        time.sleep(60)
    elif mode == "ask":
        say("The answer is in the sheet.")
    else:
        say("echo: " + prompt)
    result()
    return 0


if __name__ == "__main__":
    sys.exit(main())
