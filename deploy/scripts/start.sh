#!/usr/bin/env bash
# Start all 3 sweeper services via supervisor.
#
# Usage:
#   bash start.sh                 # start all
#   bash start.sh --foreground    # run supervisord in foreground (Ctrl-C to stop)
#   bash start.sh --status        # show what's running

set -euo pipefail

SWEEPER_HOME="${IPRACTICOM_HOME:-$HOME/.ipracticom-sweeper}"
SUP_CONF="$SWEEPER_HOME/supervisor/sweeper.conf"
VENV="$SWEEPER_HOME/venv"

if [[ ! -f "$SUP_CONF" ]]; then
    echo "❌ no supervisor config at $SUP_CONF — run install_claude_box.sh first" >&2
    exit 1
fi

SUPERVISORD="$VENV/bin/supervisord"
SUPERVISORCTL="$VENV/bin/supervisorctl"

case "${1:-start}" in
    --status|status)
        "$SUPERVISORCTL" -c "$SUP_CONF" status
        ;;
    --foreground|fg)
        echo "Starting supervisord in foreground (Ctrl-C to stop)..."
        exec "$SUPERVISORD" -c "$SUP_CONF" --nodaemon=false
        ;;
    restart)
        "$SUPERVISORCTL" -c "$SUP_CONF" restart all
        "$SUPERVISORCTL" -c "$SUP_CONF" status
        ;;
    stop)
        "$SUPERVISORCTL" -c "$SUP_CONF" stop all
        ;;
    start|*)
        if "$SUPERVISORCTL" -c "$SUP_CONF" status >/dev/null 2>&1; then
            echo "supervisord already running — reloading config"
            "$SUPERVISORCTL" -c "$SUP_CONF" reread
            "$SUPERVISORCTL" -c "$SUP_CONF" update
            "$SUPERVISORCTL" -c "$SUP_CONF" start all 2>/dev/null || true
        else
            echo "Starting supervisord..."
            "$SUPERVISORD" -c "$SUP_CONF"
            sleep 2
        fi
        "$SUPERVISORCTL" -c "$SUP_CONF" status
        ;;
esac
