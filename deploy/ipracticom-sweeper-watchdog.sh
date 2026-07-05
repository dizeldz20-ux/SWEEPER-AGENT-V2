#!/usr/bin/env bash
# iPracticom Sweeper watchdog helper — runs every 60s via systemd timer.
# Probes /healthz; restarts ipracticom-sweeper-api on 5xx/timeout;
# alerts admin on 3+ restarts/hour.
set -euo pipefail

HEALTHZ_URL="${WATCHDOG_HEALTHZ_URL:-http://127.0.0.1:8787/healthz}"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/ipracticom-sweeper}"
COOLDOWN_SECONDS="${WATCHDOG_COOLDOWN:-300}"
WINDOW_SECONDS="${WATCHDOG_WINDOW:-3600}"
MAX_RESTARTS="${WATCHDOG_MAX_RESTARTS:-3}"
SERVICE_NAME="${WATCHDOG_SERVICE:-ipracticom-sweeper-api.service}"

CODE=$(curl --silent --max-time 5 -o /dev/null -w '%{http_code}' "$HEALTHZ_URL" || echo "000")

if [[ "$CODE" == "200" ]]; then
    exit 0
fi

# 4xx is not our fault (auth, misconfigured reverse proxy). Skip.
if [[ "$CODE" =~ ^4 ]]; then
    exit 0
fi

mkdir -p "$STATE_DIR"
RESTART_FILE="$STATE_DIR/watchdog_restarts.json"

NOW=$(date +%s)
if [[ -f "$RESTART_FILE" ]]; then
    LAST=$(python3 - <<EOF
import json
try:
    data = json.load(open("$RESTART_FILE"))
    r = data.get("restarts", [])
    print(int(max(r)) if r else 0)
except Exception:
    print(0)
EOF
)
    ELAPSED=$((NOW - LAST))
    if (( ELAPSED < COOLDOWN_SECONDS )); then
        exit 0
    fi
fi

systemctl restart "$SERVICE_NAME"
NEW_TS=$(date +%s)

WINDOW="$WINDOW_SECONDS" python3 - <<EOF
import json
from datetime import datetime, timezone
path = "$RESTART_FILE"
window = int(__import__("os").environ["WINDOW"])
new_ts = "$NEW_TS"
prior = []
try:
    prior = json.load(open(path)).get("restarts", [])
except Exception:
    pass
prior.append(new_ts)
now_ts = float(new_ts)
prior = [t for t in prior if (now_ts - float(t)) < window]
json.dump({"restarts": prior}, open(path, "w"))
EOF

COUNT=$(python3 -c "import json; print(len(json.load(open('$RESTART_FILE'))['restarts']))")
if (( COUNT >= MAX_RESTARTS )); then
    "${SWEEPER_NOTIFY_BIN:-ipracticom-sweeper}" notify \
        --channel telegram --severity critical \
        --summary "Sweeper restart storm: $COUNT restarts in last hour ($SERVICE_NAME)" \
        || true
fi

exit 0