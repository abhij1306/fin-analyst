# SPEC-16: AI providers and API keys

**On hold (owner, 2026-11-28): optional; "dont build the last PR until i say so the API one". No row of this spec is
started until the owner says so.**

## Summary

Owner, 2026-11-28: a user should be able to add a ChatGPT, Gemini or other API key. Today every AI step runs through
Claude Code on the user's own login, and fin's Python never calls a model (`product.md` §2). This is feasible, and
it is the largest spec here, because the rules that keep the assistant from approving things live in Claude Code's
settings and hooks, not in fin. It is last on purpose: specs 02, 03 and 05 build the seams it needs.

## What is tied to Claude today

| Part | Tie | Work |
|---|---|---|
| Reading a document | The reading file format is neutral; a skill writes it; `read_by = 'claude'` | small after SPEC-02 |
| Mapping proposals | `fin map propose` takes neutral JSON | small |
| Report narrative | Markdown with figure references, linted by fin | small |
| Chat | `claude -p` with stream parsing, session resume, and 16 decision commands denied by flag | large |
| Skills | 13 prompt files only Claude Code runs | medium |
| Guard and action log | Claude Code hooks | must move into fin |

## Scope

1. **Provider setting.** `config.toml [assistant] provider = "claude-code" | "anthropic" | "openai" | "gemini" |
   "openai-compatible"`, `model`, `base_url`. `claude-code` stays the default and needs no key.
2. **Keys** (Q6): `fin assistant key set <provider>` reads the key from a prompt (never an argument) into
   `~/FinAnalysis/keys.toml`, readable by the user only; an environment variable overrides it. A key is never
   written to `config.toml`, a log, a run record, the database or an error message; a test greps for it.
3. **Consent** (Q7). Turning on a provider that sends data out shows what will be sent and to whom, once, and
   records the consent in the audit log. Settings shows the provider in use on every screen that can send data.
4. **Single-shot tasks first:** document reading (page images to the reading file), mapping proposals, narrative.
   Each is one request with a structured answer checked by the validation fin already has. A failed check is shown
   to the user, not retried silently.
5. **Chat with tools.** A chat backend interface (text, tool call, done events). The Claude Code backend is today's
   code behind it. The API backend runs the tool loop inside fin; its tools are the SPEC-02 service functions that
   read or propose. **Decision functions (approve, accept, confirm, mark final, sent, close) are never tools.** The
   assistant can only ask the user to press the button.
6. **Guardrails in fin.** The service layer refuses a decision whose actor is not `user`, whatever the interface;
   tool calls are written to the run log; the skills' instructions move to prompt files fin loads for any provider.
7. **Evaluation.** The golden evaluation runs per provider, and the report says which provider produced it.

Out of scope: local models (an OpenAI-compatible endpoint covers them if the user runs one); fine-tuning; running
more than one provider in one turn.

## To verify before building

Each provider's limits for PDF and image input, how reliable its structured output is, its tool-call format, and
whether a given OpenAI-compatible endpoint supports tools. Check against current documentation at build time.

## Acceptance criteria

- [ ] With a recorded-response fake provider, a test reads a document, proposes mappings and drafts narrative, and
      fin's checks accept or refuse each as they would for Claude.
- [ ] A test calls every decision service with an AI actor and each is refused.
- [ ] The key never appears in logs, run records or error text (test).
- [ ] With `provider = "claude-code"` nothing changes for today's users.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Refuse decisions from an AI actor in the service layer | `src/fin/services` | criterion 2 |
| 2 | Add the provider setting, key storage and consent record | `config.py`, `src/fin/assistant`, Settings screen | criterion 3 |
| 3 | Add a provider client with reading, mapping and narrative tasks | `src/fin/assistant`, new dependencies | criterion 1 |
| 4 | Put the chat behind a backend interface | `src/fin/ui/chat_*` | chat tests unchanged |
| 5 | Add the API chat backend with fin's tools | `src/fin/assistant`, UI | chat tests with the fake provider |
| 6 | Move the skills' instructions into provider-neutral prompts | `.claude/commands`, `src/fin/assistant/prompts` | both backends pass |

Risk: high (keys, data leaving the machine, approvals). Every PR gets a separate reviewer.
