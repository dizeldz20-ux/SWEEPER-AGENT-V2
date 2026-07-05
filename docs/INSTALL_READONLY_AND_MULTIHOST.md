# iPracticom Sweeper — READ-ONLY Mode & Multi-Host Setup Guide
**For:** Daniel Maimon (Hermes provider)
**Audience:** A new Hermes agent that will deploy the iPracticom Sweeper on 3 Linux VMs (1 controller + 2 monitored).
**Goal:** Monitor + report only. No auto-repair. Connect to other hosts over SSH keys with command restrictions.

---

## § 1 — What changed: Read-Only mode

A new env var `IPRACTICOM_SWEEPER_READONLY=1` forces the pipeline to:
- Skip ALL repair actions, even SAFE/GUARDED ones
- Still emit Slack/Telegram notifications
- Add `"readonly": true` to every pipeline result so the dashboard can show a banner
- The `/api/run` endpoint respects it too (admin trigger does NOT bypass readonly)

**Why this matters for the test fleet:** when we inject faults into the 2 monitored VMs (kill nginx, fill disk, OOM), we want to see the agent detect and report — not fix. Fixing would mask whether the detection works.

**Two ways to enable:**

| Method | When to use |
|---|---|
| Env var `IPRACTICOM_SWEEPER_READONLY=1` | Recommended for prod-like setup; works for CLI, agent_api, and dashboard |
| CLI flag `--no-repair` | For ad-hoc one-shot runs |

The env var is the source of truth. The flag is a manual override.

---

## § 2 — Installation on a Linux VM (Hermes-managed)

These instructions are written for the Hermes agent that will execute them. **Do NOT install on a machine you don't own.** Verify each command's exit code before moving on.

### § 2.1 — Pre-flight checks (must all pass)

```bash
# 1. We are root
[ "$(id -u)" -eq 0 ] || { echo "FAIL: must be root"; exit 1; }

# 2. systemd exists (this is a server, not a container)
systemctl --version | head -1

# 3. Python 3.10+
python3 --version

# 4. pip available for system python
python3 -m pip --version

# 5. Network reachability to the iPracticom repo (HTTPS)
curl -sSI https://github.com/iPracticom/ipracticom-sweeper | head -1
```

If any fail → STOP. Report to the operator. Do not improvise.

### § 2.2 — Install the package

```bash
# Clone to a stable location
git clone https://github.com/iPracticom/ipracticom-sweeper.git /opt/ipracticom-sweeper
cd /opt/ipracticom-sweeper

# Pin to v0.3.0 (the version this skill was tested against)
git checkout v0.3.0

# Site-wide install (no venv — matches the install-systemd.sh pattern)
python3 -m pip install -e . --break-system-packages --quiet

# Verify the CLI is callable
python3 -m ipracticom_sweeper.sweeper --help | head -5
```

### § 2.3 — Configure READ-ONLY mode

Create `/etc/ipracticom-sweeper/environment`:

```bash
mkdir -p /etc/ipracticom-sweeper
cat > /etc/ipracticom-sweeper/environment <<'EOF'
# iPracticom Sweeper — host environment
# READ-ONLY mode: detect and report, never fix.
IPRACTICOM_SWEEPER_READONLY=1

# Server identity (auto-detected from IMDSv2 if empty; override for naming)
# IPRACTICOM_SERVER_ID=ipracticom-test-vm-01

# Notification channels (use test channels — NEVER prod during fault injection)
SLACK_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Agent API token (required if binding to non-loopback)
AGENT_API_TOKEN=$(openssl rand -hex 32)
EOF

chmod 600 /etc/ipracticom-sweeper/environment
```

### § 2.4 — Install + enable systemd service

```bash
# Use the bundled installer (handles everything: dirs, units, enable, start)
sudo bash /opt/ipracticom-sweeper/scripts/install-systemd.sh

# Verify the timer is active (runs every 5 min)
systemctl is-active ipracticom-sweeper.timer

# Verify the env var is loaded by the service
systemctl show ipracticom-sweeper.service | grep -i environment

# Trigger an initial run and watch the output
journalctl -u ipracticom-sweeper -n 30 --no-pager
```

### § 2.5 — Verify read-only is in effect

```bash
# Should contain "readonly": true
python3 -m ipracticom_sweeper.sweeper --json | jq '.readonly, .defcon, .defcon_label'

# Should NOT change anything on disk even after 10 minutes
systemctl status ipracticom-sweeper.service --no-pager
```

### § 2.6 — Start the agent API (for remote control)

```bash
# Create a systemd unit for the API (not bundled in v0.3.0; one-shot)
cat > /etc/systemd/system/ipracticom-sweeper-api.service <<'EOF'
[Unit]
Description=iPracticom Sweeper Agent API
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/ipracticom-sweeper/environment
ExecStart=/usr/bin/python3 -m ipracticom_sweeper.agent_api \
    --host 127.0.0.1 --port 8810
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ipracticom-sweeper-api.service

# Verify it's up
curl -sS http://127.0.0.1:8810/healthz | jq .
```

---

## § 3 — Multi-host monitoring over SSH (test fleet pattern)

**Topology:**

```
┌──────────────────────────────────────────┐
│  VM-1 (controller)                        │
│  Hermes Agent + iPracticom Sweeper       │
│  /etc/ssh/ssh_keys/id_monitor            │
│  ────────────────────────► ssh         ──┼──► VM-2 (target)
│                                          ─┼──► VM-3 (target)
└──────────────────────────────────────────┘
```

**Security model:** The controller VM holds a dedicated SSH key (`id_monitor`). Each target VM has the public key in a restricted `authorized_keys` entry that:
- Pins source IP (controller's LAN IP)
- Pins the exact command the key may invoke (one command only — a read-only monitor script)
- Disables all forwarding / tunneling

This is the **same pattern AWS uses for SSM agent invocation** — least-privilege per-key. SSH key + `command=` + `from=` is the on-prem equivalent.

### § 3.1 — On each TARGET VM (VM-2, VM-3)

Create a dedicated low-privilege user the monitor will log in as:

```bash
# Create the user with no shell, no home (we won't log in interactively)
useradd -r -s /usr/sbin/nologin -M monitor

# Create a directory for the public key
install -d -m 755 -o monitor -g monitor /etc/ssh/monitor_authorized
```

### § 3.2 — On each TARGET VM: write the restricted authorized_keys

The public key goes here, NOT in the user's home (since the user has no home):

```bash
cat > /etc/ssh/monitor_authorized/authorized_keys <<'EOF'
from="10.x.x.5,127.0.0.1",command="/usr/local/bin/sweeper-remote-readonly.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,expiry-time="20261231T235959Z" ssh-ed25519 AAAA...CONTROLLER_PUBLIC_KEY... monitor@controller
EOF

chmod 644 /etc/ssh/monitor_authorized/authorized_keys
chown -R monitor:monitor /etc/ssh/monitor_authorized
```

Wire it into sshd:

```bash
cat > /etc/ssh/sshd_config.d/99-monitor.conf <<'EOF'
Match User monitor
    AuthorizedKeysFile /etc/ssh/monitor_authorized/authorized_keys
    PermitTTY no
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
EOF

systemctl reload ssh
```

### § 3.3 — On each TARGET VM: the read-only monitor script

This script is the **only** thing the key may invoke. sshd ignores anything else.

```bash
cat > /usr/local/bin/sweeper-remote-readonly.sh <<'EOF'
#!/bin/bash
# iPracticom Sweeper — remote read-only monitor
# Invoked by sshd on the target via the restricted key.
# Returns JSON on stdout. NEVER writes to disk. NEVER kills anything.

set -euo pipefail

echo "{\"host\":\"$(hostname -f)\",\"ts\":\"$(date -u +%FT%TZ)\",\"checks\":{"

first=1
emit() {
    [ $first -eq 0 ] && echo "," || first=0
    echo -n "$1"
}

# CPU load (1-min avg, read-only)
load=$(awk '{print $1}' /proc/loadavg)
emit "\"load1\":${load}"

# Memory (used %, read-only from /proc/meminfo)
mem_total=$(awk '/MemTotal/{print $2}' /proc/meminfo)
mem_avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
mem_used_pct=$(awk -v t=$mem_total -v a=$mem_avail 'BEGIN{printf "%.1f", (t-a)*100/t}')
emit "\"mem_used_pct\":${mem_used_pct}"

# Disk root (read-only df)
disk_pct=$(df / --output=pcent | tail -1 | tr -dc '0-9.')
emit "\"disk_root_pct\":${disk_pct}"

# Listening services count (read-only)
listening=$(ss -tln | tail -n +2 | wc -l)
emit "\"listening_count\":${listening}"

# Top 5 CPU consumers (read-only ps)
top_procs=$(ps -eo comm,%cpu --sort=-%cpu | head -6 | tail -5 | awk '{printf "\\"%s\\":%s,", $1, $2}' | sed 's/,$//')
emit "\"top_procs\":{${top_procs}}}"
EOF

chmod 755 /usr/local/bin/sweeper-remote-readonly.sh
```

### § 3.4 — On the CONTROLLER VM (VM-1)

Generate the keypair and copy the private key to the sweeper's config dir:

```bash
# Generate an ed25519 key (no passphrase — it's machine identity)
ssh-keygen -t ed25519 -N "" -C "sweeper-monitor@$(hostname -s)" \
    -f /etc/ipracticom-sweeper/ssh/id_monitor

# Lock down permissions
chmod 700 /etc/ipracticom-sweeper/ssh
chmod 600 /etc/ipracticom-sweeper/ssh/id_monitor

# Copy the PUBLIC key into § 3.2 above (manually paste)
cat /etc/ipracticom-sweeper/ssh/id_monitor.pub
```

### § 3.5 — On the CONTROLLER VM: configure the fleet

`/etc/ipracticom-sweeper/fleet.yaml`:

```yaml
hosts:
  - name: target-vm-2
    host: 10.x.x.6
    ssh_user: monitor
    ssh_key: /etc/ipracticom-sweeper/ssh/id_monitor
    ssh_port: 22
  - name: target-vm-3
    host: 10.x.x.7
    ssh_user: monitor
    ssh_key: /etc/ipracticom-sweeper/ssh/id_monitor
    ssh_port: 22

poll_interval_seconds: 60
readonly: true   # belt-and-suspenders: also disable local repair when polling remote
```

---

## § 4 — Failure injection (for the Hermes agent's tests)

**Approved faults (no systemd required, all reversible):**

```bash
# CPU spike — spawn 4 CPU-bound workers for 5 minutes
for i in 1 2 3 4; do (timeout 300 yes > /dev/null) & done

# Memory pressure — allocate 80% of RAM and hold for 5 minutes
python3 -c "
import ctypes, time
libc = ctypes.CDLL('libc.so.6')
size = int(open('/proc/meminfo').read().split('MemTotal:')[1].split()[0]) * 1024 * 4 // 5
buf = (ctypes.c_char * size)()
time.sleep(300)
"

# Disk fill — create a 5GB sparse file
truncate -s 5G /tmp/fillfile && sync
# To unfill:
rm /tmp/fillfile

# Service kill — pick a non-critical service to restart
sudo systemctl restart cron   # safe; ifdown-able

# Network — drop 50% of outgoing packets for 60s (requires root + iptables)
sudo iptables -A OUTPUT -m statistic --mode random --probability 0.5 -j DROP
sleep 60 && sudo iptables -D OUTPUT -m statistic --mode random --probability 0.5 -j DROP
```

**Each fault must:**
1. Be announced to the operator BEFORE injection (Slack message with start time)
2. Have a known cleanup procedure (provided in the announcement)
3. Be on a target VM, NEVER the controller

---

## § 5 — Verification checklist (Hermes must complete before reporting done)

| # | Check | Command | Pass criterion |
|---|---|---|---|
| 1 | Package installed | `python3 -m ipracticom_sweeper.sweeper --help` | exit 0, help text shown |
| 2 | READONLY mode set | `python3 -m ipracticom_sweeper.sweeper --json \| jq '.readonly'` | `true` |
| 3 | Timer active | `systemctl is-active ipracticom-sweeper.timer` | `active` |
| 4 | Last run OK | `systemctl status ipracticom-sweeper.service` | `Active: inactive (dead)` with `Result: success` |
| 5 | Agent API listening | `curl -sS http://127.0.0.1:8810/healthz \| jq .ok` | `true` |
| 6 | Auth gate works | `curl -sS http://127.0.0.1:8810/api/snapshot` | HTTP 401 |
| 7 | Auth+token works | `curl -sS -H "Authorization: Bearer $AGENT_API_TOKEN" http://127.0.0.1:8810/api/snapshot \| jq '.defcon'` | integer 1-5 |
| 8 | SSH to target works | `ssh -i /etc/ipracticom-sweeper/ssh/id_monitor -o BatchMode=yes monitor@10.x.x.6` | JSON output, exit 0 |
| 9 | SSH restricted command works | `ssh ... monitor@10.x.x.6 "echo hi"` | command IGNORED, JSON returned |
| 10 | Fault detection works | inject § 4 fault, wait 1 cycle | DEFCON < 5 in next snapshot |

**If any check fails → STOP. Do not improvise. Report exact step + exit code to operator.**

---

## § 6 — Rollback

```bash
# Disable + remove the sweeper
sudo bash /opt/ipracticom-sweeper/scripts/install-systemd.sh --uninstall
sudo systemctl disable --now ipracticom-sweeper-api.service
rm /etc/systemd/system/ipracticom-sweeper-api.service
sudo rm -rf /etc/ipracticom-sweeper /opt/ipracticom-sweeper
sudo rm -rf /var/lib/ipracticom-sweeper  # only after forensics are done
```

On each target:
```bash
sudo rm /etc/ssh/sshd_config.d/99-monitor.conf
sudo systemctl reload ssh
sudo userdel monitor
sudo rm /usr/local/bin/sweeper-remote-readonly.sh
```

---

## § 7 — What to tell the operator when done

Report back with:
1. ✓ or ✗ for each row of § 5 checklist
2. The exact `jq .readonly` value
3. The output of `journalctl -u ipracticom-sweeper -n 5 --no-pager`
4. Any warnings (even minor — they matter during fault injection)
5. **NEVER** declare success without exit codes from each command