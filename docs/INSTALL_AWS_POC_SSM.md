# iPracticom Sweeper — AWS POC Deployment (READ-ONLY, SSM-based)
**For:** Daniel Maimon
**Context:** POC for iPracticom. Your manager will provision 3 Linux VMs on AWS (1 yours, 2 for fault injection). You install the Sweeper on yours and connect to theirs via SSM.
**Goal:** Validate detection across all 4 fault classes BEFORE going to prod.

---

## § 1 — Why SSM, not SSH, on AWS

| | SSH | SSM |
|---|---|---|
| Network requirements | Port 22 open on every target + your IP allowlisted | Targets need **only outbound 443** to SSM endpoints |
| Public IPs on targets | Required (or bastion) | **Not required** — targets can be in private subnet |
| Key management | SSH keypair per operator | IAM role per instance — no keys in flight |
| Audit | `auth.log` (target-side, easy to lose) | **CloudTrail** (centralized, immutable) |
| The sweeper already supports it | ❌ would need a new SSH collector | ✅ `fleet/aws_connector.py` (built, tested) |
| Command restriction | `command=` in `authorized_keys` (manual, per-key) | `ssm:SendCommand` IAM permission + document name pinning (per-role) |
| Failure mode | Lost key = rotate + redeploy everywhere | Lost IAM = detach role from instance, done |

**On AWS, SSM is strictly better.** SSH is the right call for bare-metal / on-prem (Contabo, your laptop). For iPracticom's AWS fleet, SSM.

---

## § 2 — The topology

```
┌─────────────────────────────────────────────────────────────┐
│  AWS Account (iPracticom's)                                  │
│                                                              │
│  ┌──────────────────────┐                                   │
│  │ VPC (existing)        │                                   │
│  │                       │                                   │
│  │  ┌─────────────────┐ │   SSM agent ────► SSM Endpoint    │
│  │  │ sweeper-ctrl    │ │   (outbound 443)   ────►          │
│  │  │ YOUR VM         │ │                        AWS         │
│  │  │  + Sweeper      │ │                        SSM API    │
│  │  │  + Hermes agent │ │                                   │
│  │  └─────────────────┘ │                                   │
│  │           │ poll SSM  │                                   │
│  │           ▼           │                                   │
│  │  ┌─────────────────┐ │                                   │
│  │  │ target-vm-1     │◄┤                                   │
│  │  │ (dev mgr's)     │ │                                   │
│  │  │ SSM agent       │ │                                   │
│  │  └─────────────────┘ │                                   │
│  │  ┌─────────────────┐ │                                   │
│  │  │ target-vm-2     │◄┤                                   │
│  │  │ (dev mgr's)     │ │                                   │
│  │  │ SSM agent       │ │                                   │
│  │  └─────────────────┘ │                                   │
│  │                       │                                   │
│  │  Subnet: private      │                                   │
│  │  No public IPs        │                                   │
│  │  IAM role: sweeper-*  │                                   │
│  └──────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘
```

**Key requirement:** all 3 VMs in the **same VPC** so your sweeper can reach SSM endpoints and so SSM can reach the targets via the SSM agent (outbound-only — no inbound rules needed).

---

## § 3 — Pre-flight: ask the dev manager to confirm

Send him this exact checklist (don't improvise — these are the prerequisites):

```
☐ All 3 VMs in same AWS account, same VPC, same region
☐ Amazon Linux 2023 or Ubuntu 22.04+ on each (SSM agent preinstalled)
☐ Subnet has route to SSM endpoints:
    - ssmmessages.<region>.amazonaws.com:443
    - ssm.<region>.amazonaws.com:443
    - ec2messages.<region>.amazonaws.com:443
    (VPC endpoints OR NAT gateway OR public subnet — your choice)
☐ Each target VM has an IAM instance profile attached:
    Role: sweeper-target-role
    Policies: AmazonSSMManagedInstanceCore (AWS managed)
☐ Your sweeper VM has an IAM instance profile:
    Role: sweeper-controller-role
    Policies:
      - AmazonSSMReadOnlyAccess  (or custom with ssm:SendCommand, ssm:GetCommandInvocation)
      - ssm:DescribeInstanceInformation
      - ec2:DescribeInstances (to discover target hostnames)
☐ SSM agent is RUNNING on all 3 VMs:
    sudo systemctl status amazon-ssm-agent
    (Amazon Linux 2023 / Ubuntu 22.04+: preinstalled and active by default)
☐ Your IAM user (or the controller VM's role) can call:
    aws ssm describe-instance-information --output json
    → returns all 3 VMs with "PingStatus": "Online"
```

**If any checkbox is unchecked → STOP. SSM won't work without these.**

---

## § 4 — Install the sweeper on YOUR controller VM

These steps run on YOUR VM only (`sweeper-ctrl`).

### § 4.1 — Install

```bash
# Pre-flight (must all pass)
[ "$(id -u)" -eq 0 ] || { echo "FAIL: must be root"; exit 1; }
python3 --version  # need 3.10+
curl -sSI https://github.com/iPracticom/ipracticom-sweeper | head -1
aws sts get-caller-identity  # confirms IAM works

# Install
git clone https://github.com/iPracticom/ipracticom-sweeper.git /opt/ipracticom-sweeper
cd /opt/ipracticom-sweeper
git checkout v0.3.0
python3 -m pip install -e . --break-system-packages --quiet

# Verify
python3 -m ipracticom_sweeper.sweeper --help | head -3
```

### § 4.2 — Configure READ-ONLY

```bash
mkdir -p /etc/ipracticom-sweeper
cat > /etc/ipracticom-sweeper/environment <<'EOF'
# iPracticom Sweeper — READ-ONLY mode for POC
IPRACTICOM_SWEEPER_READONLY=1

# Fleet config: which target instances to poll via SSM
# (region, instance-id or tag filter)
IPRACTICOM_FLEET_REGION=eu-central-1
IPRACTICOM_FLEET_TAG_KEY=Environment
IPRACTICOM_FLEET_TAG_VALUE=ipracticom-poc

# Notification channels (test channels only)
SLACK_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Agent API auth
AGENT_API_TOKEN=*** rand -hex 32)
EOF

chmod 600 /etc/ipracticom-sweeper/environment
```

### § 4.3 — Install systemd units

```bash
sudo bash /opt/ipracticom-sweeper/scripts/install-systemd.sh

# Add an API unit (not bundled in v0.3.0)
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
```

### § 4.4 — Configure fleet/SSM targets

The sweeper already has the connector; you just need to tell it which instances to poll.

```bash
# Verify SSM can see the targets (replace with the dev manager's instance IDs)
aws ssm describe-instance-information \
    --filters "Key=tag:Environment,Values=ipracticom-poc" \
    --query "InstanceInformationList[].{Id:InstanceId,Ping:PingStatus,Name:ComputerName}" \
    --output table

# ALL must show PingStatus = "Online" — otherwise SSM agent is broken on that VM
```

The sweeper's fleet collector polls these automatically once `IPRACTICOM_FLEET_TAG_*` is set. Verify by reading `/var/log/ipracticom-sweeper/fleet.log` after 60s.

---

## § 5 — What the dev manager needs to do on the 2 TARGET VMs

**Hand him this exact list.** Don't deviate — the sweeper expects exactly this surface.

### § 5.1 — On each target: install the sweeper (so we can also poll via local API as a fallback)

```bash
# Same install steps as § 4.1, but on each target
git clone https://github.com/iPracticom/ipracticom-sweeper.git /opt/ipracticom-sweeper
cd /opt/ipracticom-sweeper && git checkout v0.3.0
python3 -m pip install -e . --break-system-packages --quiet
sudo bash scripts/install-systemd.sh
```

**Important:** each target also runs the sweeper LOCALLY in READ-ONLY mode. The dev manager can verify each target is detecting its OWN faults even without the controller connected. This is the fallback path.

### § 5.2 — On each target: enable the agent API on localhost only

Same `ipracticom-sweeper-api.service` as § 4.3. The controller polls targets via **SSM**, not via direct HTTP, but having the local API lets the dev manager `curl` the target's own state for cross-verification during the POC.

### § 5.3 — Inject faults ONLY via the approved list

The dev manager's fault injection script MUST use these patterns (mirrored from § 4 of `INSTALL_READONLY_AND_MULTIHOST.md`):

```bash
# CPU spike
for i in 1 2 3 4; do (timeout 300 yes > /dev/null) & done

# Memory pressure
python3 -c "import ctypes, time; buf = (ctypes.c_char * (int(open('/proc/meminfo').read().split('MemTotal:')[1].split()[0]) * 1024 * 4 // 5))(); time.sleep(300)"

# Disk fill
truncate -s 5G /tmp/fillfile && sync   # cleanup: rm /tmp/fillfile

# Service kill (use a non-critical service)
sudo systemctl restart cron

# Network — packet drop (iptables, requires root)
sudo iptables -A OUTPUT -m statistic --mode random --probability 0.5 -j DROP
sleep 60 && sudo iptables -D OUTPUT -m statistic --mode random --probability 0.5 -j DROP
```

**NEVER inject faults on the controller VM** — only on the 2 targets. The controller must stay healthy so it can observe.

---

## § 6 — Verification checklist (run on the CONTROLLER after install)

| # | Check | Command | Pass criterion |
|---|---|---|---|
| 1 | SSM sees all 3 VMs | `aws ssm describe-instance-information --filters "Key=tag:Environment,Values=ipracticom-poc" --query "length(InstanceInformationList)"` | `2` (the 2 targets) |
| 2 | IAM works | `aws sts get-caller-identity` | shows the controller role |
| 3 | Sweeper installed | `python3 -m ipracticom_sweeper.sweeper --help` | exit 0 |
| 4 | READONLY set | `cat /etc/ipracticom-sweeper/environment \| grep READONLY` | `=1` |
| 5 | Timer active | `systemctl is-active ipracticom-sweeper.timer` | `active` |
| 6 | API listening | `curl -sS http://127.0.0.1:8810/healthz \| jq .ok` | `true` |
| 7 | Local detection works | inject CPU fault on controller itself (briefly); `curl -sS -H "Authorization: Bearer $(grep AGENT_API_TOKEN /etc/ipracticom-sweeper/environment \| cut -d= -f2)" http://127.0.0.1:8810/api/run \| jq '.defcon'` | integer 1-4 within 60s |
| 8 | Fleet poll works | `tail -20 /var/log/ipracticom-sweeper/fleet.log` | shows recent entries for both targets |
| 9 | SSM-collected snapshot | `curl -sS -H "Authorization: Bearer $(grep AGENT_API_TOKEN /etc/ipracticom-sweeper/environment \| cut -d= -f2)" http://127.0.0.1:8810/api/snapshot/raw \| jq '.events[-3:]'` | contains entries with `source: ssm` |
| 10 | Cross-host detection | dev manager injects fault on target; within 5 min, `curl http://127.0.0.1:8810/api/snapshot` shows DEFCON < 5 for that target | DEFCON < 5 |

If **any** fails → STOP. Capture the failing step + exit code, send to the dev manager. Do NOT improvise.

---

## § 7 — The POC success criteria

The POC is successful when ALL of these are demonstrated:

| # | Demonstration | How |
|---|---|---|
| 1 | CPU spike detected | Dev mgr runs CPU fault on target-1 → controller's /api/snapshot shows target-1 at DEFCON 3+ within 5 min |
| 2 | Memory pressure detected | Dev mgr runs mem fault on target-2 → controller shows target-2 at DEFCON 3+ within 5 min |
| 3 | Disk fill detected | Dev mgr fills target-1 → DEFCON 3+ within 5 min |
| 4 | Service down detected | Dev mgr kills cron on target-2 → DEFCON 3+ within 5 min |
| 5 | Network degradation detected | Dev mgr drops packets on target-1 → DEFCON 3+ within 5 min |
| 6 | No auto-repair happened | All targets still have the faults when you check; no `/var/log/ipracticom-sweeper/repair.jsonl` repair entries |
| 7 | Notification fired | Slack or Telegram message received by Daniel when DEFCON < 5 |

**Capture screenshots / journal logs of all 7 for the POC report.**

---

## § 8 — Rollback

```bash
# On controller
sudo bash /opt/ipracticom-sweeper/scripts/install-systemd.sh --uninstall
sudo systemctl disable --now ipracticom-sweeper-api.service
sudo rm -rf /opt/ipracticom-sweeper /etc/ipracticom-sweeper /var/lib/ipracticom-sweeper

# On each target (via SSM — no SSH needed!)
aws ssm send-command \
    --instance-ids "i-TARGET1" "i-TARGET2" \
    --document-name "AWS-RunShellScript" \
    --parameters 'commands=["sudo bash /opt/ipracticom-sweeper/scripts/install-systemd.sh --uninstall","sudo rm -rf /opt/ipracticom-sweeper /etc/ipracticom-sweeper /var/lib/ipracticom-sweeper"]' \
    --output text
```

---

## § 9 — What to send back when done

A single message to the dev manager with:
1. ✓/✗ for each row of § 6 checklist
2. Screenshots/logs proving § 7 success criteria (especially #6 — no auto-repair)
3. Any error messages — even minor — observed during install or during fault injection
4. The dev manager's feedback on the sweep latency (how fast did DEFCON update after fault?)

**Do NOT declare success based on "looks like it works."** Each row must have an exit code + command output attached.