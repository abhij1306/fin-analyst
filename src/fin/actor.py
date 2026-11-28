"""Who took an action (docs/specs/SPEC-02): the user, or an AI provider acting for the user. Stored
as 'user' or 'ai:<provider>' (mapping decisions, run arguments, and read_by for figures and rows an
AI read). Readers test the prefix (`is_ai`, LIKE 'ai:%'), never one provider's name.

The actor is passed in by the interface: the web UI always passes USER; the CLI passes
`from_environment()`, because a `fin` command run by Claude Code is Claude's."""

import os

USER = "user"
CLAUDE = "ai:claude"  # the AI that reads, proposes and runs fin today (Claude Code)
AI_PREFIX = "ai:"  # SQL readers match it with LIKE 'ai:%'


def is_ai(actor: str) -> bool:
    return actor.startswith(AI_PREFIX)


def label(actor: str) -> str:
    """How output names an actor or reader: 'Claude' for ai:claude, 'the user' for user; any other
    value (fin, an add-on engine) as stored."""
    if actor == CLAUDE:
        return "Claude"
    if actor == USER:
        return "the user"
    return actor.removeprefix(AI_PREFIX)


def from_environment() -> str:
    """The CLI's actor: CLAUDE when Claude Code runs the command (CLAUDECODE is set), else USER.
    Only the CLI calls this; services and the UI take the actor as an argument."""
    return CLAUDE if os.environ.get("CLAUDECODE") else USER


def recorded(actor: str, decision: str) -> tuple[str, bool]:
    """(actor, on_request) for a mapping_decisions row. A proposal is the AI's own (CLAUDE today);
    any other decision an AI records was taken because the user asked."""
    if decision == "proposed":
        return CLAUDE, False
    return actor, is_ai(actor)
