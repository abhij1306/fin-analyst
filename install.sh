#!/usr/bin/env bash
# Install fin-analyst on macOS. Safe to run again: it only installs what is missing.
# Steps (PLAN.md §10): check macOS -> Homebrew -> brew bundle -> uv sync -> fin init -> fin doctor.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${FIN_DATA_DIR:-$HOME/FinAnalysis}"

die() {
  echo "install: ERROR: $*" >&2
  exit 1
}

step() {
  echo
  echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# 1. macOS and architecture (checked before anything is written)
[[ "$(uname -s)" == "Darwin" ]] || die "fin-analyst supports macOS only (found $(uname -s))."
ARCH="$(uname -m)"
case "$ARCH" in
  arm64 | x86_64) ;;
  *) die "unsupported architecture: $ARCH" ;;
esac

mkdir -p "$DATA_DIR/logs" || die "cannot create $DATA_DIR/logs (set FIN_DATA_DIR to a writable folder)"
LOG_FILE="$DATA_DIR/logs/install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

step "fin-analyst install: macOS $(sw_vers -productVersion) on $ARCH, repo $REPO_DIR, data $DATA_DIR"

# 2. Homebrew (ask before installing)
load_brew() {
  local candidate
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then
      eval "$("$candidate" shellenv)"
      return 0
    fi
  done
  return 1
}

step "Checking Homebrew"
if ! load_brew; then
  [[ -t 0 ]] || die "Homebrew is not installed. Install it from https://brew.sh and run ./install.sh again."
  read -r -p "Homebrew is not installed. Install it now? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || die "Homebrew is required. Install it from https://brew.sh and run ./install.sh again."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew || die "Homebrew installed but brew was not found in /opt/homebrew or /usr/local."
fi
echo "brew: $(brew --version | head -n 1)"

# 3. System dependencies
step "Installing system dependencies (Brewfile: uv, node, quarto)"
if brew bundle check --file="$REPO_DIR/Brewfile" >/dev/null; then
  echo "all Brewfile dependencies already installed"
else
  brew bundle install --file="$REPO_DIR/Brewfile"
fi

cd "$REPO_DIR"

# 4. Python environment (exact versions from uv.lock)
step "Syncing Python environment (uv sync --frozen)"
uv sync --frozen

# 5. Dashboards (npm ci --prefix dashboards) are added in Phase 10.

# 6. Data folder
step "Creating data folder (fin init)"
uv run fin init

# 7. Health check
step "Checking installation (fin doctor)"
uv run fin doctor

step "Install complete. Log: $LOG_FILE"
