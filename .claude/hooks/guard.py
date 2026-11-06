"""PreToolUse guard: block Claude Code actions that bypass `fin` (PLAN.md §8).

Blocks, with error E701:
- opening any .duckdb file in the data folder except through `fin`
- rm, mv (also rmdir, unlink, find -delete), redirects or tee writing into the data folder
- `fin delete` and `fin review confirm` (human-only)

Exit code 2 tells Claude Code to block the call; stderr is shown to Claude.
Fails closed: input that cannot be parsed is blocked.
"""

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from log_action import append_action, build_record

from fin.paths import data_dir

CODE = "E701"
BLOCK_EXIT = 2

FILE_TOOLS = {
    "Read": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
DELETERS = {"rm", "mv", "rmdir", "unlink"}
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nohup", "nice", "xargs"}
SHELLS = {"bash", "sh", "zsh", "dash"}
UV_VALUE_OPTIONS = {
    "--project",
    "--directory",
    "--with",
    "--python",
    "-p",
    "--env-file",
    "--package",
    "--group",
    "--extra",
    "--index",
    "--with-requirements",
}
DUCKDB_PATH = re.compile(r"[^\s'\"(),=;]+\.duckdb(?:\.wal)?\b")


def _key(path: Path) -> str:
    text = str(path)
    return text.casefold() if sys.platform in ("darwin", "win32") else text


def _inside(path: Path, root: Path) -> bool:
    key, root_key = _key(path), _key(root)
    return key == root_key or key.startswith(root_key.rstrip("/\\") + os.sep)


def _resolve(token: str, cwd: Path) -> Path:
    path = Path(os.path.expandvars(token)).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _in_data(token: str, cwd: Path, root: Path) -> bool:
    candidates = [token]
    if "=" in token:
        candidates.append(token.split("=", 1)[1])
    return any(c and _inside(_resolve(c, cwd), root) for c in candidates)


def _simple_commands(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    for token in lexer:
        if token in SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop env assignments and wrappers (sudo, uv run, python -m) to find the real command."""
    rest = list(words)
    while rest:
        head = Path(rest[0]).name
        if "=" in rest[0] and not rest[0].startswith("-"):
            rest = rest[1:]
        elif head in WRAPPERS:
            rest = rest[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[1:]
        elif head in ("uv", "uvx"):
            rest = rest[2:] if head == "uv" and rest[1:2] == ["run"] else rest[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in UV_VALUE_OPTIONS else rest[1:]
        elif head.startswith("python") and rest[1:3] == ["-m", "fin"]:
            return ["fin", *rest[3:]]
        else:
            return rest
    return rest


def _is_fin(words: list[str]) -> bool:
    return bool(words) and Path(words[0]).name in ("fin", "fin.exe")


def _paths_in_data(args: list[str], cwd: Path, root: Path) -> bool:
    return any(_in_data(arg, cwd, root) for arg in args if not arg.startswith("-"))


def check_bash(command: str, cwd: Path, root: Path) -> str | None:
    try:
        simple = _simple_commands(command)
    except ValueError as exc:
        return f"could not parse command ({exc})"
    for words in simple:
        rest = _strip_wrappers(words)
        if not rest:
            continue
        name = Path(rest[0]).name
        args = rest[1:]

        if name == "cd" and args:
            cwd = _resolve(args[0], cwd)
            continue

        if _is_fin(rest):
            sub = [word for word in args if not word.startswith("-")]
            for human_only in (["delete"], ["review", "confirm"]):
                if sub[: len(human_only)] == human_only:
                    command_name = " ".join(human_only)
                    return f"`fin {command_name}` is human-only; ask the user to run it"

        if name in DELETERS and _paths_in_data(args, cwd, root):
            return f"`{name}` on the data folder ({root}) is not allowed"
        if name == "find" and "-delete" in args and _paths_in_data(args, cwd, root):
            return f"`find -delete` on the data folder ({root}) is not allowed"
        if name == "tee" and _paths_in_data(args, cwd, root):
            return f"writing into the data folder ({root}) with tee is not allowed"
        for index, token in enumerate(words[:-1]):
            if token in REDIRECTS and _in_data(words[index + 1], cwd, root):
                return f"redirecting output into the data folder ({root}) is not allowed"

        if not _is_fin(rest):
            for token in words:
                for match in DUCKDB_PATH.findall(token):
                    if _in_data(match, cwd, root):
                        return f"{match} is a database in the data folder; use `fin` commands"

        if name in SHELLS and "-c" in args[:-1]:
            reason = check_bash(args[args.index("-c") + 1], cwd, root)
            if reason:
                return reason
        if name == "eval" and args:
            reason = check_bash(" ".join(args), cwd, root)
            if reason:
                return reason
    return None


def check(tool_name: str, tool_input: dict[str, Any], cwd: Path, root: Path) -> str | None:
    """Return the reason to block this tool call, or None to allow it."""
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return "Bash call without a command string"
        return check_bash(command, cwd, root)
    field = FILE_TOOLS.get(tool_name)
    if field:
        target = tool_input.get(field)
        if isinstance(target, str) and DUCKDB_PATH.search(target) and _in_data(target, cwd, root):
            return f"{target} is a database in the data folder; use `fin` commands"
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        tool_name = payload["tool_name"]
        tool_input = payload.get("tool_input") or {}
        cwd = Path(payload["cwd"]) if payload.get("cwd") else Path.cwd()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        sys.stderr.write(f"{CODE}: guard could not read hook input ({exc!r}); blocked\n")
        return BLOCK_EXIT

    reason = check(tool_name, tool_input, cwd, data_dir())
    if reason is None:
        return 0

    sys.stderr.write(f"{CODE}: blocked by guard: {reason}\n")
    try:
        append_action(build_record(payload, blocked=True, code=CODE, reason=reason))
    except OSError as exc:
        sys.stderr.write(f"{CODE}: could not log the block: {exc}\n")
    return BLOCK_EXIT


if __name__ == "__main__":
    sys.exit(main())
