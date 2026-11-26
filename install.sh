#!/usr/bin/env bash
# Install fin-analyst on macOS or Linux (Debian/Ubuntu). Safe to run again: it only installs what is missing.
# Steps (docs/product.md §10): check the OS -> system tools (macOS: Homebrew and brew bundle; Linux: apt-get and
# pinned downloads) -> uv sync -> npm ci -> fin init -> fin doctor.
#
# Usage: ./install.sh [--parser docling|liteparse|llamaparse|none]
#   --parser NAME  install that optional PDF add-on, with its models (or none: remove it), and record
#                  it in config.toml ([pdf] parser), through `fin parser use NAME`:
#                  docling (local, about 1.4 GB, not on Intel Macs), liteparse (local, small),
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

# Linux: tools that fin doctor checks (minimums equal TOOL_MINIMUMS in src/fin/doctor.py; a test keeps them equal).
MIN_UV="0.11"
MIN_NODE="24"
MIN_QUARTO="1.7"
QUARTO_VERSION="1.10.19" # pinned .deb from GitHub releases (the version CI renders reports with)
NODE_LINE="v24.x"        # the newest release of that line from nodejs.org

LINUX_MANUAL="Install by hand: uv >= $MIN_UV, node >= $MIN_NODE with npm, quarto >= $MIN_QUARTO, poppler (pdftoppm), curl; then run ./install.sh again."

# 1. OS and architecture (checked before anything is written)
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)
    case "$ARCH" in
      arm64 | x86_64) ;;
      *) die "unsupported architecture: $ARCH" ;;
    esac
    ;;
  Linux)
    case "$ARCH" in
      x86_64 | aarch64) ;;
      *) die "unsupported architecture: $ARCH (Linux needs x86_64 or aarch64)" ;;
    esac
    [[ -r /etc/os-release ]] || die "cannot read /etc/os-release. $LINUX_MANUAL"
    # shellcheck disable=SC1091
    OS_ID="$(. /etc/os-release && echo "${ID:-}:${ID_LIKE:-}")"
    case " ${OS_ID//:/ } " in
      *" debian "* | *" ubuntu "*) ;;
      *) die "unsupported Linux distribution (${OS_ID%%:*}); only Debian and Ubuntu are supported. $LINUX_MANUAL" ;;
    esac
    ;;
  *) die "fin-analyst supports macOS and Linux (Debian/Ubuntu) only (found $OS)." ;;
esac

mkdir -p "$DATA_DIR/logs" || die "cannot create $DATA_DIR/logs (set FIN_DATA_DIR to a writable folder)"
LOG_FILE="$DATA_DIR/logs/install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

if [[ "$OS" == Darwin ]]; then
  step "fin-analyst install: macOS $(sw_vers -productVersion) on $ARCH, repo $REPO_DIR, data $DATA_DIR"
else
  # shellcheck disable=SC1091
  step "fin-analyst install: $(. /etc/os-release && echo "$PRETTY_NAME") on $ARCH, repo $REPO_DIR, data $DATA_DIR"
fi

# 2-3. System tools
install_macos_deps() {
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
}

# version_ge FOUND MINIMUM: dotted numbers, FOUND >= MINIMUM
version_ge() {
  local found="$1" minimum="$2"
  [[ "$(printf '%s\n%s\n' "$minimum" "$found" | sort -V | head -n 1)" == "$minimum" ]]
}

# tool_ok TOOL MINIMUM: TOOL is on PATH and its version is at least MINIMUM
tool_ok() {
  local tool="$1" minimum="$2" found
  command -v "$tool" >/dev/null 2>&1 || return 1
  found="$("$tool" --version 2>/dev/null | grep -Eo '[0-9]+(\.[0-9]+)+' | head -n 1)" || return 1
  [[ -n "$found" ]] && version_ge "$found" "$minimum"
}

fetch() { # fetch URL FILE
  local url="$1" out="$2"
  curl -fsSL --proto '=https' --tlsv1.2 --retry 3 --retry-delay 10 -o "$out" "$url"
}

check_sum() { # check_sum FILE EXPECTED_SHA256
  local path="$1" expected="$2"
  [[ "$(sha256sum "$path" | cut -d ' ' -f 1)" == "$expected" ]] || die "checksum mismatch for $path; run ./install.sh again"
}

install_linux_deps() {
  local sudo_cmd=() missing=() apt_pkgs=() deb_arch uv_arch node_arch file sum
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "not root and sudo is not installed. Run as root or install sudo. $LINUX_MANUAL"
    sudo_cmd=(sudo)
    [[ -t 0 ]] || sudo_cmd=(sudo -n) # no password prompt in a non-interactive shell
  fi
  case "$ARCH" in
    x86_64) deb_arch=amd64 uv_arch=x86_64 node_arch=x64 ;;
    *) deb_arch=arm64 uv_arch=aarch64 node_arch=arm64 ;;
  esac

  command -v pdftoppm >/dev/null 2>&1 || apt_pkgs+=(poppler-utils)
  command -v curl >/dev/null 2>&1 || apt_pkgs+=(curl)
  [[ -f /etc/ssl/certs/ca-certificates.crt ]] || apt_pkgs+=(ca-certificates)
  command -v xz >/dev/null 2>&1 || apt_pkgs+=(xz-utils)
  tool_ok uv "$MIN_UV" || missing+=(uv)
  # npm 7+ reads the dashboards' lockfile (version 3); Node 24 ships npm 11
  { tool_ok node "$MIN_NODE" && tool_ok npm 7; } || missing+=(node)
  tool_ok quarto "$MIN_QUARTO" || missing+=(quarto)

  if [[ ${#apt_pkgs[@]} -eq 0 && ${#missing[@]} -eq 0 ]]; then
    echo "all system dependencies already installed"
    return
  fi

  apt_get() {
    # mirrors can fail for a minute or two; try the command again after a pause
    local subcommand="$1" attempt
    for attempt in 1 2 3; do
      "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get "$@" && return 0
      [[ $attempt -lt 3 ]] || die "apt-get $subcommand failed 3 times; check the network and run ./install.sh again"
      echo "apt-get $subcommand failed (attempt $attempt of 3); trying again in 30 seconds"
      sleep 30
    done
  }

  if [[ ${#apt_pkgs[@]} -gt 0 ]]; then
    echo "installing with apt-get: ${apt_pkgs[*]}"
    apt_get update -qq
    apt_get install -y --no-install-recommends "${apt_pkgs[@]}"
  fi
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "${TMP_DIR:-}"' EXIT

  if [[ " ${missing[*]} " == *" uv "* ]]; then
    echo "installing uv (GitHub release)"
    file="uv-${uv_arch}-unknown-linux-gnu.tar.gz"
    fetch "https://github.com/astral-sh/uv/releases/latest/download/$file" "$TMP_DIR/$file"
    fetch "https://github.com/astral-sh/uv/releases/latest/download/$file.sha256" "$TMP_DIR/$file.sha256"
    check_sum "$TMP_DIR/$file" "$(cut -d ' ' -f 1 "$TMP_DIR/$file.sha256")"
    tar -xzf "$TMP_DIR/$file" -C "$TMP_DIR"
    "${sudo_cmd[@]}" install -m 0755 "$TMP_DIR/uv-${uv_arch}-unknown-linux-gnu/uv" "$TMP_DIR/uv-${uv_arch}-unknown-linux-gnu/uvx" /usr/local/bin/
  fi

  if [[ " ${missing[*]} " == *" node "* ]]; then
    echo "installing node $NODE_LINE (nodejs.org)"
    fetch "https://nodejs.org/dist/latest-$NODE_LINE/SHASUMS256.txt" "$TMP_DIR/SHASUMS256.txt"
    file="$(grep -Eo "node-v[0-9.]+-linux-$node_arch\.tar\.xz" "$TMP_DIR/SHASUMS256.txt" | head -n 1)"
    [[ -n "$file" ]] || die "no node $NODE_LINE build for linux-$node_arch on nodejs.org"
    sum="$(grep " $file\$" "$TMP_DIR/SHASUMS256.txt" | cut -d ' ' -f 1)"
    fetch "https://nodejs.org/dist/latest-$NODE_LINE/$file" "$TMP_DIR/$file"
    check_sum "$TMP_DIR/$file" "$sum"
    "${sudo_cmd[@]}" mkdir -p /opt/fin-node
    "${sudo_cmd[@]}" rm -rf /opt/fin-node/current
    "${sudo_cmd[@]}" mkdir /opt/fin-node/current
    "${sudo_cmd[@]}" tar -xJf "$TMP_DIR/$file" -C /opt/fin-node/current --strip-components=1
    for file in node npm npx; do
      "${sudo_cmd[@]}" ln -sf "/opt/fin-node/current/bin/$file" "/usr/local/bin/$file"
    done
  fi

  if [[ " ${missing[*]} " == *" quarto "* ]]; then
    echo "installing quarto $QUARTO_VERSION (GitHub release .deb)"
    file="quarto-$QUARTO_VERSION-linux-$deb_arch.deb"
    fetch "https://github.com/quarto-dev/quarto-cli/releases/download/v$QUARTO_VERSION/$file" "$TMP_DIR/$file"
    fetch "https://github.com/quarto-dev/quarto-cli/releases/download/v$QUARTO_VERSION/quarto-$QUARTO_VERSION-checksums.txt" "$TMP_DIR/quarto-sums.txt"
    sum="$(grep " $file\$" "$TMP_DIR/quarto-sums.txt" | cut -d ' ' -f 1)"
    [[ -n "$sum" ]] || die "no checksum for $file in the Quarto release"
    check_sum "$TMP_DIR/$file" "$sum"
    chmod 0644 "$TMP_DIR/$file"
    apt_get install -y "$TMP_DIR/$file"
  fi

  # Tools go to /usr/local/bin; an older copy earlier on PATH (~/.local/bin, nvm) would still win in this
  # shell and in `uv run fin doctor`, so stop here instead of reporting success.
  command -v pdftoppm >/dev/null 2>&1 || die "pdftoppm is still not on PATH after the install. $LINUX_MANUAL"
  for file in "uv $MIN_UV" "node $MIN_NODE" "npm 7" "quarto $MIN_QUARTO"; do
    # shellcheck disable=SC2086
    tool_ok $file || die "${file%% *} on PATH ($(command -v "${file%% *}" || echo none)) is older than ${file##* } or missing; put /usr/local/bin first on PATH or remove the older copy, then run ./install.sh again. $LINUX_MANUAL"
  done
}

if [[ "$OS" == Darwin ]]; then
  install_macos_deps
else
  step "Installing system dependencies (apt-get, uv, node, quarto, poppler)"
  install_linux_deps
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
if [[ "$PARSER" == docling && "$OS" == Darwin && "$ARCH" == x86_64 ]]; then
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
