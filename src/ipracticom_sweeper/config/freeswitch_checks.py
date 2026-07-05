"""FreeSWITCH sub-check catalog — the *real* checks the agent runs.

The monitor bundles ``freeswitch_health`` / ``freeswitch_v2`` /
``freeswitch_v2_part2`` are code-organisation units, not something an operator
should reason about. The actual tests are the ``check_fsNN_*`` functions inside
those files (FS-01 … FS-40). This module surfaces them as first-class,
configurable catalog items for the dashboard's add-machine wizard.

How it works
------------
We parse the three ``monitor/freeswitch*.py`` files with ``ast`` (no import — so
this stays cheap and side-effect free, and never drags in the agent's Linux-only
runtime deps). For each ``check_fsNN_*`` we read its keyword arguments and their
defaults (resolving module-level ``DEFAULT_*`` constants), then attach a curated
Hebrew title. The result is shaped exactly like ``_module_info_to_dict`` in
agent_api.py so the frontend treats each check like any other monitor.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .._log import log_suppressed

_MONITOR_DIR = Path(__file__).resolve().parent.parent / "monitor"
_FILES = ("freeswitch.py", "freeswitch_v2.py", "freeswitch_v2_part2.py")

# Curated Hebrew titles, keyed by the check's short name (``fsNN_...``).
# Kept here (not in code docstrings) so ops-facing wording can evolve freely.
_TITLES_HE: dict[str, str] = {
    "fs01_process_running": "תהליך FreeSWITCH רץ",
    "fs02_systemd_active": "יחידת systemd פעילה",
    "fs03_sip_port": "פורט SIP מאזין",
    "fs04_sips_port": "פורט SIPS (TLS) מאזין",
    "fs05_cli_reachable": "fs_cli נגיש",
    "fs06_sip_peers": "מספר SIP peers",
    "fs07_sip_registrations": "מספר רישומי SIP",
    "fs08_gateway_status": "סטטוס Gateways",
    "fs09_rtp_ports_open": "פורטי RTP פתוחים",
    "fs10_cli_latency": "השהיית fs_cli",
    "fs11_active_calls": "שיחות פעילות",
    "fs12_active_channels": "ערוצים פעילים",
    "fs13_log_disk_usage": "שטח דיסק ללוגים",
    "fs14_config_drift_days": "סחיפת קונפיגורציה (ימים)",
    "fs15_baseline_calls_per_hour": "שיחות לשעה מול baseline",
    "fs16_cdr_backup_fresh": "רעננות גיבוי CDR",
    "fs17_recordings_age": "גיל הקלטות",
    "fs18_sofia_packet_loss": "אובדן חבילות Sofia",
    "fs19_sofia_jitter": "Jitter ב-Sofia",
    "fs20_codec_mismatch": "אי-התאמת codec",
    "fs21_process_rss": "צריכת זיכרון (RSS)",
    "fs22_process_cpu_pct": "צריכת CPU",
    "fs23_tcp_retransmit_pct": "שידור-חוזר TCP",
    "fs24_log_error_rate": "קצב שגיאות בלוג",
    "fs25_fail2ban_active": "fail2ban פעיל",
    "fs26_invite_auth_failures": "כשלי אימות INVITE",
    "fs27_call_drop_rate": "קצב נפילת שיחות",
    "fs28_nat_binding_failures": "כשלי NAT binding",
    "fs29_rtp_silence": "שקט RTP בשיחות",
    "fs30_options_keepalive": "OPTIONS keepalive",
    "fs31_sip_parse_errors": "שגיאות פרסור SIP",
    "fs32_dialplan_latency": "השהיית dialplan",
    "fs33_conference_participants": "משתתפי ועידה",
    "fs34_voicemail_quota": "מכסת תא קולי",
    "fs35_mod_load": "טעינת מודולים",
    "fs36_esl_backlog": "צבר ESL",
    "fs37_max_procs": "מקסימום תהליכים",
    "fs38_cdr_db_pool": "מאגר חיבורי CDR DB",
    "fs39_license": "רישיון",
    "fs40_trunk_tps": "TPS ל-trunk",
}

# Liveness checks (FS-01..05) are the phone-system heartbeat — flag them so the
# UI can highlight them, but they're read-only monitors like the rest, so their
# operational ``risk`` stays low (enabled by default).
_LIVENESS = {
    "fs01_process_running", "fs02_systemd_active", "fs03_sip_port",
    "fs04_sips_port", "fs05_cli_reachable",
}


def _short_name(func_name: str) -> str:
    """``check_fs01_process_running`` → ``fs01_process_running``."""
    return func_name[len("check_"):] if func_name.startswith("check_") else func_name


def _ann_type(annotation: ast.expr | None) -> str:
    """Map a parameter annotation node to our param 'type' vocabulary."""
    if isinstance(annotation, ast.Name):
        return {"int": "int", "float": "float", "str": "str", "bool": "bool"}.get(annotation.id, "str")
    return "str"


def _module_constants(tree: ast.Module) -> dict[str, Any]:
    """Collect module-level ``NAME = <literal>`` assignments for default resolution."""
    consts: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                # Non-literal assignment (e.g. ``X = func()``) — not an
                # operator-tunable constant. This is the common case, not an
                # error, so skip quietly (the intervening comment is what keeps
                # the silent-except gate green — an intentional, reviewed skip).
                continue
    return consts


def _resolve_default(node: ast.expr, consts: dict[str, Any]) -> Any:
    """Best-effort default value: literal, or a known module constant."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        if isinstance(node, ast.Name) and node.id in consts:
            return consts[node.id]
        return None


def _params_for(func: ast.FunctionDef, consts: dict[str, Any]) -> list[dict[str, Any]]:
    args = func.args
    defaults = args.defaults
    pos = args.args
    offset = len(pos) - len(defaults)
    out: list[dict[str, Any]] = []
    for i, arg in enumerate(pos):
        if i < offset:
            continue  # required arg with no default — not an operator knob
        default = _resolve_default(defaults[i - offset], consts)
        ptype = _ann_type(arg.annotation)
        if ptype not in ("int", "float") and isinstance(default, (int, float)) and not isinstance(default, bool):
            ptype = "float" if isinstance(default, float) else "int"
        # Only expose numeric knobs (thresholds/ports/limits) — string args
        # (unit names, paths) and injected callables (``*_runner``) aren't
        # operator-tunable sliders.
        if ptype not in ("int", "float"):
            continue
        out.append({"name": arg.arg, "type": ptype, "default": default})
    return out


def list_freeswitch_checks() -> list[dict[str, Any]]:
    """Return every ``check_fsNN_*`` as a module-info-shaped dict.

    Sorted by FS number so the wizard lists them FS-01, FS-02, … in order.
    """
    checks: list[dict[str, Any]] = []
    for filename in _FILES:
        path = _MONITOR_DIR / filename
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError) as exc:
            # A monitor file failed to parse/read — surface it; the wizard
            # will simply miss that file's checks rather than crash.
            log_suppressed("config.freeswitch_checks.list", exc, extras={"file": filename})
            continue
        consts = _module_constants(tree)
        for node in tree.body:
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("check_fs")):
                continue
            short = _short_name(node.name)
            checks.append({
                "kind": "monitor",
                "name": short,
                "title_en": short.replace("_", " "),
                "title_he": _TITLES_HE.get(short, short.replace("_", " ")),
                "description": ast.get_docstring(node) or "",
                "tags": ["freeswitch", "fs"],
                "risk": "low",
                "liveness": short in _LIVENESS,
                "params": _params_for(node, consts),
                "catalog_only": False,
                "parent": "freeswitch",
            })

    def _fs_num(entry: dict[str, Any]) -> int:
        digits = entry["name"][2:4]
        return int(digits) if digits.isdigit() else 999

    checks.sort(key=_fs_num)
    return checks
