# SPEC-12: Install and first run

## Summary

The client's install took three attempts ([obs. 1 to 3](../test-run/2026-11-27-client.md)): a system package asked
for a password with no terminal to ask in, the script blamed the network and retried, and an old Intel Python on the
PATH broke the second attempt. This spec makes `install.sh` finish or stop with the real reason, and ends the
install in the browser.

## Scope

1. **Quarto on macOS without `sudo`.** Install the pinned Quarto from its tarball into the user's home, as the
   Linux path already does with a pinned version; drop the cask from `Brewfile`.
2. **Retry only what is a network failure.** A failure that is not one (a password prompt, a missing permission) stops
   at once and prints the failing command and what to do.
3. **Python.** uv uses its own managed Python 3.12 (`--python-preference only-managed` or the equivalent), whatever
   is on the PATH.
4. **Where the repo lives.** The handoff tells the client's agent to clone into a folder the user names (default
   `~/fin-analyst`); `install.sh` warns when run from a temporary folder.
5. **UI by default.** `install.sh` installs the `ui` extra and ends with the link to open. `--no-ui` skips it.
6. **PDF add-on question.** The install names the three add-ons and "none" with their cost; nothing is installed
   without a choice (SPEC-04 offers it again at the point of need).
7. **`fin doctor`** checks each of the above and prints the fix.

Out of scope: Windows; a packaged app.

## Acceptance criteria

- [ ] The macOS CI job installs with no `sudo` available and passes `fin doctor`.
- [ ] A test of the retry helper: a non-network failure is not retried.
- [ ] The CI install jobs run with a broken `python3` first on the PATH and still pass.
- [ ] The second run of `install.sh` still changes nothing (the existing check).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Install Quarto without sudo on macOS and stop retrying non-network failures | `install.sh`, `Brewfile`, `macos.yml` | criteria 1, 2 |
| 2 | Use uv's managed Python and warn about temporary folders | `install.sh`, `doctor.py`, handoff doc | criterion 3 |
| 3 | Install the UI by default and end the install with its link | `install.sh`, README, docs | install jobs green |

Risk: infrastructure. A separate reviewer, and the macOS job must run on the PR (SPEC-00 PR 1).
