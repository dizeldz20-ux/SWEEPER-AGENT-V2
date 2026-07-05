# Install services under supervisor and register auto-start for the OS.
#
# This is the cross-platform replacement for systemd units. supervisor
# (the Python package) runs as a single daemon that supervises all 3
# sweeper services — agent_api, dashboard, periodic — and restarts them
# on crash. Auto-start registration is OS-specific:
#
#   Linux   → systemd --user (works even without sudo, in user mode)
#   macOS   → launchd LaunchAgent (~/Library/LaunchAgents/)
#   Windows → Task Scheduler (schtasks)
#
# Args:
#   $1  OS_FAMILY (linux | macos | windows)
#   $2  VENV_DIR
#   $3  CONFIG_DIR
#   $4  STATE_DIR
#   $5  LOG_DIR
#   $6  SUPERVISOR_DIR

service_install() {
    local os_family="$1"
    local venv_dir="$2"
    local config_dir="$3"
    local state_dir="$4"
    local log_dir="$5"
    local sup_dir="$6"

    mkdir -p "$sup_dir/conf.d" "$sup_dir/run" "$sup_dir/log" "$log_dir"

    # --- supervisor.conf -------------------------------------------
    cat > "$sup_dir/sweeper.conf" <<EOF
; supervisor config for iPracticom Sweeper — managed by deploy/scripts/install_claude_box.sh
; Do not edit by hand — re-run the installer instead.

[unix_http_server]
file=$sup_dir/run/supervisor.sock
chmod=0700

[supervisord]
logfile=$log_dir/supervisord.log
logfile_maxbytes=10MB
logfile_backups=5
loglevel=info
pidfile=$sup_dir/run/supervisord.pid
nodaemon=false
minfds=1024
minprocs=200
environment=PYTHONUNBUFFERED="1"

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://$sup_dir/run/supervisor.sock

[include]
files = $sup_dir/conf.d/*.conf
EOF

    # --- Three service definitions ---------------------------------
    # Each service:
    #   - sources its config .env
    #   - sets state + log dirs to user home (no /var/lib)
    #   - restart=on-failure (3 attempts with backoff)
    #   - stdout/stderr → logs/

    write_service_conf() {
        local name="$1"
        local cmd="$2"
        cat > "$sup_dir/conf.d/${name}.conf" <<EOF
[program:${name}]
command=$venv_dir/bin/bash -c 'set -a; . $config_dir/dashboard.env; . $config_dir/agent.env; . $config_dir/notifications.env; export DASHBOARD_USER DASHBOARD_PASS AGENT_API_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID SLACK_BOT_TOKEN SLACK_SIGNING_SECRET SLACK_CHANNEL_ID IPRACTICOM_SWEEPER_STATE_DIR=$state_dir; exec $venv_dir/bin/$cmd'
directory=$state_dir
autostart=true
autorestart=true
startretries=3
stopwaitsecs=10
stopsignal=TERM
stdout_logfile=$log_dir/${name}.out.log
stderr_logfile=$log_dir/${name}.err.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile_backups=5
environment=PYTHONPATH="$REPO_DIR/src",PYTHONUNBUFFERED="1",IPRACTICOM_SWEEPER_STATE_DIR="$state_dir"
EOF
    }

    write_service_conf "sweeper-agent" "python3 -m ipracticom_sweeper.agent_api --port 8810 --host 127.0.0.1"
    write_service_conf "sweeper-dashboard" "python3 -m ipracticom_sweeper.dashboard --port 8804 --host 127.0.0.1"
    write_service_conf "sweeper-periodic" "python3 -m ipracticom_sweeper.sweeper --json --quiet"

    # --- Auto-start registration (OS-specific) --------------------
    case "$os_family" in
        linux)   service_register_linux   "$sup_dir" "$log_dir" ;;
        macos)   service_register_macos   "$sup_dir" "$log_dir" ;;
        windows) service_register_windows "$sup_dir" "$log_dir" ;;
        *)       echo "❌ unknown OS_FAMILY: $os_family" >&2; return 1 ;;
    esac
}

# --- OS-specific auto-start ------------------------------------------------

service_register_linux() {
    local sup_dir="$1"
    local log_dir="$2"
    local user_dir="$HOME/.config/systemd/user"
    mkdir -p "$user_dir"

    cat > "$user_dir/ipracticom-sweeper.service" <<EOF
[Unit]
Description=iPracticom Sweeper supervisor (Claude box)
After=network-online.target

[Service]
Type=simple
ExecStart=$sup_dir/../venv/bin/supervisord -c $sup_dir/sweeper.conf
ExecStop=$sup_dir/../venv/bin/supervisorctl -c $sup_dir/sweeper.conf shutdown
Restart=on-failure
RestartSec=5
StandardOutput=append:$log_dir/systemd-user.out.log
StandardError=append:$log_dir/systemd-user.err.log

[Install]
WantedBy=default.target
EOF

    # Enable + start (no sudo needed because of --user)
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable ipracticom-sweeper.service 2>/dev/null || true
    systemctl --user start ipracticom-sweeper.service 2>/dev/null || {
        echo "⚠️  [service_register_linux] systemd --user start failed — start manually:" >&2
        echo "     supervisord -c $sup_dir/sweeper.conf" >&2
        return 0  # don't fail install — user can start later
    }
    echo "  → systemd --user enabled + started"
}

service_register_macos() {
    local sup_dir="$1"
    local log_dir="$2"
    local label="com.ipracticom.sweeper"
    local plist="$HOME/Library/LaunchAgents/${label}.plist"

    mkdir -p "$(dirname "$plist")"

    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>$sup_dir/../venv/bin/supervisord</string>
        <string>-c</string>
        <string>$sup_dir/sweeper.conf</string>
        <string>--nodaemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>StandardOutPath</key>
    <string>${log_dir}/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${log_dir}/launchd.err.log</string>
    <key>WorkingDirectory</key>
    <string>${HOME}</string>
</dict>
</plist>
EOF

    # Unload any existing version, then load fresh
    launchctl unload "$plist" 2>/dev/null || true
    if launchctl load "$plist" 2>/dev/null; then
        echo "  → launchctl loaded $plist"
    else
        echo "⚠️  [service_register_macos] launchctl load failed — start manually:" >&2
        echo "     supervisord -c $sup_dir/sweeper.conf" >&2
        return 0
    fi
}

service_register_windows() {
    local sup_dir="$1"
    local log_dir="$2"
    local venv_bin
    venv_bin="$(cd "$sup_dir/../venv" && pwd -W 2>/dev/null || cd "$sup_dir/../venv" && pwd)"
    # Git-bash maps paths; convert to Windows-style for schtasks
    local venv_supervisord
    venv_supervisord="${venv_bin//\//\\}\\Scripts\\supervisord.exe"
    local conf_path
    conf_path="$(cd "$sup_dir" && pwd -W 2>/dev/null || pwd)/sweeper.conf"

    # Create a .bat wrapper because schtasks needs an .exe or .bat
    local bat="$sup_dir/run/start-supervisord.bat"
    mkdir -p "$(dirname "$bat")"
    cat > "$bat" <<EOF
@echo off
cd /d "$(cygpath -w "$sup_dir/..")"
"venv\\Scripts\\supervisord.exe" -c "$conf_path"
EOF

    # Register in Task Scheduler (runs at logon)
    schtasks //Create //SC ONLOGON //TN "iPracticomSweeper" \
        //TR "$(cygpath -w "$bat")" //F 2>/dev/null || {
        echo "⚠️  [service_register_windows] schtasks failed — start manually:" >&2
        echo "     $bat" >&2
        return 0
    }
    schtasks //Run //TN "iPracticomSweeper" 2>/dev/null || true
    echo "  → Task Scheduler registered (iPracticomSweeper, on logon)"
}

service_uninstall() {
    local os_family="$1"
    case "$os_family" in
        linux)
            systemctl --user disable ipracticom-sweeper.service 2>/dev/null || true
            systemctl --user stop ipracticom-sweeper.service 2>/dev/null || true
            rm -f "$HOME/.config/systemd/user/ipracticom-sweeper.service"
            systemctl --user daemon-reload 2>/dev/null || true
            ;;
        macos)
            local plist="$HOME/Library/LaunchAgents/com.ipracticom.sweeper.plist"
            launchctl unload "$plist" 2>/dev/null || true
            rm -f "$plist"
            ;;
        windows)
            schtasks //Delete //TN "iPracticomSweeper" //F 2>/dev/null || true
            ;;
    esac
}
