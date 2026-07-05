"""Network metrics: TCP socket states, drops, errors, interface stats.

Reads from /proc/net/dev (interface counters) and /proc/net/tcp (sockets).
"""

from __future__ import annotations

from typing import Any

from .._log import log_suppressed


def _read_proc(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def get_interface_stats() -> list[dict[str, Any]]:
    """Parse /proc/net/dev for per-interface packet/byte/drop/error counts."""
    raw = _read_proc("/proc/net/dev")
    lines = raw.strip().split("\n")
    if len(lines) < 3:
        return []

    results = []
    for line in lines[2:]:
        if ":" not in line:
            continue
        iface, _, data = line.partition(":")
        parts = data.split()
        # /proc/net/dev columns:
        # receive: bytes packets errs drop fifo frame compressed multicast
        # transmit: bytes packets errs drop fifo colls carrier compressed
        if len(parts) < 16:
            continue
        results.append({
            "interface": iface.strip(),
            "rx_bytes": int(parts[0]),
            "rx_packets": int(parts[1]),
            "rx_errors": int(parts[2]),
            "rx_drops": int(parts[3]),
            "tx_bytes": int(parts[8]),
            "tx_packets": int(parts[9]),
            "tx_errors": int(parts[10]),
            "tx_drops": int(parts[11]),
        })
    return results


def get_tcp_states() -> dict[str, int]:
    """Count TCP sockets by state (ESTABLISHED, TIME_WAIT, CLOSE_WAIT, ...)."""
    raw = _read_proc("/proc/net/tcp")
    states = {}
    for line in raw.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        # state is hex at column 3
        try:
            state_hex = parts[3]
            state_int = int(state_hex, 16)
        except (ValueError, IndexError) as e:
            log_suppressed("network_tcp_state_parse", e)
            continue
        states[state_int] = states.get(state_int, 0) + 1

    # Map common states to names
    state_names = {
        1: "ESTABLISHED",
        2: "SYN_SENT",
        3: "SYN_RECV",
        4: "FIN_WAIT1",
        5: "FIN_WAIT2",
        6: "TIME_WAIT",
        7: "CLOSE",
        8: "CLOSE_WAIT",
        9: "LAST_ACK",
        10: "LISTEN",
        11: "CLOSING",
    }
    return {state_names.get(k, str(k)): v for k, v in states.items()}


def collect() -> dict[str, Any]:
    """Collect network metrics."""
    ifaces = get_interface_stats()
    tcp = get_tcp_states()

    total_rx_drops = sum(i["rx_drops"] for i in ifaces)
    total_tx_drops = sum(i["tx_drops"] for i in ifaces)
    total_rx_errors = sum(i["rx_errors"] for i in ifaces)
    total_tx_errors = sum(i["tx_errors"] for i in ifaces)

    return {
        "interfaces": ifaces,
        "interface_count": len(ifaces),
        "rx_drops_total": total_rx_drops,
        "tx_drops_total": total_tx_drops,
        "rx_errors_total": total_rx_errors,
        "tx_errors_total": total_tx_errors,
        "tcp_states": tcp,
        "established_count": tcp.get("ESTABLISHED", 0),
        "close_wait_count": tcp.get("CLOSE_WAIT", 0),
        "time_wait_count": tcp.get("TIME_WAIT", 0),
        "listen_count": tcp.get("LISTEN", 0),
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    total_drops = values["rx_drops_total"] + values["tx_drops_total"]
    total_errors = values["rx_errors_total"] + values["tx_errors_total"]

    if total_drops >= rules["network"]["dropped_packets_warn"] * 10:
        return "crit"
    if total_drops >= rules["network"]["dropped_packets_warn"]:
        return "warn"
    if total_errors >= rules["network"]["dropped_packets_warn"]:
        return "warn"
    if values["close_wait_count"] >= rules["network"]["connections_close_wait_warn"]:
        return "warn"
    return "ok"