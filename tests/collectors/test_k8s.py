"""Tests for K8s collector (kubectl calls mocked)."""
import json
import pytest
from unittest.mock import patch, MagicMock
from ipracticom_sweeper.collectors import collect_k8s_stats, defcon_from_k8s, K8sStats


def _mock_pods():
    return {
        "items": [
            {"metadata": {"namespace": "default"}, "status": {"phase": "Running"}},
            {"metadata": {"namespace": "default"}, "status": {"phase": "Running"}},
            {"metadata": {"namespace": "kube-system"}, "status": {"phase": "Running"}},
            {"metadata": {"namespace": "app"}, "status": {"phase": "Pending"}},
            {"metadata": {"namespace": "app"}, "status": {"phase": "Failed"}},
        ]
    }


def _mock_run(stdout, returncode=0, stderr=""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def test_collect_k8s_stats_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(json.dumps(_mock_pods()))
        stats = collect_k8s_stats()
    assert stats.reachable is True
    assert stats.pod_count == 5
    assert stats.pod_running == 3
    assert stats.pod_pending == 1
    assert stats.pod_failed == 1
    assert stats.namespace_count == 3


def test_collect_k8s_stats_unreachable():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("", returncode=1, stderr="connection refused")
        stats = collect_k8s_stats()
    assert stats.reachable is False
    assert "connection refused" in stats.error


def test_collect_k8s_stats_timeout():
    import subprocess
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=10)
        stats = collect_k8s_stats()
    assert stats.reachable is False
    assert "timeout" in stats.error


def test_collect_k8s_stats_bad_json():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("not json")
        stats = collect_k8s_stats()
    assert stats.reachable is False
    assert "JSON" in stats.error


def test_collect_k8s_stats_with_namespace():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(json.dumps({"items": []}))
        collect_k8s_stats(namespace="production")
        args = mock_run.call_args.args[0]
        assert "-n" in args
        assert "production" in args
        assert "--all-namespaces" not in args


def test_defcon_unreachable():
    assert defcon_from_k8s(K8sStats(0, 0, 0, 0, 0, False)) == 1


def test_defcon_failed_pods():
    stats = K8sStats(1, 5, 4, 0, 1, True)
    assert defcon_from_k8s(stats) == 2


def test_defcon_pending_pods():
    stats = K8sStats(1, 5, 4, 1, 0, True)
    assert defcon_from_k8s(stats) == 3


def test_defcon_no_pods():
    stats = K8sStats(0, 0, 0, 0, 0, True)
    assert defcon_from_k8s(stats) == 4


def test_defcon_all_running():
    stats = K8sStats(1, 5, 5, 0, 0, True)
    assert defcon_from_k8s(stats) == 5
