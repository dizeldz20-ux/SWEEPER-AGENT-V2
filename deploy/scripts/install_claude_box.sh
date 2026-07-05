#!/usr/bin/env bash
# iPracticom Sweeper — installer for Claude's box (operator workstation).
#
# Cross-platform: works on Linux, macOS, and Windows (Git Bash / WSL).
# No sudo / root required for the sweeper itself — only the AWS IAM
# bootstrap in setup_aws.sh needs admin on the AWS side.
#
# Usage:
#   bash install_claude_box.sh                # interactive (recommended)
#   bash install_claude_box.sh --auto          # non-interactive, use defaults
#   bash install_claude_box.sh --uninstall     # remove everything
#
# Idempotent: re-running is safe. Existing config is preserved.
#
# What this does:
#   1. Detect OS (linux / macos / windows-git-bash / wsl)
#   2. Verify Python ≥ 3.11 (downloads if missing on macOS via pyenv)
#   3. Create venv at ~/.ipracticom-sweeper/venv
#   4. pip install -e . the sweeper package
#   5. Initialize ~/.ipracticom-sweeper/ with config templates
#   6. Install supervisor + create 3 services:
#        - sweeper-agent       (port 8810, the API for remote access)
#        - sweeper-dashboard   (port 8804, the web UI)
#        - sweeper-periodic    (5-min sweep, no port)
#   7. Register auto-start per OS:
#        Linux   → systemd --user
#        macOS   → launchd LaunchAgent
#        Windows → Task Scheduler (via schtasks)

set -euo pipefail

# --- Locate this script's directory (works in bash and git-bash) -----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/detect_os.sh
source "$LIB_DIR/detect_os.sh"
# shellcheck source=lib/python_check.sh
source "$LIB_DIR/python_check.sh"
# shellcheck source=lib/venv_install.sh
source "$LIB_DIR/venv_install.sh"
# shellcheck source=lib/config_init.sh
source "$LIB_DIR/config_init.sh"
# shellcheck source=lib/service_install.sh
source "$LIB_DIR/service_install.sh"

# --- Colors (TTY only) ----------------------------------------------------
if [[ -t 1 ]]; then
    C_BLUE='\033[0;34m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
else
    C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_RESET=''
fi
log()  { printf "${C_BLUE}[install]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[install]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[install]${C_RESET} ⚠️  %s\n" "$*" >&2; }
err()  { printf "${C_RED}[install]${C_RESET} ❌ %s\n" "$*" >&2; }

# --- Parse args -----------------------------------------------------------
MODE="interactive"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)        MODE="auto" ;;
        --uninstall)   MODE="uninstall" ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0 ;;
        *) err "unknown arg: $1"; exit 2 ;;
    esac
    shift
done

# --- Constants ------------------------------------------------------------
SWEEPER_HOME="$HOME/.ipracticom-sweeper"
VENV_DIR="$SWEEPER_HOME/venv"
CONFIG_DIR="$SWEEPER_HOME/config"
STATE_DIR="$SWEEPER_HOME/state"
LOG_DIR="$SWEEPER_HOME/logs"
SUPERVISOR_DIR="$SWEEPER_HOME/supervisor"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Uninstall ------------------------------------------------------------
if [[ "$MODE" == "uninstall" ]]; then
    log "Uninstalling iPracticom Sweeper from $SWEEPER_HOME"
    service_uninstall "$OS_FAMILY" || true
    if [[ -d "$SWEEPER_HOME" ]]; then
        log "Removing $SWEEPER_HOME (keeping config — pass --purge to wipe)"
        rm -rf "$VENV_DIR" "$STATE_DIR" "$LOG_DIR" "$SUPERVISOR_DIR"
        ok "Uninstalled. Config files preserved in $CONFIG_DIR."
    fi
    exit 0
fi

# --- Install flow ---------------------------------------------------------
log "iPracticom Sweeper installer"
log "  OS: $OS_FAMILY ($OS_PRETTY)"
log "  Home: $SWEEPER_HOME"
echo

detect_os
log "Detected OS family: $OS_FAMILY ($OS_PRETTY)"

# Step 1: Python
log "[1/5] Checking Python ≥ 3.11..."
python_check || { err "Python check failed"; exit 1; }
ok "Python $PY_VERSION at $PYTHON_BIN"

# Step 2: venv + install
log "[2/5] Creating virtualenv + installing sweeper..."
venv_install "$VENV_DIR" "$REPO_DIR" || { err "venv install failed"; exit 1; }
ok "Installed in $VENV_DIR"

# Step 3: config
log "[3/5] Initializing config..."
config_init "$CONFIG_DIR" "$MODE" || { err "config init failed"; exit 1; }
ok "Config at $CONFIG_DIR"

# Step 4: state dirs
log "[4/5] Creating state + log dirs..."
mkdir -p "$STATE_DIR/audit" "$STATE_DIR/snapshots" "$LOG_DIR"
ok "State at $STATE_DIR, logs at $LOG_DIR"

# Step 5: services + auto-start
log "[5/5] Installing services + auto-start..."
service_install "$OS_FAMILY" "$VENV_DIR" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR" "$SUPERVISOR_DIR" || {
    err "service install failed"; exit 1
}
ok "Services installed (supervisor-managed)"

# --- Summary --------------------------------------------------------------
echo
ok "🎉 Install complete!"
echo
cat <<EOF

Next steps:
  1. Edit credentials:
       $CONFIG_DIR/dashboard.env     (DASHBOARD_USER / DASHBOARD_PASS)
       $CONFIG_DIR/agent.env         (AGENT_API_TOKEN)
       $CONFIG_DIR/notifications.env (TELEGRAM_BOT_TOKEN / SLACK_BOT_TOKEN)
       $CONFIG_DIR/fleet.yaml        (EC2 tags for the fleet scanner)

  2. AWS setup (one-time, on the AWS admin side):
       bash setup_aws.sh

  3. Slack setup (optional):
       bash setup_slack.sh

  4. Start everything:
       bash scripts/start.sh           (or: supervisorctl -c supervisor/sweeper.conf start all)

  5. Access the dashboard:
       http://localhost:8804            (Basic Auth — creds in dashboard.env)

  6. Reachable from the internet (optional):
       cloudflared tunnel --url http://localhost:8804
       cloudflared tunnel --url http://localhost:8810

EOF
