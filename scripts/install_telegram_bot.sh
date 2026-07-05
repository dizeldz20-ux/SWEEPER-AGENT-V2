#!/usr/bin/env bash
# iPracticom Sweeper — Telegram bot installer.
# Run after `bootstrap.sh` (which installs the agent and the package).
# Creates /etc/ipracticom-sweeper/telegram-bot.env with placeholder values,
# installs the systemd unit that runs `python -m ipracticom_sweeper.telegram_bot`,
# and prints the next-step checklist.
#
# Usage:
#   sudo bash install_telegram_bot.sh
#   sudo bash install_telegram_bot.sh --uninstall

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE_NAME="ipracticom-sweeper-telegram"
ENV_FILE="/etc/ipracticom-sweeper/telegram-bot.env"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# --- Colors ---
if [[ -t 1 ]]; then
    C_BLUE='\033[0;34m' C_GREEN='\033[0;32m' C_YELLOW='\033[1;33m' C_RED='\033[0;31m' C_RESET='\033[0m'
else
    C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_RESET=''
fi

log()  { printf "${C_BLUE}[tg-bot]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[tg-bot]${C_RESET} ✅ %s\n" "$*"; }
warn() { printf "${C_YELLOW}[tg-bot]${C_RESET} ⚠️  %s\n" "$*"; }
err()  { printf "${C_RED}[tg-bot]${C_RESET} ❌ %s\n" "$*"; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        err "Must run as root. Try: sudo bash $0"
        exit 1
    fi
}

# --- Uninstall ---
if [[ "${1:-}" == "--uninstall" ]]; then
    require_root
    log "Uninstalling ${SERVICE_NAME}..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
        rm -f "${SERVICE_FILE}"
        systemctl daemon-reload
        ok "systemd unit removed"
    fi
    warn "Env file preserved at ${ENV_FILE} (delete manually if desired)"
    exit 0
fi

require_root

# --- Create env file if missing ---
mkdir -p "$(dirname "${ENV_FILE}")"
if [[ ! -f "${ENV_FILE}" ]]; then
    cat > "${ENV_FILE}" <<'ENVEOF'
# Telegram bot configuration for iPracticom Sweeper
#
# 1. Create a bot with @BotFather on Telegram → /newbot → copy the token
# 2. Send /start to your bot, then to @userinfobot to get your chat_id
# 3. Fill in TELEGRAM_BOT_TOKEN and ALLOWED_CHAT_IDS below
# 4. Restart: sudo systemctl restart ipracticom-sweeper-telegram
#
# AGENT_API_TOKEN is the same token used by the main sweeper agent_api
# (see /etc/ipracticom-sweeper/agent.env or .env). Copy it here so the
# bot can call protected endpoints.

TELEGRAM_BOT_TOKEN=*** /placeholder/agent-token-here
ENVEOF
    chmod 600 "${ENV_FILE}"
    warn "Created ${ENV_FILE} — fill in TELEGRAM_BOT_TOKEN and ALLOWED_CHAT_IDS"
else
    log "Env file already exists at ${ENV_FILE}, leaving untouched"
fi

# --- Install systemd unit (if available) ---
if command -v systemctl >/dev/null 2>&1; then
    log "Installing systemd unit..."
    cat > "${SERVICE_FILE}" <<UNITEOF
[Unit]
Description=iPracticom Sweeper Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/python3 -m ipracticom_sweeper.telegram_bot
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNITEOF
    systemctl daemon-reload
    ok "systemd unit installed at ${SERVICE_FILE}"
    warn "Not started yet — fill in the env file and run:"
    echo "     sudo systemctl enable --now ${SERVICE_NAME}.service"
    echo "     sudo journalctl -u ${SERVICE_NAME}.service -f"
else
    warn "systemd not available — run manually:"
    echo "     source ${ENV_FILE} && cd ${REPO_DIR} && python3 -m ipracticom_sweeper.telegram_bot"
fi

cat <<EOF

${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}
  🤖 iPracticom Sweeper Telegram bot installed
${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}

  📝 Config:        ${ENV_FILE}
  ⚙️  Service:      ${SERVICE_NAME}

  👉 Next steps:
     1. Edit ${ENV_FILE} and set TELEGRAM_BOT_TOKEN + ALLOWED_CHAT_IDS
     2. Enable:   sudo systemctl enable --now ${SERVICE_NAME}.service
     3. Logs:     sudo journalctl -u ${SERVICE_NAME}.service -f
     4. Test:     Open Telegram, send /start to your bot
${C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}
EOF
