"""Catalogue of available checks + current threshold parameters.

Additive module for v0.5.0 slice 1.2. Provides:
  - CHECK_REGISTRY: curated list of check metadata (key, label_he, description_he,
    rule_keys with type/default/description_he)
  - render_catalogue() / render_check() helpers used by dashboard routes.

This module does NOT mutate rules.yaml — the editor is read-only (slice 1.2).
Future slices may add write support once an explicit approval flow exists.
"""
from __future__ import annotations

from typing import Any

# Each entry: key used in rules.yaml + the snapshot's modules dict
#   label_he       — Hebrew display name
#   description_he — short operator-facing description (1-2 lines)
#   rule_keys      — ordered list of param keys (matches rules.yaml)
#                    each with: type (int|float|str|list), default, description_he
CHECK_REGISTRY: list[dict[str, Any]] = [
    {
        "key": "cpu",
        "label_he": "מעבד (CPU)",
        "description_he": "עומס מעבד: load average, steal time, iowait.",
        "rule_keys": [
            {"name": "load_avg_5min_warn", "type": "float",
             "description_he": "סף אזהרה — load average ליבה ל-5 דקות"},
            {"name": "load_avg_5min_crit", "type": "float",
             "description_he": "סף קריטי — load average ליבה ל-5 דקות"},
            {"name": "steal_percent_warn", "type": "float",
             "description_he": "אחוז גניבת זמן מעבד על-ידי hypervisor"},
            {"name": "iowait_percent_warn", "type": "float",
             "description_he": "אחוז המתנה לדיסק"},
        ],
    },
    {
        "key": "memory",
        "label_he": "זיכרון",
        "description_he": "שימוש ב-RAM ו-swap.",
        "rule_keys": [
            {"name": "used_percent_warn", "type": "float",
             "description_he": "סף אזהרה לשימוש בזיכרון (%)"},
            {"name": "used_percent_crit", "type": "float",
             "description_he": "סף קריטי לשימוש בזיכרון (%)"},
            {"name": "swap_used_percent_warn", "type": "float",
             "description_he": "סף אזהרה ל-swap (%)"},
        ],
    },
    {
        "key": "disk",
        "label_he": "דיסק",
        "description_he": "תפוסת דיסק ו-inodes. כולל הגדרת mounts שחייבים להיות read-only.",
        "rule_keys": [
            {"name": "used_percent_warn", "type": "float",
             "description_he": "סף אזהרה לתפוסת דיסק (%)"},
            {"name": "used_percent_crit", "type": "float",
             "description_he": "סף קריטי לתפוסת דיסק (%)"},
            {"name": "inode_used_percent_warn", "type": "float",
             "description_he": "סף אזהרה לשימוש ב-inodes (%)"},
        ],
    },
    {
        "key": "network",
        "label_he": "רשת",
        "description_he": "מנות שנופלות, retransmits, CLOSE_WAIT accumulation.",
        "rule_keys": [
            {"name": "dropped_packets_warn", "type": "int",
             "description_he": "סף מנות שנופלות"},
            {"name": "tcp_retransmit_percent_warn", "type": "float",
             "description_he": "סף אחוז retransmit TCP"},
            {"name": "connections_close_wait_warn", "type": "int",
             "description_he": "סף חיבורי CLOSE_WAIT פתוחים"},
        ],
    },
    {
        "key": "services",
        "label_he": "שירותים",
        "description_he": "שירותים קריטיים שחייבים להיות רצים.",
        "rule_keys": [
            {"name": "critical_list", "type": "list",
             "description_he": "רשימת שירותים קריטיים"},
            {"name": "failed_units_window_min", "type": "int",
             "description_he": "חלון זמן לספירת failed units (דקות)"},
        ],
    },
    {
        "key": "logs",
        "label_he": "לוגים",
        "description_he": "קצב שגיאות ו-OOM events.",
        "rule_keys": [
            {"name": "error_rate_per_min_warn", "type": "int",
             "description_he": "סף שגיאות לדקה"},
            {"name": "oom_events_window_min", "type": "int",
             "description_he": "חלון זמן לספירת OOM events (דקות)"},
        ],
    },
    {
        "key": "processes",
        "label_he": "תהליכים",
        "description_he": "zombies ותהליכים תקועים.",
        "rule_keys": [
            {"name": "zombie_count_warn", "type": "int",
             "description_he": "סף תהליכי zombie"},
            {"name": "stuck_proc_minutes_warn", "type": "int",
             "description_he": "סף דקות לתהליך תקוע"},
        ],
    },
    {
        "key": "security",
        "label_he": "אבטחה",
        "description_he": "SSH brute-force, sudo failures.",
        "rule_keys": [
            {"name": "failed_ssh_per_min_warn", "type": "int",
             "description_he": "סף כשלונות SSH לדקה"},
            {"name": "sudo_failures_per_hour_warn", "type": "int",
             "description_he": "סף כשלונות sudo לשעה"},
        ],
    },
    {
        "key": "aws",
        "label_he": "AWS / ענן",
        "description_he": "AWS-specific signals (IMDS reachability, region, etc.).",
        "rule_keys": [],
    },
    {
        "key": "kernel",
        "label_he": "קרנל",
        "description_he": "kernel errors, OOM killer events, soft lockups.",
        "rule_keys": [
            {"name": "oops_window_min", "type": "int",
             "description_he": "חלון זמן לספירת kernel oops/panic (דקות)"},
            {"name": "oom_events_warn", "type": "int",
             "description_he": "סף אזהרה — OOM events"},
            {"name": "soft_lockup_warn", "type": "int",
             "description_he": "סף אזהרה — soft lockup events"},
        ],
    },
    {
        "key": "process_tracker",
        "label_he": "Top תהליכים",
        "description_he": "תהליכים בולטים ב-CPU/זיכרון — דגימה לפי תהליך.",
        "rule_keys": [
            {"name": "top_n", "type": "int",
             "description_he": "מספר תהליכים לדגום"},
            {"name": "sample_interval_sec", "type": "int",
             "description_he": "מרווח דגימה (שניות)"},
        ],
    },
    {
        "key": "fd_check",
        "label_he": "File Descriptors",
        "description_he": "ניצול file descriptors של המערכת — סכנת exhaustion.",
        "rule_keys": [
            {"name": "used_percent_warn", "type": "float",
             "description_he": "סף אזהרה — אחוז ניצול FD"},
            {"name": "used_percent_crit", "type": "float",
             "description_he": "סף קריטי — אחוז ניצול FD"},
        ],
    },
    {
        "key": "security_baseline",
        "label_he": "Baseline אבטחה",
        "description_he": "השוואת הגדרות מערכת ל-baseline: SSH root login, SUID binaries, פורטים פתוחים.",
        "rule_keys": [
            {"name": "ssh_permit_root_warn", "type": "str",
             "description_he": "ערך צפוי ל-PermitRootLogin (no/prohibit-password)"},
            {"name": "suid_max_warn", "type": "int",
             "description_he": "סף אזהרה — מספר SUID binaries חריגים"},
            {"name": "open_ports_allowed", "type": "list",
             "description_he": "רשימת פורטים שצריכים להיות פתוחים"},
        ],
    },
    {
        "key": "uptime",
        "label_he": "זמן פעילות",
        "description_he": "זמן פעילות המערכת וריצה אחרונה.",
        "rule_keys": [
            {"name": "min_uptime_sec_warn", "type": "int",
             "description_he": "סף אזהרה — uptime מינימלי (שניות, מתחת = crit)"},
        ],
    },
    {
        "key": "health",
        "label_he": "בריאות סוכן",
        "description_he": "heartbeat של הסוכן עצמו — agent_api, sweep cycles, last_run.",
        "rule_keys": [
            {"name": "heartbeat_max_age_sec_warn", "type": "int",
             "description_he": "סף אזהרה — heartbeat ישן מדי (שניות)"},
        ],
    },
    # v0.5.0: FreeSWITCH Tier 1 — service health (FS-01..FS-05)
    {
        "key": "freeswitch",
        "label_he": "FreeSWITCH — סטטוס שירות",
        "description_he": "Tier 1: האם ה-process רץ, systemd active, ports 5060/5080 פתוחים, fs_cli עונה. כל ה-5 = crit אם נופל.",
        "rule_keys": [
            {"name": "process_required", "type": "str",
             "description_he": "שם ה-process הנדרש (freeswitch)"},
            {"name": "ports_required", "type": "list",
             "description_he": "פורטים שחייבים להיות פתוחים (SIP=5060, SIPS=5061, ws=5066, wss=7443)"},
            {"name": "fs_cli_timeout_ms", "type": "int",
             "description_he": "timeout ל-fs_cli (אלפיות שנייה)"},
        ],
    },
    # v0.5.0: FreeSWITCH Tier 2 — network integrity (FS-06..FS-09)
    {
        "key": "freeswitch_network",
        "label_he": "FreeSWITCH — רשת ו-signaling",
        "description_he": "Tier 2: SIP peers, registrations, gateways, RTP ports. Zero registrations = crit.",
        "rule_keys": [
            {"name": "min_registrations_warn", "type": "int",
             "description_he": "מספר registrations מינימלי לפני אזהרה"},
            {"name": "min_registrations_crit", "type": "int",
             "description_he": "מספר registrations מינימלי לפני crit"},
            {"name": "max_gateway_down_warn", "type": "int",
             "description_he": "מספר gateways שמותר להיות down"},
        ],
    },
    # v0.5.0: FreeSWITCH Tier 3 — operational + baseline drift (FS-10..FS-15)
    {
        "key": "freeswitch_operational",
        "label_he": "FreeSWITCH — תפעול ו-drift",
        "description_he": "Tier 3: cli latency, active calls/channels, disk, config drift, baseline.",
        "rule_keys": [
            {"name": "cli_latency_warn_ms", "type": "int",
             "description_he": "סף אזהרה — fs_cli latency (ms)"},
            {"name": "max_active_calls_warn", "type": "int",
             "description_he": "סף אזהרה — מספר שיחות פעילות"},
            {"name": "disk_free_percent_warn", "type": "float",
             "description_he": "סף אזהרה — דיסק פנוי (%)"},
        ],
    },
    # v0.5.0: FreeSWITCH Tier 4 — edge cases (FS-16..FS-25)
    {
        "key": "freeswitch_edge",
        "label_he": "FreeSWITCH — מקרי קצה",
        "description_he": "Tier 4: גיבוי CDR, recordings, packet loss, jitter, codec, RSS, CPU, TCP retransmits, log errors, fail2ban.",
        "rule_keys": [
            {"name": "cdr_backup_age_max_days_warn", "type": "int",
             "description_he": "גיל מקסימלי לגיבוי CDR (ימים)"},
            {"name": "jitter_warn_ms", "type": "int",
             "description_he": "סף אזהרה — jitter (ms)"},
            {"name": "packet_loss_percent_warn", "type": "float",
             "description_he": "סף אזהרה — packet loss (%)"},
            {"name": "fail2ban_banned_min_warn", "type": "int",
             "description_he": "מספר IPs חסומים מינימלי ב-fail2ban"},
        ],
    },
    # v0.6.x: Catalogue parity — these modules are imported in monitor/checks.py
    # but were not exposed via the catalogue UI.
    {
        "key": "aide",
        "label_he": "AIDE — שלמות מערכת",
        "description_he": "בדיקת שינויים בקבצים קריטיים מול בסיס AIDE. ריצה שגרתית אוטומטית + התראה כשיש drift חריג.",
        "rule_keys": [
            {"name": "critical_paths", "type": "list",
             "description_he": "רשימת נתיבים שחייבים להיות ללא שינוי"},
        ],
    },
    {
        "key": "http",
        "label_he": "בדיקות HTTP",
        "description_he": "בדיקת endpoints חיצוניים — זמן תגובה, סטטוס, תקינות גוף התגובה.",
        "rule_keys": [
            {"name": "slow_response_ms", "type": "int",
             "description_he": "סף זמן תגובה איטי (אלפיות שנייה)"},
        ],
    },
    {
        "key": "iostat",
        "label_he": "I/O latency (iostat)",
        "description_he": "זמני המתנה לדיסק (await) וניצול אחוזי לפי מכשיר. מתבסס על sysstat package.",
        "rule_keys": [
            {"name": "await_warn_ms", "type": "int",
             "description_he": "סף אזהרה — await במילישניות"},
            {"name": "await_crit_ms", "type": "int",
             "description_he": "סף קריטי — await במילישניות"},
            {"name": "util_warn_percent", "type": "float",
             "description_he": "סף אזהרה — אחוז ניצול דיסק"},
            {"name": "util_crit_percent", "type": "float",
             "description_he": "סף קריטי — אחוז ניצול דיסק"},
        ],
    },
    {
        "key": "smart",
        "label_he": "SMART — בריאות דיסקים",
        "description_he": "מאפייני SMART — sectors מועברים מחדש, טמפרטורה, בריאות כללית. מתבסס על smartmontools.",
        "rule_keys": [
            {"name": "reallocated_warn", "type": "int",
             "description_he": "סף אזהרה — sectors שהועברו מחדש"},
            {"name": "reallocated_crit", "type": "int",
             "description_he": "סף קריטי — sectors שהועברו מחדש"},
            {"name": "temp_warn_c", "type": "int",
             "description_he": "סף אזהורה — טמפרטורת דיסק ב-Celsius"},
        ],
    },
    {
        "key": "ssl",
        "label_he": "תעודות SSL",
        "description_he": "בדיקת תוקף תעודות TLS לרשימת hosts. התראה לפני תפוגה + קריטי ב-CertWindow קצר.",
        "rule_keys": [
            {"name": "warn_days", "type": "int",
             "description_he": "סף אזהרה — ימים לפני פקיעת תעודה"},
            {"name": "crit_days", "type": "int",
             "description_he": "סף קריטי — ימים לפני פקיעת תעודה"},
        ],
    },
]  # v0.5.0 baseline (slice 1.x + Sprint 2 FS integration)


def get_check(module_key: str) -> dict[str, Any] | None:
    """Return registry entry for a module, or None if unknown."""
    for entry in CHECK_REGISTRY:
        if entry["key"] == module_key:
            return entry
    return None


def render_catalogue(rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Build list of {key, label_he, description_he, param_count} for the index page."""
    out = []
    for entry in CHECK_REGISTRY:
        module_rules = rules.get(entry["key"], {}) if isinstance(rules, dict) else {}
        out.append({
            "key": entry["key"],
            "label_he": entry["label_he"],
            "description_he": entry["description_he"],
            "param_count": len(entry["rule_keys"]),
            "current_param_count": len(module_rules) if isinstance(module_rules, dict) else 0,
        })
    return out


def render_check(module_key: str, rules: dict[str, Any]) -> dict[str, Any] | None:
    """Build {entry, params: [...]} for the per-module editor page.

    Each param carries: name, type, description_he, current_value.
    Read-only — does not mutate rules.
    """
    entry = get_check(module_key)
    if entry is None:
        return None
    module_rules = rules.get(module_key, {}) if isinstance(rules, dict) else {}
    params = []
    for rk in entry["rule_keys"]:
        params.append({
            "name": rk["name"],
            "type": rk["type"],
            "description_he": rk["description_he"],
            "current_value": module_rules.get(rk["name"]) if isinstance(module_rules, dict) else None,
        })
    return {
        "entry": entry,
        "params": params,
        "all_current": module_rules if isinstance(module_rules, dict) else {},
    }
