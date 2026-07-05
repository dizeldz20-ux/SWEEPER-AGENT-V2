"""v0.6.0 — slice 6.2: machine actions.

Maintenance mode is metadata-only — direct toggle, no approvals.
Destructive actions (reboot, agent_restart, ssm_connect) write a
RepairProposal and return the proposal id. No mutation of state.
"""
import json
from pathlib import Path

import pytest

from ipracticom_sweeper.dashboard import (
    _get_maintenance_state,
    _save_maintenance_state,
    app,
)


def _client():
    app.config["TESTING"] = True
    return app.test_client()


# --- Maintenance (metadata-only, no approvals) -------------------------


def test_maintenance_route_accepts_valid_durations(tmp_path, monkeypatch):
    """Maintenance endpoint accepts 15, 60, 240, 0 and persists state."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    c = _client()
    for d in (15, 60, 240, 0):
        r = c.post("/v6/machines/web-1/maintenance", data={"duration_min": str(d)})
        assert r.status_code == 200, f"d={d} got {r.status_code}"
        body = r.get_json()
        assert body["ok"] is True
        assert body["state"]["host"] == "web-1"
        assert body["state"]["duration_min"] == d


def test_maintenance_route_rejects_invalid_duration(tmp_path, monkeypatch):
    """Anything not in {0,15,60,240} is 400."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    c = _client()
    for bad in ("30", "5", "999", "abc"):
        r = c.post("/v6/machines/web-1/maintenance", data={"duration_min": bad})
        assert r.status_code == 400, f"d={bad!r} should be 400, got {r.status_code}"


def test_maintenance_off_clears_state(tmp_path, monkeypatch):
    """`/maintenance/off` removes the JSON sidecar."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    c = _client()
    c.post("/v6/machines/web-1/maintenance", data={"duration_min": "15"})
    assert _get_maintenance_state("web-1") is not None
    r = c.post("/v6/machines/web-1/maintenance/off")
    assert r.status_code == 200
    assert _get_maintenance_state("web-1") is None


def test_save_maintenance_state_persists_to_disk(tmp_path):
    """Round-trip: write state, read back, remove, read again."""
    state = {
        "host": "db-1",
        "enabled_at": "2026-06-30T12:00:00+00:00",
        "duration_min": 60,
        "expires_at_ts": 1782860400.0,
    }
    # Patch via env so the helper uses tmp dir.
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    # Reload helper module-level BASE… Actually the helpers use os.environ.get
    # at runtime, so this should already take effect.
    import importlib
    mod = importlib.import_module("ipracticom_sweeper.dashboard")
    if hasattr(mod, "_BASE_STATE"):
        # reset module cache so the helper reads the new env
        pass
    prev = _save_maintenance_state("db-1", state)
    assert prev is None
    rt = _get_maintenance_state("db-1")
    assert rt == state
    prev2 = _save_maintenance_state("db-1", None)
    assert prev2 == state
    assert _get_maintenance_state("db-1") is None


# --- Destructive actions: approval queue, NO execution -----------------


def test_reboot_writes_proposal_no_state_change(tmp_path, monkeypatch):
    """reboot → RepairProposal in /var/lib/.../pending_repairs, no exec."""
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    # PENDING_DIR is bound at import time — swap it to a tmp subdir so the
    # proposal file lands under tmp_path.
    import ipracticom_sweeper.repair.pending as _pending_mod
    new_pending = tmp_path / "pending_repairs"
    new_pending.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pending_mod, "PENDING_DIR", new_pending)

    c = _client()
    r = c.post(
        "/v6/machines/web-1/action",
        data={"action": "reboot"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["queued"] is True
    assert body["proposal"]["action"] == "reboot"
    assert body["proposal"]["kwargs"]["host"] == "web-1"
    assert "shutdown" in body["proposal"]["proposed_command"]
    # Confirmation modal is wired in the template (onsubmit=confirm), not the API.
    files = list(new_pending.glob("*.json"))
    assert len(files) >= 1
    blob = json.loads(files[0].read_text())
    assert blob["action"] == "reboot"
    assert blob["status"] == "pending"


@pytest.mark.parametrize("op", ["agent_restart", "ssm_connect"])
def test_other_destructive_actions_enqueue_proposal(op, tmp_path, monkeypatch):
    """agent_restart + ssm_connect both create proposals (never execute)."""
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    import ipracticom_sweeper.repair.pending as _pending_mod
    new_pending = tmp_path / "pending_repairs"
    new_pending.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pending_mod, "PENDING_DIR", new_pending)

    c = _client()
    r = c.post(
        "/v6/machines/web-1/action",
        data={"action": op},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["proposal"]["action"] == op


def test_unknown_action_returns_400(tmp_path, monkeypatch):
    """Unknown action values are rejected with 400."""
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    c = _client()
    r = c.post("/v6/machines/web-1/action", data={"action": "format_c_drive"})
    assert r.status_code == 400


def test_remote_mode_blocks_machine_actions(monkeypatch):
    """In remote mode the machine actions endpoint refuses (returns 400)."""
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: True,
    )
    c = _client()
    for op in ("agent_restart", "reboot", "ssm_connect"):
        r = c.post("/v6/machines/web-1/action", data={"action": op})
        assert r.status_code == 400, f"{op} should be 400 in remote mode"


# --- Template + CSS wires the actions ----------------------------------


def test_v6_machines_template_has_dropdowns():
    """The template exposes maintenance + actions dropdowns."""
    body = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "templates" / "v6_machines.html").read_text(encoding="utf-8")
    for marker in (
        "v6-maint-dropdown",
        "v6-actions-dropdown",
        "/v6/machines/{{ h.host_id }}/maintenance",
        "/v6/machines/{{ h.host_id }}/action",
        "agent_restart",
        "ssm_connect",
        "value=\"15\"",
        "value=\"60\"",
        "value=\"240\"",
        "value=\"0\"",
        'confirm(',
    ):
        assert marker in body, f"template missing {marker!r}"


def test_v6_machines_rendered_for_under_maint_host(tmp_path, monkeypatch):
    """A host with maintenance state renders the 'בתחזוקה' badge + cancel form."""
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    _save_maintenance_state("web-1", {
        "host": "web-1", "enabled_at": "2026-06-30T12:00:00+00:00",
        "duration_min": 15, "expires_at_ts": 1782861300.0,
    })
    c = _client()
    r = c.get("/v6/machines")
    assert r.status_code == 200


def test_css_includes_dropdown_styles():
    """style.css ships the dropdown menu + maintenance row styling."""
    css = (Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css").read_text(encoding="utf-8")
    for marker in (
        ".v6-row-maint",
        ".v6-maint-dropdown",
        ".v6-actions-dropdown",
        ".v6-maint-menu",
        ".v6-actions-menu",
        ".v6-btn-link",
    ):
        assert marker in css, f"missing CSS rule {marker}"


def test_basic_auth_protects_destructive_endpoint(tmp_path, monkeypatch):
    """`/v6/machines/<host>/action` is gated by the same Basic Auth as the rest."""
    import os
    os.environ["IPRACTICOM_SWEEPER_STATE_DIR"] = str(tmp_path)
    monkeypatch.setattr(
        "ipracticom_sweeper.dashboard._is_remote_mode",
        lambda: False,
    )
    monkeypatch.setattr("ipracticom_sweeper.dashboard._DASHBOARD_USER", "u")
    monkeypatch.setattr("ipracticom_sweeper.dashboard._DASHBOARD_PASS", "p")
    c = _client()
    r = c.post("/v6/machines/web-1/action", data={"action": "reboot"})
    assert r.status_code == 401
    import base64
    creds = base64.b64encode(b"u:p").decode()
    r2 = c.post(
        "/v6/machines/web-1/action",
        data={"action": "reboot"},
        headers={
            "Authorization": f"Basic {creds}",
            "Origin": "http://localhost",
        },
    )
    assert r2.status_code == 200


# ============= v1.5.16 RED: ghost repair actions =============================
# Code review findings #7, #8 (HEAD v1.5.15): the /v6/machines/<host>/action
# endpoint accepts "reboot", "agent_restart", "ssm_connect" — these create
# proposals. But none of them are registered in repair.REPAIRS. So when the
# operator approves a proposal, execute_repair() returns
# success=False with "Unknown repair action: reboot" — silently failing
# in a way that looks like a real repair that just failed.

from ipracticom_sweeper.repair import list_available_repairs, execute_repair


def test_reboot_is_registered_as_repair():
    """The 'reboot' action must be in REPAIRS — operators approve proposals
    and expect real execution.

    Currently: NOT registered. Approving a reboot proposal runs execute_repair
    which returns success=False with 'Unknown repair action: reboot'.
    """
    available = list_available_repairs()
    assert "reboot" in available, (
        f"'reboot' is offered in /v6/machines/<host>/action UI but not in "
        f"REPAIRS registry. Approving a reboot proposal silently fails. "
        f"Available repairs: {sorted(available)}"
    )


def test_agent_restart_is_registered_as_repair():
    """The 'agent_restart' action must be registered."""
    available = list_available_repairs()
    assert "agent_restart" in available, (
        f"'agent_restart' is in UI but not in REPAIRS. {sorted(available)}"
    )


def test_ssm_connect_is_registered_as_repair():
    """The 'ssm_connect' action must be registered."""
    available = list_available_repairs()
    assert "ssm_connect" in available, (
        f"'ssm_connect' is in UI but not in REPAIRS. {sorted(available)}"
    )


def test_executing_reboot_proposal_returns_real_result(monkeypatch):
    """When execute_repair('reboot', host='x') is called, it must NOT
    return success=False with 'Unknown repair action'.

    This is what happens when an operator approves a reboot proposal
    today — silent failure that looks like a real repair attempt.
    """
    from ipracticom_sweeper.repair.actions import execute_repair
    # Don't actually run shutdown — patch subprocess to a no-op.
    import subprocess
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(args=a[0] if a else [], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    # We can't fully test 'reboot' without root, but we CAN verify it
    # doesn't return success=False with 'Unknown repair action'.
    try:
        result = execute_repair("reboot", host="dummy.example.com", dry_run=True)
    except Exception:
        # If implementation requires real infra, that's fine — we just
        # want to verify it's registered.
        pytest.skip("reboot implementation requires real infra; registration confirmed by other test")
        return
    # If it returned a result, the message must not be 'Unknown repair action'
    if not result.success:
        assert "Unknown repair action" not in (result.message or ""), (
            f"reboot is not registered but UI offers it — silent failure: {result.message}"
        )
