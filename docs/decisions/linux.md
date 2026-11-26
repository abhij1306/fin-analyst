# Linux install support

Builder readings of the owner's 2026-11-26 request ("best would be to make it to be able to run in any environment linux", "dont remove mac its a added feature", "how client uses data is not our concern"); the owner confirms or changes them. Format: [README.md](README.md).

## 1. Supported Linux: Debian and Ubuntu, x86_64 and aarch64

- **Decision**: `install.sh` runs on Linux when `/etc/os-release` has `ID` or `ID_LIKE` of debian or ubuntu. Any other distribution stops before anything is written, with the tools to install by hand (uv, node 24 with npm, quarto, poppler, curl).
- **Why**: the request named Linux without a distribution; apt covers the common client machines and CI image, and a second package manager would be untested code.
- **How to change it**: `install.sh` (the OS check, `install_linux_deps`).

## 2. What Linux installs, and from where

- **Decision**: only what is missing or older than `fin doctor`'s minimums. poppler-utils, curl and xz-utils from apt; uv from its GitHub release; node from nodejs.org (newest 24.x); quarto from a pinned GitHub release `.deb` (1.10.19, the version CI renders with). Each download is checked against its published SHA-256. A tool already present at the minimum is kept, never replaced.
- **Why**: apt's own uv, node and quarto are absent or too old on Ubuntu 24.04. Homebrew takes the current version on macOS; Linux does the same for uv and node, and pins quarto because its release is a single file.
- **How to change it**: `install.sh` (`QUARTO_VERSION`, `NODE_LINE`, `MIN_*`; the minimums must equal `TOOL_MINIMUMS` in `src/fin/doctor.py`, which a unit test checks).

## 3. Node minimum stays 24

- **Decision**: unchanged. The dashboards' `engines` allows node 20 or later, but CI builds and tests with 24, so Linux installs 24 when the machine has less.
- **Why**: a lower minimum would be untested.
- **How to change it**: `doctor.py` (`TOOL_MINIMUMS`), `install.sh` (`MIN_NODE`, `NODE_LINE`).

## 4. Root or sudo

- **Decision**: as root, `install.sh` runs apt-get directly; otherwise through `sudo` (`sudo -n` without a terminal, so it fails instead of waiting for a password). Without root or sudo it stops with a message.
- **Why**: system packages need root; there is no per-user fallback worth maintaining.
- **How to change it**: `install.sh` (`install_linux_deps`).

## 5. Fix hints differ per OS

- **Decision**: `fin doctor` and `fin report --render` name `brew` commands on macOS and `./install.sh` (or apt-get for poppler) on Linux.
- **Why**: a brew command on Linux is wrong advice.
- **How to change it**: `src/fin/installhint.py`.
