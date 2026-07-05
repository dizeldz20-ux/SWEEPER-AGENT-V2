#!/usr/bin/env bash
# Install iPracticom Sweeper as systemd services: timer (periodic sweep) + API (HTTP).
#
# Usage:
#   sudo bash scripts/install-systemd.sh           # install
#   sudo bash scripts/install-systemd.sh --uninstall
#   sudo bash scripts/install-systemd.sh --uninstall --purge  # also wipe state dir
#
# What this does:
#   1. Install the Python package site-wide (/usr/bin/python3 -m pip install -e . --break-system-packages)
#   2. Create runtime dirs (/var/lib/ipracticom-sweeper/{audit,snapshots,cache,fleet,pending_repairs})
#   3. Copy default repair_policy.yaml to /etc/ipracticom-sweeper/ if missing
#   4. Copy unit files to /etc/systemd/system/ (sweeper.service, sweeper.timer, sweeper-api.service)
#   5. Enable + start the timer (sweeps every 5 min) AND the API (long-running on :8787)
#   6. Verify both with systemctl status + curl /healthz
#
# Uninstall (--uninstall): stop + disable both units, remove unit files.
# With --purge: also wipe /var/lib/ipracticom-sweeper/ (DESTRUCTIVE — audit/forensic data).
#
# Requires: systemd, root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="$PROJECT_ROOT/systemd"
STATE_DIR="/var/lib/ipracticom-sweeper"
CONFIG_DIR="/etc/ipracticom-sweeper"

# --- Helpers -----------------------------------------------------------------

log() { echo "[install-systemd] $*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root"

PURGE=0
ACTION="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall) ACTION="uninstall" ;;
        --purge)     PURGE=1 ;;
        --help|-h)
            echo "Usage: sudo bash $0 [--uninstall] [--purge]"
            echo "  (no args)   install + enable + start sweeper.timer + sweeper-api.service"
            echo "  --uninstall disable + remove systemd units"
            echo "  --purge     with --uninstall: also wipe $STATE_DIR (DESTRUCTIVE)"
            exit 0
            ;;
    esac
done

# --- Uninstall ---------------------------------------------------------------

if [ "$ACTION" = "uninstall" ]; then
    log "Uninstalling ipracticom-sweeper services..."
    # Stop in reverse-start order
    systemctl stop ipracticom-sweeper-watchdog.timer 2>/dev/null || true
    systemctl stop ipracticom-sweeper-api.service 2>/dev/null || true
    systemctl stop ipracticom-sweeper.timer 2>/dev/null || true
    systemctl disable ipracticom-sweeper-watchdog.timer 2>/dev/null || true
    systemctl disable ipracticom-sweeper-api.service 2>/dev/null || true
    systemctl disable ipracticom-sweeper.timer 2>/dev/null || true
    rm -f /etc/systemd/system/ipracticom-sweeper-watchdog.service
    rm -f /etc/systemd/system/ipracticom-sweeper-watchdog.timer
    rm -f /usr/local/bin/ipracticom-sweeper-watchdog.sh
    rm -f /etc/systemd/system/ipracticom-sweeper-api.service
    rm -f /etc/systemd/system/ipracticom-sweeper.service
    rm -f /etc/systemd/system/ipracticom-sweeper.timer
    systemctl daemon-reload

    if [ "$PURGE" = "1" ]; then
        warn_color="$(printf '\033[1;33m')"; reset_color="$(printf '\033[0m')"
        echo "${warn_color}[install-systemd] ⚠️  PURGE: wiping $STATE_DIR${reset_color}" >&2
        rm -rf "$STATE_DIR"
    else
        log "Runtime dirs in $STATE_DIR preserved (re-run with --purge to wipe)."
    fi
    log "Uninstalled."
    exit 0
fi

# --- Install -----------------------------------------------------------------

log "Step 1/6: Installing Python package site-wide..."
/usr/bin/python3 -m pip install -e "$PROJECT_ROOT" --break-system-packages --quiet

log "Step 2/6: Creating runtime directories (audit, snapshots, cache, fleet, pending_repairs)..."
mkdir -p "$STATE_DIR"/{audit,snapshots,cache,fleet,pending_repairs}
chmod 750 "$STATE_DIR"
log "  dirs: $STATE_DIR/{audit,snapshots,cache,fleet,pending_repairs}"

log "Step 3/6: Seeding /etc/ipracticom-sweeper/repair_policy.yaml if missing..."
mkdir -p "$CONFIG_DIR"
POLICY_SRC="$PROJECT_ROOT/etc/repair_policy.yaml"
if [[ -f "$POLICY_SRC" ]] && [[ ! -f "$CONFIG_DIR/repair_policy.yaml" ]]; then
    cp "$POLICY_SRC" "$CONFIG_DIR/repair_policy.yaml"
    chmod 640 "$CONFIG_DIR/repair_policy.yaml"
    log "  copied default policy (default=auto, service_restart=needs_approval)"
else
    log "  $CONFIG_DIR/repair_policy.yaml already exists, leaving untouched"
fi

log "Step 4/6: Installing systemd unit files..."
for unit in ipracticom-sweeper.service ipracticom-sweeper.timer ipracticom-sweeper-api.service ipracticom-sweeper-watchdog.service ipracticom-sweeper-watchdog.timer; do
    if [[ -f "$SYSTEMD_DIR/$unit" ]]; then
        cp "$SYSTEMD_DIR/$unit" /etc/systemd/system/
        chmod 644 "/etc/systemd/system/$unit"
        log "  /etc/systemd/system/$unit"
    else
        fail "missing $SYSTEMD_DIR/$unit"
    fi
done
# Install the watchdog helper script
if [[ -f "$SYSTEMD_DIR/ipracticom-sweeper-watchdog.sh" ]]; then
    cp "$SYSTEMD_DIR/ipracticom-sweeper-watchdog.sh" /usr/local/bin/ipracticom-sweeper-watchdog.sh
    chmod 755 /usr/local/bin/ipracticom-sweeper-watchdog.sh
    log "  /usr/local/bin/ipracticom-sweeper-watchdog.sh"
fi
systemctl daemon-reload

log "Step 5/6: Enabling + starting services..."
# sweeper.timer: periodic sweep every 5 min
systemctl enable ipracticom-sweeper.timer
systemctl restart ipracticom-sweeper.timer
# sweeper-api.service: long-running dashboard on :8787
systemctl enable ipracticom-sweeper-api.service
systemctl restart ipracticom-sweeper-api.service
# sweeper-watchdog.timer: external watchdog for the API (every 60s)
systemctl enable ipracticom-sweeper-watchdog.timer
systemctl restart ipracticom-sweeper-watchdog.timer

log "Step 6/6: Verifying..."
sleep 2
if ! systemctl is-active --quiet ipracticom-sweeper.timer; then
    fail "ipracticom-sweeper.timer failed to start"
fi
log "  ✓ timer active"
if ! systemctl is-active --quiet ipracticom-sweeper-api.service; then
    fail "ipracticom-sweeper-api.service failed to start"
fi
log "  ✓ API service active"
if ! systemctl is-active --quiet ipracticom-sweeper-watchdog.timer; then
    fail "ipracticom-sweeper-watchdog.timer failed to start"
fi
log "  ✓ watchdog timer active"

# Run once now so the user sees immediate output
log "Triggering initial sweep..."
systemctl start ipracticom-sweeper.service || log "(initial sweep exited non-zero — check journal)"

# Verify HTTP endpoint
sleep 1
if curl -sf http://127.0.0.1:8787/ >/dev/null 2>&1; then
    log "  ✓ dashboard responds on http://127.0.0.1:8787/"
elif curl -sf http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
    log "  ✓ healthz responds on http://127.0.0.1:8787/healthz"
else
    log "  ⚠️  API listening but root path not responding — check 'journalctl -u ipracticom-sweeper-api'"
fi

log ""
log "=== Installation complete ==="
log ""
log "Useful commands:"
log "  systemctl status ipracticom-sweeper.timer          # timer state"
log "  systemctl status ipracticom-sweeper.service        # last sweep run"
log "  systemctl status ipracticom-sweeper-api.service    # dashboard/API"
log "  journalctl -u ipracticom-sweeper -f                # follow sweep logs"
log "  journalctl -u ipracticom-sweeper-api -f            # follow API logs"
log "  systemctl list-timers ipracticom-sweeper           # next 5 runs"
log "  curl -s http://127.0.0.1:8787/                     # HTML dashboard"
log "  sudo bash $0 --uninstall [--purge]                 # remove"
