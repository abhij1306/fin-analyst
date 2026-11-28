"""The conversations of the UI chat: one JSON file each under `<data folder>/chat/`.

A conversation keeps its Claude session id (`--resume` after `fin ui` restarts) and the items the
chat shows (what the user said, Claude's text, tool calls, permission requests, errors)."""

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fin.audit.errors import UiActionInvalid
from fin.paths import atomic_write

Conversation = dict[str, object]
TITLE_LENGTH = 48
# The reader thread of a turn and the routes use the same files: a change holds it across load,
# change and save, so two requests never save over each other.
LOCK = threading.RLock()


def chat_dir(root: Path) -> Path:
    return root / "chat"


def clean_id(conv_id: str) -> str:
    """The id as a canonical UUID (also the Claude session id); E008 for anything else."""
    try:
        return str(uuid.UUID(conv_id))
    except ValueError:
        raise UiActionInvalid(f"unknown conversation {conv_id!r}") from None


def _path(root: Path, conv_id: str) -> Path:
    return chat_dir(root) / f"{clean_id(conv_id)}.json"


def create(root: Path, company: str) -> Conversation:
    """A new, empty conversation; its id is also the Claude session id."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conv: Conversation = {
        "id": str(uuid.uuid4()),
        "title": "New chat",
        "company": company,
        "created": now,
        "updated": now,
        "started": False,  # the Claude session exists (the first turn began): resume, not create
        "items": [],
    }
    save(root, conv)
    return conv


def load(root: Path, conv_id: str) -> Conversation:
    """The conversation, or E008 when there is none with this id."""
    path = _path(root, conv_id)
    with LOCK:
        if not path.is_file():
            raise UiActionInvalid(f"unknown conversation {conv_id!r}")
        return json.loads(path.read_text(encoding="utf-8"))


def save(root: Path, conv: Conversation) -> None:
    path = _path(root, str(conv["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    conv["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    with LOCK:
        atomic_write(path, json.dumps(conv, indent=1))


def items(conv: Conversation) -> list[dict[str, object]]:
    return conv["items"]  # type: ignore[return-value]


def add_user_item(root: Path, conv: Conversation, text: str) -> None:
    """The user's message; the first one names the conversation."""
    if not items(conv):
        conv["title"] = " ".join(text.split())[:TITLE_LENGTH]
    items(conv).append({"kind": "user", "text": text})
    save(root, conv)


def listing(root: Path) -> list[Conversation]:
    """Every conversation, the most recently used first."""
    found = []
    with LOCK:
        for path in chat_dir(root).glob("*.json") if chat_dir(root).is_dir() else []:
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # a file that is not a conversation is not listed
    return sorted(found, key=lambda c: str(c.get("updated", "")), reverse=True)
