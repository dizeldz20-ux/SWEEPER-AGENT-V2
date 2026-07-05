#!/usr/bin/env bash
# iPracticom Sweeper — Slack setup helper.
#
# Verifies Slack credentials and tells you the exact Event Subscription URL
# to paste into your Slack App config. Read-only — does NOT modify Slack.
#
# Usage:
#   bash setup_slack.sh
#
# Requires:
#   - SLACK_BOT_TOKEN (xoxb-...) and SLACK_SIGNING_SECRET already set,
#     either in env or in ~/.ipracticom-sweeper/config/notifications.env

set -euo pipefail

CONFIG_DIR="${IPRACTICOM_CONFIG_DIR:-$HOME/.ipracticom-sweeper/config}"
NOTIF_ENV="$CONFIG_DIR/notifications.env"

C_BLUE='\033[0;34m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { printf "${C_BLUE}[slack]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[slack]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[slack]${C_RESET} ⚠️  %s\n" "$*" >&2; }
err()  { printf "${C_RED}[slack]${C_RESET} ❌ %s\n" "$*" >&2; }

# --- Load creds -----------------------------------------------------------
if [[ -f "$NOTIF_ENV" ]]; then
    set -a; . "$NOTIF_ENV"; set +a
fi

if [[ -z "${SLACK_BOT_TOKEN:-}" ]] || [[ -z "${SLACK_SIGNING_SECRET:-}" ]]; then
    err "SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set."
    echo "" >&2
    echo "  Edit $NOTIF_ENV (mode 600) and re-run." >&2
    exit 1
fi

# --- Verify token ---------------------------------------------------------
log "Verifying bot token via Slack auth.test..."
AUTH_RESP="$(curl -fsS -X POST https://slack.com/api/auth.test \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" 2>&1)" || {
    err "Slack API call failed (no internet?)"
    exit 1
}
OK="$(echo "$AUTH_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("ok"))')"
if [[ "$OK" != "True" ]]; then
    err "auth.test failed:"
    echo "$AUTH_RESP" | python3 -m json.tool >&2
    exit 1
fi
TEAM="$(echo "$AUTH_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["team"])')"
USER="$(echo "$AUTH_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["user"])')"
ok "token valid — team: $TEAM, bot user: $USER"

# --- Verify signing secret format ----------------------------------------
# Slack signing secrets are exactly 32 hex chars (older format was longer).
SECRET_LEN="${#SLACK_SIGNING_SECRET}"
if [[ "$SECRET_LEN" -lt 32 ]]; then
    warn "signing secret looks short (${SECRET_LEN} chars). Slack secrets are usually 32+ hex chars."
else
    ok "signing secret looks valid ($SECRET_LEN chars)"
fi

# --- Detect what the Event Subscription URL should be --------------------
log "Detecting public URL for the agent API..."
PUBLIC_URL=""
# 1. Check Cloudflare quick tunnel log (if running)
if [[ -r /tmp/cf_agent_api.log ]]; then
    PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cf_agent_api.log | head -1 || true)"
fi
# 2. Check env override
if [[ -z "$PUBLIC_URL" ]] && [[ -n "${AGENT_PUBLIC_URL:-}" ]]; then
    PUBLIC_URL="$AGENT_PUBLIC_URL"
fi
# 3. Check Cloudflare config
if [[ -z "$PUBLIC_URL" ]] && [[ -r "$HOME/.cloudflared/config.yml" ]]; then
    PUBLIC_URL="$(grep -E '^\s*url:' "$HOME/.cloudflared/config.yml" | awk '{print $2}' | head -1 || true)"
fi

if [[ -z "$PUBLIC_URL" ]]; then
    warn "could not detect public URL — you'll need to enter it manually"
    echo "  Start a tunnel with:" >&2
    echo "    cloudflared tunnel --url http://localhost:8810" >&2
    echo "  Then re-run this script." >&2
else
    ok "public URL detected: $PUBLIC_URL"
fi

# --- Print summary --------------------------------------------------------
echo
ok "Slack setup looks good!"
echo
echo "Next steps (manual, in Slack App config at https://api.slack.com/apps):"
echo
echo "  1. Event Subscriptions → Enable Events → Request URL:"
if [[ -n "$PUBLIC_URL" ]]; then
    echo "       ${PUBLIC_URL}/slack/events"
else
    echo "       <your-public-agent-url>/slack/events"
fi
echo
echo "  2. Subscribe to bot events:"
echo "       - message.channels   (read messages in channels)"
echo "       - message.im         (read DMs to the bot)"
echo
echo "  3. OAuth & Permissions → Bot Token Scopes:"
echo "       - chat:write         (send messages)"
echo "       - chat:write.public  (post to channels bot isn't in)"
echo "       - commands           (if you add slash commands later)"
echo
echo "  4. Interactivity → Request URL (same as Event Subscriptions URL above)"
echo
echo "  5. Install the app to your workspace and invite the bot to your channel:"
echo "       /invite @iPracticomSweeper #your-channel"
echo
