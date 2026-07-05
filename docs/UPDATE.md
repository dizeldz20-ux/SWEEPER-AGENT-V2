# iPracticom Sweeper — Update Guide
**For:** Hermes agents / operators upgrading an existing install to the latest version.
**Last updated:** 2026-06-30

---

## § 1 — TL;DR

```bash
# As root, from inside the repo:
sudo bash scripts/update.sh
```

That's it. The script:
1. Backs up `/etc/ipracticom-sweeper/*` and `/var/lib/ipracticom-sweeper/*` to `.update_backup/`.
2. Stops the timer + telegram bot.
3. `git pull --ff-only` from `origin/master`.
4. Reinstalls the Python package (`pip install -e .`).
5. Refreshes systemd units if changed.
6. Restarts timer + telegram bot.
7. Verifies `GET /healthz` returns 200.
8. Prints old version → new version.

If anything fails mid-update, the script restores the backup automatically. You can also manually rollback:

```bash
sudo bash scripts/update.sh --rollback
```

---

## § 2 — First-time install vs. update

These are **different scripts**. Don't confuse them:

| Script | Use case | What it does |
|---|---|---|
| `scripts/install-systemd.sh` | First-time install on a fresh VM | Installs pip package, creates systemd units, enables timer |
| `scripts/install_telegram_bot.sh` | First-time install of Telegram bot | Creates `/etc/ipracticom-sweeper/telegram-bot.env` + telegram systemd unit |
| `scripts/update.sh` | Upgrading an existing install | Preserves config + state, pulls latest code, reinstalls |

If you run `install-systemd.sh` on a VM that already has the sweeper installed, it will fail (or worse, partially clobber config). **Only use `update.sh` for upgrades.**

---

## § 3 — What gets preserved across updates

The update script backs up and restores:

| Path | Contents | Why |
|---|---|---|
| `/etc/ipracticom-sweeper/agent.env` | `AGENT_API_TOKEN`, secrets | Without this, the API auth breaks |
| `/etc/ipracticom-sweeper/telegram-bot.env` | `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS`, `AGENT_API_URL` | Without this, the bot can't authenticate |
| `/etc/ipracticom-sweeper/repair_policy.yaml` | Your approval/auto policy | Without this, default reverts to `auto` (DANGEROUS) |
| `/var/lib/ipracticom-sweeper/heartbeat.json` | Last pipeline run timestamp + psutil metrics | Without this, "fresh" health checks fail |
| `/var/lib/ipracticom-sweeper/audit/repairs.jsonl` | Repair execution log (auditable trail) | Without this, you lose forensic history |
| `/var/lib/ipracticom-sweeper/pending_repairs/` | Repair proposals awaiting approval | Without this, in-flight approvals vanish |
| `/var/lib/ipracticom-sweeper/snapshots/` | Time-series snapshots | Without this, `/api/history` is empty |
| `/var/lib/ipracticom-sweeper/connectors.yaml` | SSM connector definitions | Without this, you have to re-add monitored hosts |

The backup lives at `/var/lib/ipracticom-sweeper/.update_backup/` and is overwritten on every `update.sh` run. **Only the most recent backup is kept** — to keep history, copy it elsewhere before the next update.

---

## § 4 — Subcommands

| Command | What it does |
|---|---|
| `sudo bash scripts/update.sh` | Pull + reinstall + restart (the main flow) |
| `sudo bash scripts/update.sh --check` | Show what commits would be pulled, no changes |
| `sudo bash scripts/update.sh --version` | Print installed version + local HEAD + remote HEAD |
| `sudo bash scripts/update.sh --rollback` | Restore from `.update_backup/` |
| `sudo bash scripts/update.sh --help` | Show usage |

---

## § 5 — Verifying an update succeeded

After `sudo bash scripts/update.sh` finishes:

```bash
# 1. Check the version numbers
sudo bash scripts/update.sh --version
# Expected: installed = remote HEAD = latest commit hash

# 2. Check the timer is firing
systemctl list-timers ipracticom-sweeper
# Expected: NEXT column shows a time ~5 min from now

# 3. Check the last sweep succeeded
systemctl status ipracticom-sweeper.service
# Expected: "Active: inactive (dead)" with "Result: success" (oneshot, so inactive is OK)

# 4. Check the API is responding
curl -s http://127.0.0.1:8787/healthz
# Expected: {"ok": true}

# 5. Check the bot is connected
systemctl status ipracticom-sweeper-telegram.service
# Expected: "Active: active (running)"

# 6. Check the Telegram bot receives messages
# Open Telegram, send /start → should get the menu.
```

If any of those fail, run `sudo bash scripts/update.sh --rollback` to revert.

---

## § 6 — When `--rollback` doesn't help

Some failures require manual intervention:

| Symptom | Likely cause | Fix |
|---|---|---|
| `pip install` fails on new dependency | OS package missing | `apt install python3-dev libffi-dev` (Debian) or equivalent, then re-run update |
| `git pull --ff-only` fails because local has diverged | You committed locally or `git pull` was run previously with rebase | `git fetch && git reset --hard origin/master` (WARNING: destroys local commits), then re-run update |
| Timer doesn't fire after restart | systemd reload issue | `systemctl daemon-reload && systemctl restart ipracticom-sweeper.timer` |
| Bot gives "agent_api request failed" again | New code path also slow, increase timeout | Edit `src/ipracticom_sweeper/telegram_bot/services/agent_client.py` and bump `timeout=120.0` to 240.0 |
| Repair policy was reset to `auto` | `/etc/ipracticom-sweeper/repair_policy.yaml` was missing before backup | The backup preserves the file — but if the file didn't exist, there's nothing to restore. Manually set `default: needs_approval` again |

---

## § 7 — What changed in this update (v0.4.7 example)

Every commit message follows Conventional Commits (`feat:`, `fix:`, `docs:`). The `update.sh` output shows the diff between local HEAD and origin HEAD:

```
[update] Behind by 3 commit(s):
b717476 fix(telegram): v0.4.7 - run_now timeout 10s -> 120s
2c9442d feat(policy): v0.4.6 - default=needs_approval + rich alerts
4b6c518 feat(telegram): v0.4.5 — render real psutil metrics + English connector prompts
```

Read the full diff in `CHANGELOG.md` if you want to know what behavior changed before applying.

---

## § 8 — Pinning to a specific version

If you don't want to track `master` (e.g. production), override the branch:

```bash
sudo SWEEPER_BRANCH=v0.4.7 bash scripts/update.sh
```

The script will pull that specific ref. To switch back:

```bash
sudo SWEEPER_BRANCH=master bash scripts/update.sh
```