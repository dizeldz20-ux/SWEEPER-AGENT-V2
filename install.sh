#!/usr/bin/env bash
# iPracticom Sweeper — install.sh — the ONE-LINER installer.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/dizeldz20-ux/ipracticom-sweeper-private/master/install.sh | sudo bash
#   sudo SWEEPER_BRANCH=v0.6.3 bash install.sh     # pin to a tag/branch
#   sudo bash install.sh --uninstall               # remove
#
# What it does (all in one shot, idempotent):
#   1. Detects OS family (apt = Debian/Ubuntu, dnf = RHEL/Amazon Linux/Fedora)
#   2. Installs system deps (smartmontools, sysstat, aide, python3-venv, python3-pip)
#   3. Clones repo to /opt/ipracticom-sweeper (if not already there) or 'git pull'
#   4. Installs Python package site-wide (--break-system-packages)
#   5. Creates /var/lib/ipracticom-sweeper/{audit,snapshots,cache,fleet,pending_repairs}
#   6. Seeds /etc/ipracticom-sweeper/repair_policy.yaml from the repo default
#   7. Installs + enables systemd units (sweeper.timer, sweeper-api.service)
#   8. Triggers one initial sweep
#   9. Verifies http://127.0.0.1:8787/ is responding
#  10. Prints a summary banner
#
# Safe to re-run. Network + curl/git/apt-or-dnf access required.

set -euo pipefail

REPO_URL="https://github.com/dizeldz20-ux/ipracticom-sweeper-private.git"
# Default to the repo's live branch. The old default (v1.0.0) pinned a fresh
# install to an ancient tag; there is no v1.5.x tag, so `master` is the current
# release line. Override with SWEEPER_BRANCH=<tag> to pin.
BRANCH="${SWEEPER_BRANCH:-master}"
INSTALL_DIR="${SWEEPER_INSTALL_DIR:-/opt/ipracticom-sweeper}"
SERVICE_NAME="ipracticom-sweeper"
STATE_DIR="/var/lib/${SERVICE_NAME}"
LOG_DIR="/var/log/${SERVICE_NAME}"
CONFIG_DIR="/etc/${SERVICE_NAME}"

# --- Colors ---
if [[ -t 1 ]]; then
    C_BLUE='\033[0;34m' C_GREEN='\033[0;32m' C_YELLOW='\033[1;33m' C_RED='\033[0;31m' C_RESET='\033[0m'
else
    C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_RESET=''
fi

log()  { printf "${C_BLUE}[install]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[install]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[install]${C_RESET} ⚠️  %s\n" "$*" >&2; }
err()  { printf "${C_RED}[install]${C_RESET} ❌ %s\n" "$*" >&2; }
fail() { err "$*"; exit 1; }
banner() { printf "\n${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n  %s\n${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n\n" "$1"; }

# --- Root + dependencies ---
if [[ $EUID -ne 0 ]]; then
    fail "Must run as root. Try: sudo bash $0 $*"
fi

# --- Uninstall mode ---
for arg in "$@"; do
    case "$arg" in
        --uninstall)
            log "Uninstalling ${SERVICE_NAME}..."
            if [[ -d "$INSTALL_DIR" ]]; then
                (cd "$INSTALL_DIR" && bash scripts/install-systemd.sh --uninstall) || true
            else
                systemctl disable --now "${SERVICE_NAME}.timer" 2>/dev/null || true
                systemctl disable --now "${SERVICE_NAME}-api.service" 2>/dev/null || true
                systemctl disable --now "ipracticom-sweeper-watchdog.timer" 2>/dev/null || true
                rm -f "/etc/systemd/system/${SERVICE_NAME}.service" \
                      "/etc/systemd/system/${SERVICE_NAME}.timer" \
                      "/etc/systemd/system/${SERVICE_NAME}-api.service" \
                      "/etc/systemd/system/ipracticom-sweeper-watchdog.service" \
                      "/etc/systemd/system/ipracticom-sweeper-watchdog.timer" \
                      "/usr/local/bin/ipracticom-sweeper-watchdog.sh"
                systemctl daemon-reload || true
            fi
            ok "systemd units removed"
            warn "State preserved at $STATE_DIR (delete manually if desired)"
            banner "🗑️  Uninstall complete"
            exit 0
            ;;
        --help|-h)
            cat <<EOF
iPracticom Sweeper one-liner installer

Usage:
  sudo bash $0              # install from master (current release line)
  sudo SWEEPER_BRANCH=<tag> bash $0
  sudo bash $0 --uninstall

Env overrides:
  SWEEPER_BRANCH=...    git ref to install (default: master; pin a tag to freeze)
  SWEEPER_INSTALL_DIR=...  where to clone (default: /opt/ipracticom-sweeper)

Repo: $REPO_URL
EOF
            exit 0
            ;;
    esac
done

# --- Preflight: tools we need ---
log "Preflight: checking required commands..."
for cmd in git python3 systemctl curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        fail "missing required command: $cmd. Install it first."
    fi
done
ok "git, python3, systemctl, curl all present"

# --- OS detection ---
PKG_MGR=""
if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
fi
[[ -n "$PKG_MGR" ]] || fail "no supported package manager (apt/dnf/yum). Open an issue."
log "OS package manager detected: $PKG_MGR"

# --- Install OS packages (best-effort, degrade gracefully) ---
log "Installing system dependencies via $PKG_MGR..."
case "$PKG_MGR" in
    apt)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq 2>&1 | tail -1 || warn "apt-get update had warnings"
        apt-get install -y -qq \
            smartmontools sysstat aide python3-venv python3-pip 2>&1 | tail -1 \
            || warn "some apt packages failed (will degrade gracefully)"
        ;;
    dnf|yum)
        $PKG_MGR install -y -q \
            smartmontools sysstat aide python3-pip python3-devel gcc 2>&1 | tail -1 \
            || warn "some dnf packages failed (will degrade gracefully)"
        ;;
esac
ok "system dependencies installed"

# --- Get the code ---
log "Acquiring code at $INSTALL_DIR..."
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "  existing repo found, fetching $BRANCH..."
    cd "$INSTALL_DIR"
    git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"
    git fetch --depth 1 origin "$BRANCH" 2>&1 | tail -2
    git reset --hard "origin/$BRANCH" 2>&1 | tail -2 || fail "could not update existing checkout"
else
    log "  cloning..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" 2>&1 | tail -3
fi
cd "$INSTALL_DIR"
# We may be on a detached HEAD after git reset — pin to the actual commit and show it.
HEAD_SHA="$(git rev-parse --short HEAD)"
log "  at commit ${HEAD_SHA} of branch ${BRANCH}"

# --- Python package ---
log "Installing Python package site-wide..."
/usr/bin/python3 -m pip install -e "$INSTALL_DIR" --break-system-packages --quiet 2>&1 | tail -3 \
    || fail "pip install failed"

# --- State + config dirs ---
log "Creating runtime dirs..."
mkdir -p "$STATE_DIR"/{audit,snapshots,cache,fleet,pending_repairs} "$LOG_DIR" "$CONFIG_DIR"
chmod 750 "$STATE_DIR" "$LOG_DIR" "$CONFIG_DIR"
ok "state=$STATE_DIR, logs=$LOG_DIR, config=$CONFIG_DIR"

# --- .env (only if missing) ---
if [[ ! -f "$CONFIG_DIR/agent.env" ]] && [[ -f "$INSTALL_DIR/.env.example" ]]; then
    cp "$INSTALL_DIR/.env.example" "$CONFIG_DIR/agent.env"
    chmod 640 "$CONFIG_DIR/agent.env"
    warn "Created $CONFIG_DIR/agent.env — edit it to set SLACK_WEBHOOK_URL or TELEGRAM_BOT_TOKEN (optional)"
else
    log "$CONFIG_DIR/agent.env already exists (skipping)"
fi

# --- repair_policy.yaml seed ---
if [[ -f "$INSTALL_DIR/etc/repair_policy.yaml" ]] && [[ ! -f "$CONFIG_DIR/repair_policy.yaml" ]]; then
    cp "$INSTALL_DIR/etc/repair_policy.yaml" "$CONFIG_DIR/repair_policy.yaml"
    chmod 640 "$CONFIG_DIR/repair_policy.yaml"
    log "  seeded repair_policy.yaml"
fi

# --- Hand off to install-systemd.sh (does the systemd work) ---
log "Running scripts/install-systemd.sh..."
bash "$INSTALL_DIR/scripts/install-systemd.sh"

# --- Final verify ---
log "Final verification..."
sleep 2
DASH_OK=0
for url in "http://127.0.0.1:8787/healthz" "http://127.0.0.1:8787/" "http://127.0.0.1:8787/api/snapshot"; do
    if curl -sf -o /dev/null --max-time 5 "$url" 2>/dev/null; then
        DASH_OK=1
        log "  ✓ $url responded"
        break
    fi
done
if [[ "$DASH_OK" != "1" ]]; then
    warn "  no API endpoint answered on 127.0.0.1:8787. Check 'journalctl -u ipracticom-sweeper-api -n 50'."
fi

# --- Banner ---
VERSION="$(grep -E '^version\s*=' "$INSTALL_DIR/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
banner "🎉 iPracticom Sweeper v${VERSION} installed (${HEAD_SHA})"

cat <<EOF
  📍 Install dir:    ${INSTALL_DIR}
  📦 State:          ${STATE_DIR}
  📝 Logs:           ${LOG_DIR}
  ⚙️  Config:        ${CONFIG_DIR}

  👉 Next steps:
     1. (Optional) edit ${CONFIG_DIR}/agent.env
     2. Open the dashboard:
          http://127.0.0.1:8787/

  Useful commands:
    systemctl status ipracticom-sweeper.timer         # timer state
    systemctl status ipracticom-sweeper-api.service   # dashboard/API
    journalctl -u ipracticom-sweeper -f               # sweep logs
    journalctl -u ipracticom-sweeper-api -f           # API logs
    sudo bash scripts/update.sh                        # upgrade to a newer version

  To remove:  sudo bash $0 --uninstall
EOF
