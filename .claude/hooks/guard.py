"""PreToolUse guard: ask the user before Claude Code changes or deletes data (PLAN.md §8).

Guardrails prevent accidents only; they never stop Claude doing what the user asked.
Each guarded action gets a Claude Code confirmation prompt ("ask", code E701):
- rm, mv (also rmdir, unlink, find -delete), redirects or tee writing into the data folder
- opening any .duckdb file in the data folder except through `fin`
- `fin delete` and `fin review confirm`

The hook always exits 0. For a guarded action it prints the decision JSON on stdout and logs it to
claude_actions.jsonl with "decision": "ask"; otherwise it prints nothing and the call goes ahead.
Input that cannot be parsed also gets "ask", never a silent allow.
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
                    return f"`fin {command_name}` changes data"

        if name in DELETERS and _paths_in_data(args, cwd, root):
            return f"`{name}` on the data folder ({root})"
        if name == "find" and "-delete" in args and _paths_in_data(args, cwd, root):
            return f"`find -delete` on the data folder ({root})"
        if name == "tee" and _paths_in_data(args, cwd, root):
            return f"`tee` into the data folder ({root})"
        for index, token in enumerate(words[:-1]):
            if token in REDIRECTS and _in_data(words[index + 1], cwd, root):
                return f"redirecting output into the data folder ({root})"

        if not _is_fin(rest):
            for token in words:
                for match in DUCKDB_PATH.findall(token):
                    if _in_data(match, cwd, root):
                        return f"{match} is a data-folder database opened outside `fin`"

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
    """Return the reason to ask the user before this tool call, or None to allow it."""
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return "Bash call without a command string"
        return check_bash(command, cwd, root)
    field = FILE_TOOLS.get(tool_name)
    if field:
        target = tool_input.get(field)
        if isinstance(target, str) and DUCKDB_PATH.search(target) and _in_data(target, cwd, root):
            return f"{target} is a data-folder database opened outside `fin`"
    return None


def ask(payload: dict[str, Any], reason: str) -> int:
    """Print the "ask" decision so Claude Code shows the user a confirmation prompt, and log it."""
    decision = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"{CODE}: {reason}. Approve only if you asked for this change.",
    }
    sys.stdout.write(json.dumps({"hookSpecificOutput": decision}) + "\n")
    try:
        append_action(build_record(payload, decision="ask", code=CODE, reason=reason))
    except OSError as exc:
        sys.stderr.write(f"{CODE}: could not log the confirmation request: {exc}\n")
    return 0


def main() -> int:
    payload: Any = None
    try:
        payload = json.loads(sys.stdin.read())
        tool_name = payload["tool_name"]
        tool_input = payload.get("tool_input") or {}
        cwd = Path(payload["cwd"]) if payload.get("cwd") else Path.cwd()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        known = payload if isinstance(payload, dict) else {}
        return ask(known, f"guard could not read hook input ({exc!r})")

    reason = check(tool_name, tool_input, cwd, data_dir())
    if reason is None:
        return 0
    return ask(payload, reason)


if __name__ == "__main__":
    sys.exit(main())
