#!/usr/bin/env bash
# Install fin-analyst on macOS. Safe to run again: it only installs what is missing.
# Steps (PLAN.md §10): check macOS -> Homebrew -> brew bundle -> uv sync -> npm ci -> fin init -> fin doctor.
#
# Usage: ./install.sh [--docling | --no-docling]
#   --docling     also install the optional Docling PDF add-on (PyTorch and Docling's models, about
#                 1.4 GB) and record it in config.toml ([pdf] docling = true)
#   --no-docling  remove the add-on and record that ([pdf] docling = false)
#   neither       keep what config.toml says (off for a new install)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${FIN_DATA_DIR:-$HOME/FinAnalysis}"

die() {
  echo "install: ERROR: $*" >&2
  exit 1
}

DOCLING_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --docling) DOCLING_FLAG="--docling" ;;
    --no-docling) DOCLING_FLAG="--no-docling" ;;
    *) die "unknown option: $arg (usage: ./install.sh [--docling | --no-docling])" ;;
  esac
done

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
  brew bundle install --file="$REPO_DIR/Brewfile"
fi

cd "$REPO_DIR"

# The Docling add-on: the flag, or what config.toml already says (a plain sync would remove it).
# config.toml is read as TOML (uv is installed by now; --no-project needs no sync).
docling_in_config() {
  [[ -f "$DATA_DIR/config.toml" ]] || { echo false; return; }
  uv run --no-project python -I -c '
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print(str(tomllib.load(f).get("pdf", {}).get("docling") is True).lower())
' "$DATA_DIR/config.toml"
}
DOCLING=false
if [[ "$DOCLING_FLAG" == "--docling" ]]; then
  DOCLING=true
elif [[ -z "$DOCLING_FLAG" ]]; then
  DOCLING="$(docling_in_config)" || die "cannot read $DATA_DIR/config.toml; fix it or run ./install.sh --docling or --no-docling"
fi
if [[ "$DOCLING" == true && "$ARCH" == x86_64 ]]; then
  die "the Docling add-on needs an Apple silicon Mac (PyTorch has no Intel macOS build); run ./install.sh --no-docling"
fi
echo "Docling add-on: $DOCLING"

# 4. Python environment (exact versions from uv.lock)
if [[ "$DOCLING" == true ]]; then
  step "Syncing Python environment with the Docling add-on (uv sync --frozen --extra docling)"
  uv sync --frozen --extra docling
  step "Downloading Docling's models (once; about 700 MB)"
  .venv/bin/docling-tools models download layout tableformer rapidocr
else
  step "Syncing Python environment (uv sync --frozen)"
  uv sync --frozen
fi

# 5. Dashboard packages (exact versions from dashboards/package-lock.json; npm ci is safe to repeat)
step "Installing dashboard packages (npm ci --prefix dashboards)"
npm ci --prefix dashboards --no-audit --no-fund --ignore-scripts

# 6. Data folder
step "Creating data folder (fin init)"
uv run fin init ${DOCLING_FLAG:+"$DOCLING_FLAG"}

# 7. Health check
step "Checking installation (fin doctor)"
uv run fin doctor

step "Install complete. Log: $LOG_FILE"
