#!/usr/bin/env bash
# iPracticom Sweeper — Update to latest version from the public repo.
#
# Pulls the latest code from github.com/dizeldz20-ux/ipracticom-sweeper-private
# (master branch), reinstalls the Python package + systemd units, preserves
# operator state (config, tokens, repair policy, audit logs, pending repairs,
# heartbeat), and restarts the services.
#
# Safe to re-run. Idempotent. Will refuse if /etc/ipracticom-sweeper does not
# look like an existing install.
#
# Usage:
#   sudo bash scripts/update.sh             # pull + reinstall + restart
#   sudo bash scripts/update.sh --check     # dry run: show what would change
#   sudo bash scripts/update.sh --version   # print current installed version
#   sudo bash scripts/update.sh --rollback  # restore from /var/lib/ipracticom-sweeper/.update_backup
#
# Requires: systemd, root, network access to GitHub.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPO_URL="https://github.com/dizeldz20-ux/ipracticom-sweeper-private.git"
REPO_BRANCH="${SWEEPER_BRANCH:-master}"
CONFIG_DIR="/etc/ipracticom-sweeper"
STATE_DIR="/var/lib/ipracticom-sweeper"
BACKUP_DIR="${STATE_DIR}/.update_backup"
SYSTEMD_DIR="$PROJECT_ROOT/systemd"
BACKUP_TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# --- Colors ---
if [[ -t 1 ]]; then
    C_BLUE='\033[0;34m' C_GREEN='\033[0;32m' C_YELLOW='\033[1;33m' C_RED='\033[0;31m' C_RESET='\033[0m'
else
    C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_RESET=''
fi

log()  { printf "${C_BLUE}[update]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[update]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[update]${C_RESET} ⚠️  %s\n" "$*"; }
err()  { printf "${C_RED}[update]${C_RESET} ❌ %s\n" "$*"; }
fail() { err "$*"; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || fail "must run as root. Try: sudo bash $0"
}

# --- Subcommands ---

cmd_version() {
    require_root
    local v="unknown"
    if [[ -f "$PROJECT_ROOT/pyproject.toml" ]]; then
        v=$(grep -E '^version\s*=' "$PROJECT_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
    fi
    echo "Installed version: $v"
    echo "Repo: $REPO_URL @ $REPO_BRANCH"
    if [[ -d "$PROJECT_ROOT/.git" ]]; then
        local head
        head=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
        echo "Local HEAD: $head"
        local remote
        remote=$(git -C "$PROJECT_ROOT" rev-parse --short "origin/$REPO_BRANCH" 2>/dev/null || echo "unknown")
        echo "Origin HEAD: $remote"
        if [[ "$head" != "$remote" ]]; then
            warn "Local is behind origin — run: sudo bash $0"
        fi
    fi
}

cmd_check() {
    require_root
    log "Dry run — checking what would change..."
    if [[ ! -d "$PROJECT_ROOT/.git" ]]; then
        fail "Not a git repo: $PROJECT_ROOT"
    fi
    cd "$PROJECT_ROOT"
    local local_head
    local_head=$(git rev-parse HEAD)
    log "Fetching origin/$REPO_BRANCH..."
    git fetch origin "$REPO_BRANCH" 2>&1 | tail -3
    local remote_head
    remote_head=$(git rev-parse "origin/$REPO_BRANCH")
    if [[ "$local_head" == "$remote_head" ]]; then
        ok "Already up to date ($(git rev-parse --short HEAD))"
    else
        local commits_behind
        commits_behind=$(git rev-list --count "HEAD..origin/$REPO_BRANCH")
        log "Behind by $commits_behind commit(s):"
        git log --oneline "HEAD..origin/$REPO_BRANCH" | head -10
        log ""
        log "Run 'sudo bash $0' to apply."
    fi
}

cmd_rollback() {
    require_root
    if [[ ! -d "$BACKUP_DIR" ]]; then
        fail "No backup found at $BACKUP_DIR"
    fi
    warn "Rolling back to backup at $BACKUP_DIR..."
    if [[ -d "$CONFIG_DIR" ]]; then
        cp -av "$BACKUP_DIR/config/"* "$CONFIG_DIR/" 2>&1 | tail -5
    fi
    if [[ -d "$STATE_DIR" ]]; then
        # Restore heartbeat, audit, pending_repairs, connectors.yaml — but NOT
        # .update_backup itself.
        for sub in audit pending_repairs snapshots cache fleet connectors.yaml heartbeat.json; do
            if [[ -e "$BACKUP_DIR/state/$sub" ]]; then
                rm -rf "$STATE_DIR/$sub"
                cp -a "$BACKUP_DIR/state/$sub" "$STATE_DIR/$sub"
            fi
        done
    fi
    ok "Rollback complete. Restarting services..."
    systemctl restart ipracticom-sweeper.service 2>/dev/null || true
    ok "Done. Verify with: systemctl status ipracticom-sweeper.service"
}

# --- Main update flow ---

cmd_update() {
    require_root

    if [[ ! -d "$PROJECT_ROOT/.git" ]]; then
        fail "Not a git repo: $PROJECT_ROOT"
    fi
    if [[ ! -d "$CONFIG_DIR" ]]; then
        fail "No existing install at $CONFIG_DIR. Use install-systemd.sh first."
    fi

    cd "$PROJECT_ROOT"

    # --- 1. Pre-flight: what's the current version vs remote ---
    log "Step 1/8: Pre-flight checks..."
    local local_head remote_head
    local_head=$(git rev-parse HEAD)
    git fetch origin "$REPO_BRANCH" 2>&1 | tail -3 || fail "git fetch failed (network?)"
    remote_head=$(git rev-parse "origin/$REPO_BRANCH")
    if [[ "$local_head" == "$remote_head" ]]; then
        ok "Already up to date ($(git rev-parse --short HEAD)). Nothing to do."
        exit 0
    fi
    local commits_behind
    commits_behind=$(git rev-list --count "HEAD..origin/$REPO_BRANCH")
    log "Behind by $commits_behind commit(s):"
    git log --oneline "HEAD..origin/$REPO_BRANCH" | head -10

    # --- 2. Backup operator state ---
    log "Step 2/8: Backing up config + state to $BACKUP_DIR..."
    rm -rf "$BACKUP_DIR"
    mkdir -p "$BACKUP_DIR/config" "$BACKUP_DIR/state"
    # Config: agent.env, telegram-bot.env, repair_policy.yaml — anything in /etc
    if [[ -d "$CONFIG_DIR" ]]; then
        cp -a "$CONFIG_DIR"/. "$BACKUP_DIR/config/" 2>/dev/null || true
    fi
    # State: heartbeat, audit logs, pending repairs, connectors.yaml, snapshots
    for sub in audit pending_repairs snapshots cache fleet connectors.yaml heartbeat.json metrics.db; do
        if [[ -e "$STATE_DIR/$sub" ]]; then
            cp -a "$STATE_DIR/$sub" "$BACKUP_DIR/state/" 2>/dev/null || true
        fi
    done
    # Mark the backup with the version we're rolling back from.
    local from_version
    from_version=$(grep -E '^version\s*=' "$PROJECT_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
    echo "$from_version" > "$BACKUP_DIR/from_version.txt"
    echo "$local_head" > "$BACKUP_DIR/from_commit.txt"
    ok "Backed up (version=$from_version, commit=$(git rev-parse --short "$local_head"))"

    # --- 3. Stop services ---
    log "Step 3/8: Stopping services..."
    if systemctl list-units --type=service --all 2>/dev/null | grep -q ipracticom-sweeper-telegram; then
        systemctl stop ipracticom-sweeper-telegram.service 2>/dev/null || warn "telegram bot stop failed"
        ok "telegram bot stopped"
    fi
    # We don't stop the timer (the service is oneshot per timer trigger), but we
    # disable it momentarily so a stray timer doesn't fire mid-upgrade.
    if systemctl is-enabled --quiet ipracticom-sweeper.timer 2>/dev/null; then
        systemctl stop ipracticom-sweeper.timer 2>/dev/null || true
        ok "sweeper timer stopped"
    fi

    # --- 4. Pull latest code ---
    log "Step 4/8: git pull origin $REPO_BRANCH..."
    if ! git pull --ff-only origin "$REPO_BRANCH" 2>&1 | tail -10; then
        err "git pull failed — restoring backup..."
        cmd_rollback
        fail "git pull failed. Site rolled back to previous version."
    fi
    ok "Pulled $(git rev-parse --short HEAD)"

    # --- 5. Reinstall Python package ---
    log "Step 5/8: Reinstalling Python package..."
    /usr/bin/python3 -m pip install -e "$PROJECT_ROOT" --break-system-packages --quiet 2>&1 | tail -3 \
        || fail "pip install failed"
    ok "package reinstalled"

    # --- 6. Refresh systemd units (if changed) ---
    log "Step 6/8: Refreshing systemd units..."
    if [[ -d "$SYSTEMD_DIR" ]]; then
        for unit in ipracticom-sweeper.service ipracticom-sweeper.timer; do
            if [[ -f "$SYSTEMD_DIR/$unit" ]]; then
                if ! diff -q "$SYSTEMD_DIR/$unit" "/etc/systemd/system/$unit" >/dev/null 2>&1; then
                    cp -v "$SYSTEMD_DIR/$unit" /etc/systemd/system/
                    log "  $unit updated"
                fi
            fi
        done
        systemctl daemon-reload
    fi

    # --- 7. Restart services ---
    log "Step 7/8: Restarting services..."
    systemctl start ipracticom-sweeper.timer 2>/dev/null || warn "timer start failed (already running?)"
    ok "sweeper timer started"
    if systemctl list-unit-files 2>/dev/null | grep -q ipracticom-sweeper-telegram.service; then
        systemctl start ipracticom-sweeper-telegram.service 2>/dev/null || warn "telegram bot start failed"
        ok "telegram bot started"
    fi

    # --- 8. Verify ---
    log "Step 8/8: Verifying..."
    sleep 2
    local api_ok=0
    # 8a. API root or /healthz
    if curl -sf http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
        ok "agent_api /healthz returns 200"
        api_ok=1
    elif curl -sf http://127.0.0.1:8787/ >/dev/null 2>&1; then
        ok "agent_api / (root) returns 200"
        api_ok=1
    else
        warn "agent_api not responding — check 'systemctl status ipracticom-sweeper-api.service'"
    fi
    # 8b. v6 dashboard smoke — at least one v6 route should serve (200 or 302)
    if [[ "$api_ok" == "1" ]]; then
        if curl -sf -o /dev/null --max-time 5 http://127.0.0.1:8787/v6/machines 2>/dev/null \
            || curl -sf -o /dev/null --max-time 5 http://127.0.0.1:8787/v6/alerts 2>/dev/null \
            || curl -s  -o /dev/null --max-time 5 -w '%{http_code}' http://127.0.0.1:8787/v6/index 2>/dev/null | grep -qE '^(2|3)'; then
            ok "v6 dashboard surface responding"
        else
            warn "v6 routes not detected (this is fatal if upgrading TO v0.6.0+)"
        fi
    fi
    local new_version
    new_version=$(grep -E '^version\s*=' "$PROJECT_ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
    ok "Updated from $from_version → $new_version"

    echo ""
    echo "${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo "  ✅ Update complete: $from_version → $new_version"
    echo "${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}"
    echo ""
    echo "  Useful commands:"
    echo "    systemctl status ipracticom-sweeper.timer"
    echo "    systemctl status ipracticom-sweeper-telegram.service"
    echo "    journalctl -u ipracticom-sweeper -f"
    echo "    sudo bash $0 --rollback    # undo this update"
    echo "    sudo bash $0 --check       # check for newer versions"
    echo ""
}

# --- Dispatch ---
case "${1:-}" in
    --check)    cmd_check ;;
    --version)  cmd_version ;;
    --rollback) cmd_rollback ;;
    --help|-h)
        cat <<EOF
iPracticom Sweeper update script

Usage:
  sudo bash $0 [OPTIONS]

Options:
  (no args)     Update to latest from origin/$REPO_BRANCH
  --check       Show what would change (no modifications)
  --version     Print installed version + remote HEAD
  --rollback    Restore config + state from last backup
  --help, -h    This message

Repository: $REPO_URL
Branch: $REPO_BRANCH (override with SWEEPER_BRANCH=foo bash $0)
EOF
        ;;
    "")         cmd_update ;;
    *)          err "Unknown option: $1"; echo "Run: sudo bash $0 --help"; exit 2 ;;
esac