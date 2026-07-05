"""Kubernetes collector: uses kubectl CLI (no API permissions required)."""
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass


@dataclass
class K8sStats:
    namespace_count: int
    pod_count: int
    pod_running: int
    pod_pending: int
    pod_failed: int
    reachable: bool
    error: str | None = None


def collect_k8s_stats(
    context: str | None = None,
    namespace: str | None = None,
    timeout: int = 10,
) -> K8sStats:
    """Get K8s cluster stats via kubectl.

    context: optional kubeconfig context
    namespace: optional namespace filter
    timeout: seconds
    """
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += ["get", "pods", "-o", "json", "--all-namespaces"]
    if namespace:
        # Replace --all-namespaces with -n
        cmd.remove("--all-namespaces")
        cmd += ["-n", namespace]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return K8sStats(0, 0, 0, 0, 0, False, error=result.stderr.strip()[:200])

        data = json.loads(result.stdout)
        items = data.get("items", [])

        namespaces = set()
        running = 0
        pending = 0
        failed = 0
        for pod in items:
            ns = pod.get("metadata", {}).get("namespace", "default")
            namespaces.add(ns)
            phase = pod.get("status", {}).get("phase", "")
            if phase == "Running":
                running += 1
            elif phase == "Pending":
                pending += 1
            elif phase == "Failed":
                failed += 1

        return K8sStats(
            namespace_count=len(namespaces),
            pod_count=len(items),
            pod_running=running,
            pod_pending=pending,
            pod_failed=failed,
            reachable=True,
        )
    except subprocess.TimeoutExpired:
        return K8sStats(0, 0, 0, 0, 0, False, error=f"timeout after {timeout}s")
    except json.JSONDecodeError as e:
        return K8sStats(0, 0, 0, 0, 0, False, error=f"JSON parse error: {e}")
    except Exception as e:
        return K8sStats(0, 0, 0, 0, 0, False, error=str(e)[:200])


def defcon_from_k8s(stats: K8sStats) -> int:
    """Map K8s stats to DEFCON level (1-5, lower=worse)."""
    if not stats.reachable:
        return 1
    if stats.pod_failed > 0:
        return 2
    if stats.pod_pending > 0:
        return 3
    if stats.pod_count == 0:
        return 4
    return 5
