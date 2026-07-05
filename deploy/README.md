# iPracticom Sweeper — Claude Box Deployment

Install the iPracticom Sweeper on **Claude's box** (operator workstation) so it can monitor a fleet of AWS EC2 instances and accept Slack approvals.

Cross-platform: works on **Linux**, **macOS**, and **Windows (Git Bash / WSL)**. No sudo / root required.

## What gets installed

| Path | Purpose |
|---|---|
| `~/.ipracticom-sweeper/venv/` | Python venv with sweeper + supervisor |
| `~/.ipracticom-sweeper/config/` | env files + fleet.yaml + rules.yaml |
| `~/.ipracticom-sweeper/state/` | audit log + snapshots (replaces `/var/lib/`) |
| `~/.ipracticom-sweeper/logs/` | service stdout/stderr |
| `~/.ipracticom-sweeper/supervisor/` | supervisor config (3 services) |

The installer runs three services under `supervisord`:
- **sweeper-agent** — `agent_api` on port 8810 (for the dashboard's remote mode)
- **sweeper-dashboard** — `dashboard` on port 8804 (web UI)
- **sweeper-periodic** — `sweeper` CLI run on a 5-minute loop

Auto-start is registered via the OS-native mechanism:
- Linux → systemd `--user`
- macOS → launchd LaunchAgent
- Windows → Task Scheduler (on logon)

## Quick start

```bash
# 1. Install (interactive — will prompt for missing creds)
bash install_claude_box.sh

# 2. AWS setup (one-time, needs IAM admin on your AWS account)
bash setup_aws.sh

# 3. Slack setup (optional — only if you want Slack alerts)
bash setup_slack.sh

# 4. Start everything (if not already running)
bash start.sh

# 5. Open dashboard
open http://localhost:8804       # macOS
xdg-open http://localhost:8804    # Linux
start http://localhost:8804       # Windows
```

Default credentials (created in `--auto` mode):
- Dashboard: `admin` / `<random 20-char password>` — shown in install summary
- Agent API: random 64-char hex token — saved in `agent.env`

## Manual / non-interactive install

```bash
bash install_claude_box.sh --auto    # generate random creds
# OR
bash install_claude_box.sh --uninstall
```

## Files you need to edit

After install, edit these to match your environment:

| File | What to fill in |
|---|---|
| `config/dashboard.env` | DASHBOARD_USER / DASHBOARD_PASS |
| `config/agent.env` | AGENT_API_TOKEN, AWS credentials (from setup_aws.sh) |
| `config/notifications.env` | TELEGRAM_BOT_TOKEN, SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_CHANNEL_ID |
| `config/fleet.yaml` | EC2 tags for the fleet scanner |
| `config/repair_policy.yaml` | Which repairs run automatically vs require approval |

## Expose to the internet (optional)

The dashboard and agent API bind to 127.0.0.1 by default. To access from a browser outside this machine:

```bash
# Quick tunnel (URL changes every restart)
cloudflared tunnel --url http://localhost:8804
cloudflared tunnel --url http://localhost:8810

# Named tunnel (stable URL — needs one-time setup)
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
cloudflared tunnel run my-tunnel
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Claude's box (this machine)                                     │
│                                                                 │
│  supervisord                                                    │
│   ├── sweeper-agent      (Flask :8810, Bearer auth)             │
│   ├── sweeper-dashboard  (Flask :8804, Basic auth)              │
│   └── sweeper-periodic   (5-min sweep, no port)                 │
│                                                                 │
│  ~/.ipracticom-sweeper/                                         │
│   ├── venv/        Python 3.11+ with sweeper + supervisor       │
│   ├── config/      env files + fleet.yaml + rules.yaml          │
│   ├── state/       audit/ snapshots/                            │
│   └── logs/        service stdout/stderr                        │
└─────────────────────────────────────────────────────────────────┘
        ▲                                │
        │ HTTPS (SSM SendCommand)        │ HTTPS (Slack webhook)
        │                                │
┌───────┴────────────┐         ┌─────────┴───────────┐
│ AWS EC2 fleet      │         │ Slack               │
│ (no agent needed)  │         │ #sweeper-alerts     │
│ iPracticomSweeper  │         │ Approve / Silence   │
│ Role on each       │         │ Run Now buttons     │
└────────────────────┘         └─────────────────────┘
```

## Troubleshooting

**Q: `python3.11+ not found`**
- Linux: `sudo apt install python3.11 python3.11-venv`
- macOS: `brew install python@3.11` or `pyenv install 3.11`
- Windows: https://python.org/downloads/

**Q: `pip install -e .` fails with "externally-managed-environment"**
- The venv bypasses PEP 668. Make sure `$HOME/.ipracticom-sweeper/venv/bin/python -m pip install ...` is used (it is, by default).

**Q: Service won't start — port already in use**
- `lsof -i :8810` / `lsof -i :8804` — kill the conflicting process or change ports in supervisor conf.

**Q: systemd --user start failed**
- Some Linux distros require `loginctl enable-linger $USER` for user services to survive logout.

**Q: launchctl load failed**
- Check Console.app → system.log. Common cause: plist syntax error.

**Q: schtasks says "Access is denied"**
- Run from an elevated cmd.exe / PowerShell. Or change script to use Task Scheduler GUI.

## Re-running

`install_claude_box.sh` is **idempotent**: safe to re-run. Existing config files in `~/.ipracticom-sweeper/config/` are preserved.

To wipe and start over:
```bash
bash install_claude_box.sh --uninstall
rm -rf ~/.ipracticom-sweeper
bash install_claude_box.sh
```
