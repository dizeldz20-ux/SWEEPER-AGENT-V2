"""Sprint 13.2 — outbound-connection anomaly detector.

Parses `ss -tn` output, classifies remote IPs against an allow/deny list,
and returns a list of unusual outbound connections. Designed for
testability: parse_ss_output is a pure function.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from .._log import log_suppressed


# State-line pattern in `ss -tn`:
# State  Recv-Q  Send-Q   Local Address:Port   Peer Address:Port
# ESTAB  0       0        10.0.0.5:443          1.2.3.4:443
_LINE_RE = re.compile(
    r"^(?P<state>\S+)\s+\d+\s+\d+\s+"
    r"(?P<local>\S+)\s+(?P<remote>\S+)$"
)


@dataclass
class EgressConnection:
    state: str
    local: str
    remote_ip: str
    remote_port: int


@dataclass
class EgressResult:
    status: str
    unknown_ips: list[EgressConnection] = field(default_factory=list)
    blocked_ips: list[EgressConnection] = field(default_factory=list)
    total: int = 0
    reason: str = ""


def parse_ss_output(stdout: str) -> list[EgressConnection]:
    """Parse `ss -tn` output into a list of EgressConnection."""
    out: list[EgressConnection] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("State") or line.startswith("Netid"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        state = m.group("state")
        if state.startswith("LISTEN") or state.startswith("UNCONN"):
            continue
        remote = m.group("remote")
        # remote is "ip:port" or "[v6]:port"
        host, _, port_str = remote.rpartition(":")
        host = host.strip("[]")
        try:
            port = int(port_str)
        except ValueError as e:
            log_suppressed("egress_parse_port", e)
            continue
        # Only IPv4 for now (simple; IPv6 can be added later)
        try:
            ipaddress.IPv4Address(host)
        except (ipaddress.AddressValueError, ValueError) as e:
            log_suppressed("egress_parse_ip", e)
            continue
        out.append(EgressConnection(state=state, local=m.group("local"),
                                    remote_ip=host, remote_port=port))
    return out


def _ip_in_cidrs(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ipaddress.AddressValueError, ValueError):
        return False
    for cidr in cidrs:
        try:
            if addr in ipaddress.IPv4Network(cidr, strict=False):
                return True
        except (ipaddress.AddressValueError, ValueError) as e:
            log_suppressed("egress_cidr_parse", e)
            continue
    return False


def check_egress(
    allowlist: Optional[list[str]] = None,
    denylist: Optional[list[str]] = None,
    ss_runner=None,
) -> EgressResult:
    """Check outbound connections against allow/deny lists.

    allowlist: list of CIDR ranges. If non-empty, IPs not in any range
               are 'unknown' (warn).
    denylist:  list of CIDR ranges. IPs in any range are crit.
    """
    allowlist = allowlist or []
    denylist = denylist or []

    if ss_runner is None:
        def _default_runner():
            try:
                r = subprocess.run(["ss", "-tn"], capture_output=True, text=True, timeout=5)
                return r.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return ""
        ss_runner = _default_runner
    stdout = ss_runner()
    conns = parse_ss_output(stdout)

    blocked: list[EgressConnection] = []
    unknown: list[EgressConnection] = []

    for c in conns:
        if denylist and _ip_in_cidrs(c.remote_ip, denylist):
            blocked.append(c)
        elif allowlist and not _ip_in_cidrs(c.remote_ip, allowlist):
            unknown.append(c)

    if blocked:
        status = "crit"
    elif unknown:
        status = "warn"
    else:
        status = "ok"

    return EgressResult(
        status=status,
        unknown_ips=unknown,
        blocked_ips=blocked,
        total=len(conns),
    )
