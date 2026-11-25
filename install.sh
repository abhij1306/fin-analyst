#!/usr/bin/env bash
# Install fin-analyst on macOS. Safe to run again: it only installs what is missing.
# Steps (docs/product.md §10): check macOS -> Homebrew -> brew bundle -> uv sync -> npm ci -> fin init -> fin doctor.
#
# Usage: ./install.sh [--parser docling|liteparse|llamaparse|none]
#   --parser NAME  install that optional PDF add-on, with its models (or none: remove it), and record
#                  it in config.toml ([pdf] parser), through `fin parser use NAME`:
#                  docling (local, about 1.4 GB, Apple silicon only), liteparse (local, small),
#                  llamaparse (cloud, needs LLAMA_CLOUD_API_KEY)
#   no flag        keep what config.toml says (none for a new install)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${FIN_DATA_DIR:-$HOME/FinAnalysis}"

die() {
  echo "install: ERROR: $*" >&2
  exit 1
}

USAGE="usage: ./install.sh [--parser docling|liteparse|llamaparse|none]"
PARSER_FLAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --parser)
      [[ $# -ge 2 ]] || die "--parser needs a name ($USAGE)"
      PARSER_FLAG="$2"
      shift 2
      ;;
    --parser=*)
      PARSER_FLAG="${1#--parser=}"
      shift
      ;;
    *) die "unknown option: $1 ($USAGE)" ;;
  esac
done
case "$PARSER_FLAG" in
  "" | none | docling | liteparse | llamaparse) ;;
  *) die "unknown PDF add-on: $PARSER_FLAG ($USAGE)" ;;
esac

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
  /bin/bash -c "$(curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew || die "Homebrew installed but brew was not found in /opt/homebrew or /usr/local."
fi
echo "brew: $(brew --version | head -n 1)"

# 3. System dependencies
step "Installing system dependencies (Brewfile: uv, node, quarto, poppler)"
if brew bundle check --file="$REPO_DIR/Brewfile" >/dev/null; then
  echo "all Brewfile dependencies already installed"
else
  # GitHub release downloads (the quarto cask) sometimes answer 504 for a minute or two; brew's own
  # retries come within seconds, so try the whole bundle again after a pause before giving up.
  for attempt in 1 2 3; do
    brew bundle install --file="$REPO_DIR/Brewfile" && break
    [[ $attempt -lt 3 ]] || die "brew bundle failed 3 times; check the network and run ./install.sh again"
    echo "brew bundle failed (attempt $attempt of 3); trying again in 60 seconds"
    sleep 60
  done
fi

cd "$REPO_DIR"

# The PDF add-on: the flag, or what config.toml already says (a plain sync would remove it).
# config.toml is read as TOML (uv is installed by now; --no-project needs no sync).
parser_in_config() {
  [[ -f "$DATA_DIR/config.toml" ]] || { echo none; return; }
  uv run --no-project python -I -c '
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f).get("pdf", {}).get("parser", "none"))
' "$DATA_DIR/config.toml"
}
PARSER="$PARSER_FLAG"
if [[ -z "$PARSER" ]]; then
  PARSER="$(parser_in_config)" || die "cannot read $DATA_DIR/config.toml; fix it or run ./install.sh --parser NAME"
fi
if [[ "$PARSER" == docling && "$ARCH" == x86_64 ]]; then
  die "the Docling add-on needs an Apple silicon Mac (PyTorch has no Intel macOS build); run ./install.sh --parser liteparse or --parser none"
fi
echo "PDF add-on: $PARSER"

# 4. Python environment (exact versions from uv.lock)
if [[ "$PARSER" == none ]]; then
  step "Syncing Python environment (uv sync --frozen)"
  uv sync --frozen
else
  step "Syncing Python environment with the $PARSER add-on (uv sync --frozen --extra $PARSER)"
  uv sync --frozen --extra "$PARSER"
fi

# 5. Dashboard packages (exact versions from dashboards/package-lock.json; npm ci is safe to repeat)
step "Installing dashboard packages (npm ci --prefix dashboards)"
npm ci --prefix dashboards --no-audit --no-fund --ignore-scripts

# 6. Data folder
# fin runs from the environment synced above (no second uv run).
step "Creating data folder (fin init)"
.venv/bin/fin init

# The PDF add-on's models and its line in config.toml (fin parser use: safe to repeat).
if [[ -n "$PARSER_FLAG" || "$PARSER" != none ]]; then
  step "Setting up the PDF add-on (fin parser use $PARSER)"
  .venv/bin/fin parser use "$PARSER"
fi

# 7. Health check
step "Checking installation (fin doctor)"
uv run fin doctor

step "Install complete. Log: $LOG_FILE"
