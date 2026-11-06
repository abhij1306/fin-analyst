"""PostToolUse hook: append every Claude Code tool call to <data>/logs/claude_actions.jsonl.

Reads the hook payload (JSON) from stdin. See PLAN.md §8.
"""

import json
import sys
from datetime import UTC, datetime
from typing import Any

from fin.paths import logs_dir

LOG_FILE_NAME = "claude_actions.jsonl"
MAX_FIELD_CHARS = 2000


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"... [truncated, {len(value)} chars]"
    if isinstance(value, dict):
        return {key: _truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate(item) for item in value]
    return value


def build_record(payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if payload.get("tool_name") == "Bash" else tool_input
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": payload.get("session_id"),
        "event": payload.get("hook_event_name"),
        "tool": payload.get("tool_name"),
        "cwd": payload.get("cwd"),
        "command": _truncate(command),
        **extra,
    }


def append_action(record: dict[str, Any]) -> None:
    folder = logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / LOG_FILE_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"log_action: hook input is not JSON: {exc}\n")
        return 1
    try:
        append_action(build_record(payload))
    except OSError as exc:
        sys.stderr.write(f"log_action: cannot write {LOG_FILE_NAME}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
