# Initialize ~/.ipracticom-sweeper/config/ with template files.
# In --auto mode, generate random tokens; in interactive mode, leave for user.
#
# Sets file modes to 600 for secrets, 644 for non-secrets.
#
# Files created:
#   dashboard.env     Basic auth creds for the dashboard (Basic Auth)
#   agent.env         Bearer token for the agent API
#   notifications.env Telegram + Slack creds (user fills in)
#   fleet.yaml        EC2 tag filters for the fleet connector
#   rules.yaml        Local sweeper rules (copied from repo defaults)

config_init() {
    local config_dir="$1"
    local mode="$2"

    mkdir -p "$config_dir"
    chmod 700 "$config_dir"

    # --- dashboard.env -----------------------------------------------
    local dash_user="admin"
    local dash_pass
    if [[ "$mode" == "auto" ]]; then
        dash_pass="$(openssl rand -base64 18 | tr -d '+/=' | head -c 20)"
    else
        dash_pass="changeme-please"
    fi
    cat > "$config_dir/dashboard.env" <<EOF
# HTTP Basic Auth for the dashboard (browser prompt).
# In --auto mode a random password was generated for you.
DASHBOARD_USER=$dash_user
DASHBOARD_PASS=$dash_pass
EOF
    chmod 600 "$config_dir/dashboard.env"

    # --- agent.env ---------------------------------------------------
    local agent_token
    if [[ "$mode" == "auto" ]]; then
        agent_token="$(openssl rand -hex 32)"
    else
        agent_token="changeme-please"
    fi
    cat > "$config_dir/agent.env" <<EOF
# Bearer token for the agent API (programmatic access).
# Required for the dashboard's "remote mode" to talk to this agent.
AGENT_API_TOKEN=$agent_token
EOF
    chmod 600 "$config_dir/agent.env"

    # --- notifications.env ------------------------------------------
    cat > "$config_dir/notifications.env" <<EOF
# Telegram (optional) — create a bot via @BotFather, paste token + chat_id.
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=

# Slack (optional) — Slack App with bot token + signing secret.
# SLACK_BOT_TOKEN=xoxb-...        (from "OAuth & Permissions" page)
# SLACK_SIGNING_SECRET=...        (from "Basic Information" → "App Credentials")
# SLACK_CHANNEL_ID=C...           (channel to post alerts to)
EOF
    chmod 600 "$config_dir/notifications.env"

    # --- fleet.yaml --------------------------------------------------
    cat > "$config_dir/fleet.yaml" <<'EOF'
# EC2 instances the fleet connector scans via AWS Systems Manager.
#
# Two ways to define the fleet (use ONE of them):
#
#   Option A — tag-based (queries EC2 on every run):
#     tags:
#       env: [prod, staging]
#       team: [infra, backend]
#
#   Option B — explicit instance IDs:
#     instance_ids:
#       - i-0123456789abcdef0
#       - i-0fedcba9876543210
#
# Tags match as: instance has (env=prod OR env=staging) AND team=infra

tags:
  env: [prod]
  team: [infra]
EOF

    # --- rules.yaml --------------------------------------------------
    if [[ -f "$REPO_DIR/rules/default.yaml" ]]; then
        cp "$REPO_DIR/rules/default.yaml" "$config_dir/rules.yaml"
    else
        cat > "$config_dir/rules.yaml" <<'EOF'
# Default sweeper rules (thresholds for warnings).
cpu:
  load_avg_5min_warn: 2.0
  load_avg_5min_crit: 5.0
  iowait_percent_warn: 20.0
memory:
  used_percent_warn: 80.0
  used_percent_crit: 95.0
  swap_used_percent_warn: 50.0
disk:
  used_percent_warn: 80.0
  used_percent_crit: 95.0
  read_only_mounts: []
services:
  critical_list: []
security:
  failed_ssh_per_min_warn: 5
  failed_ssh_per_min_crit: 20
EOF
    fi

    # --- repair_policy.yaml (auto vs approval) -----------------------
    cat > "$config_dir/repair_policy.yaml" <<'EOF'
# Which repairs run automatically vs require operator approval.
# auto        → run immediately, log to audit/repairs.jsonl
# needs_approval → write a proposal, wait for Slack button or dashboard click
auto:
  - drop_caches
  - log_truncate_journald
  - top_processes_snapshot
  - notify_human
needs_approval:
  - service_restart
  - rotate_logs
EOF
}
