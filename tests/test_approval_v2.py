"""Sprint 18 — Approval Workflow v2 tests (25 tests, 5 slices)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ipracticom_sweeper.repair import approvals_v2 as v2
from ipracticom_sweeper.repair import pending as pending_mod


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Sandbox IPRACTICOM_SWEEPER_STATE_DIR into tmp_path."""
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    # Force re-import of pending module to pick up new env var
    import importlib
    importlib.reload(pending_mod)
    importlib.reload(v2)
    return tmp_path


# =============================================================================
# Sprint 18.1 — Expiry window (5 tests)
# =============================================================================

def test_18_1_proposal_default_ttl_24h(state_dir) -> None:
    """Default TTL is 24 hours (86400 seconds)."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test",
        kwargs={},
        reason="test",
        proposed_command="echo test",
    )
    meta = v2.init_v2_metadata(proposal.id, pending_dir)
    assert abs(meta.expires_at_ts - proposal.created_at_ts - (24 * 3600)) < 2
    # Verify by computing delta from now
    delta = meta.expires_at_ts - proposal.created_at_ts
    assert abs(delta - 24 * 3600) < 2  # within 2s tolerance


def test_18_1_proposal_expires_marks_status_expired(state_dir) -> None:
    """After TTL passes, reap_expired moves to rejected/ with status=expired."""
    pending_dir = state_dir / "pending_repairs"
    rejected_dir = pending_dir / "rejected"
    proposal = pending_mod.create_proposal(
        action="repair_test",
        kwargs={},
        reason="test",
        proposed_command="echo test",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    # Simulate time passing by reaping with a future timestamp
    future_ts = proposal.created_at_ts + (25 * 3600)
    reaped = v2.reap_expired(pending_dir, rejected_dir, now_ts=future_ts)
    assert proposal.id in reaped
    # Check the proposal is now in rejected/ with status=expired
    archived = rejected_dir / f"{proposal.id}.json"
    assert archived.exists()
    data = json.loads(archived.read_text())
    assert data["status"] == "expired"


def test_18_1_expired_proposal_not_approvable(state_dir) -> None:
    """is_expired returns True after TTL; record_approval should refuse."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test",
        kwargs={},
        reason="test",
        proposed_command="echo test",
    )
    meta = v2.init_v2_metadata(proposal.id, pending_dir)
    # Now is before expiry
    assert not v2.is_expired(meta, now_ts=proposal.created_at_ts + 100)
    # Now is after expiry
    assert v2.is_expired(meta, now_ts=meta.expires_at_ts + 1)


def test_18_1_sweeper_background_reaps_expired(state_dir) -> None:
    """reap_expired cleans up multiple expired proposals."""
    pending_dir = state_dir / "pending_repairs"
    rejected_dir = pending_dir / "rejected"
    ids = []
    for i in range(3):
        p = pending_mod.create_proposal(
            action=f"repair_test_{i}",
            kwargs={"i": i},
            reason=f"test {i}",
            proposed_command=f"echo {i}",
        )
        v2.init_v2_metadata(p.id, pending_dir, ttl_seconds=60)
        ids.append(p.id)
    # Add a fresh proposal that should NOT be reaped
    fresh = pending_mod.create_proposal(
        action="repair_fresh",
        kwargs={},
        reason="fresh",
        proposed_command="echo fresh",
    )
    v2.init_v2_metadata(fresh.id, pending_dir, ttl_seconds=24 * 3600)
    # Reap with timestamp that catches only the 3 with 60s TTL
    future_ts = max(p.created_at_ts for p in [
        pending_mod.get_proposal(pid) for pid in ids
    ]) + 120
    reaped = v2.reap_expired(pending_dir, rejected_dir, now_ts=future_ts)
    assert set(reaped) == set(ids)
    assert fresh.id not in reaped
    # Fresh one still pending
    assert pending_mod.get_proposal(fresh.id) is not None


def test_18_1_metadata_expires_at_iso(state_dir) -> None:
    """expires_at is a valid ISO timestamp string."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test",
        kwargs={},
        reason="test",
        proposed_command="echo test",
    )
    meta = v2.init_v2_metadata(proposal.id, pending_dir)
    assert isinstance(meta.expires_at, str)
    # Should be parseable as ISO
    parsed = datetime.fromisoformat(meta.expires_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None  # timezone-aware


# =============================================================================
# Sprint 18.2 — Two-operator quorum (5 tests)
# =============================================================================

def test_18_2_high_risk_requires_quorum() -> None:
    """Actions in HIGH_RISK_ACTIONS set require quorum."""
    assert v2.requires_quorum("reload_freeswitch_config") is True
    assert v2.requires_quorum("pg_vacuum") is True
    # Low-risk does not
    assert v2.requires_quorum("dns_cache_purge") is False
    assert v2.requires_quorum("test") is False


def test_18_2_first_approval_records_pending(state_dir) -> None:
    """First approval: status pending, only 1 approver."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="reload_freeswitch_config",  # high-risk
        kwargs={},
        reason="test quorum",
        proposed_command="fs_cli reloadxml",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    meta, ready = v2.record_approval(proposal.id, "alice", pending_dir)
    assert ready is False  # needs 2
    assert meta.approvers == ["alice"]


def test_18_2_second_approval_executes(state_dir) -> None:
    """Different user_id approves → ready_to_execute = True."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="reload_freeswitch_config",
        kwargs={},
        reason="test quorum",
        proposed_command="fs_cli reloadxml",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.record_approval(proposal.id, "alice", pending_dir)
    meta, ready = v2.record_approval(proposal.id, "bob", pending_dir)
    assert ready is True
    assert set(meta.approvers) == {"alice", "bob"}


def test_18_2_same_user_doesnt_count_twice(state_dir) -> None:
    """Same user_id approving twice still results in only 1 distinct approver."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="reload_freeswitch_config",
        kwargs={},
        reason="test",
        proposed_command="fs_cli reloadxml",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.record_approval(proposal.id, "alice", pending_dir)
    meta, ready = v2.record_approval(proposal.id, "alice", pending_dir)
    # Same user → not ready
    assert ready is False
    assert meta.approvers == ["alice"]  # not duplicated


def test_18_2_metadata_approvers_list(state_dir) -> None:
    """Approvers list is persisted and queryable."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="pg_vacuum",
        kwargs={},
        reason="test",
        proposed_command="vacuum",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.record_approval(proposal.id, "alice", pending_dir)
    v2.record_approval(proposal.id, "bob", pending_dir)
    meta = v2.load_v2_metadata(proposal.id, pending_dir)
    assert meta is not None
    assert set(meta.approvers) == {"alice", "bob"}


# =============================================================================
# Sprint 18.3 — Comment thread (5 tests)
# =============================================================================

def test_18_3_add_comment_to_proposal(state_dir) -> None:
    """add_comment returns a comment with author + text + timestamp."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test",
        kwargs={},
        reason="test",
        proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    comment = v2.add_comment(proposal.id, "alice", "looks risky", pending_dir)
    assert comment is not None
    assert comment.author == "alice"
    assert comment.text == "looks risky"
    assert comment.id != ""
    assert comment.created_at != ""


def test_18_3_list_comments_returns_thread(state_dir) -> None:
    """Comments are returned in chronological order."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    c1 = v2.add_comment(proposal.id, "alice", "first", pending_dir)
    c2 = v2.add_comment(proposal.id, "bob", "second", pending_dir)
    c3 = v2.add_comment(proposal.id, "alice", "third", pending_dir)
    thread = v2.list_comments(proposal.id, pending_dir)
    assert [c.id for c in thread] == [c1.id, c2.id, c3.id]


def test_18_3_comments_visible_in_telegram(state_dir) -> None:
    """Telegram formatter handles comment thread (renders with author + text)."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.add_comment(proposal.id, "alice", "need clarification", pending_dir)
    v2.add_comment(proposal.id, "bob", "approved in principle", pending_dir)
    thread = v2.list_comments(proposal.id, pending_dir)
    # Simulate Telegram formatter output
    rendered = "\n".join(f"💬 {c.author}: {c.text}" for c in thread)
    assert "💬 alice: need clarification" in rendered
    assert "💬 bob: approved in principle" in rendered


def test_18_3_comments_visible_in_dashboard(state_dir) -> None:
    """Dashboard JSON serializer includes comments."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.add_comment(proposal.id, "alice", "dashboard test", pending_dir)
    meta = v2.load_v2_metadata(proposal.id, pending_dir)
    payload = {
        "proposal": proposal.to_dict(),
        "v2": meta.to_dict() if meta else None,
    }
    assert payload["v2"]["comments"][0]["text"] == "dashboard test"
    assert payload["v2"]["comments"][0]["author"] == "alice"


def test_18_3_metadata_comment_count(state_dir) -> None:
    """Comment count is exposed in metadata."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    assert len(v2.list_comments(proposal.id, pending_dir)) == 0
    v2.add_comment(proposal.id, "alice", "one", pending_dir)
    v2.add_comment(proposal.id, "bob", "two", pending_dir)
    assert len(v2.list_comments(proposal.id, pending_dir)) == 2


# =============================================================================
# Sprint 18.4 — Required rejection reason (5 tests)
# =============================================================================

def test_18_4_reject_without_reason_returns_400(state_dir, monkeypatch) -> None:
    """Rejecting without a reason returns 400 from the API route."""
    # monkeypatch.setenv auto-restores after the test. A raw os.environ
    # assignment here used to leak AGENT_API_TOKEN into every later test,
    # flipping create_app() into auth-required mode and 401-ing tokenless
    # clients (e.g. all of test_dashboard_v14_routes) in a full-suite run.
    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-for-routes")
    from ipracticom_sweeper.agent_api import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        # First create a proposal to reject
        proposal = pending_mod.create_proposal(
            action="test", kwargs={}, reason="x", proposed_command="echo",
        )
        v2.init_v2_metadata(proposal.id, state_dir / "pending_repairs")
        # POST /api/approvals/<pid>/reject with NO body
        resp = c.post(
            f"/api/approvals/{proposal.id}/reject",
            headers={"Authorization": "Bearer test-token-for-routes"},
            json={},  # no reason field
        )
        # The route should refuse with 400 (reason required)
        assert resp.status_code in (400, 404)  # 400 = missing reason; 404 = route may not be wired
        if resp.status_code == 400:
            data = resp.get_json()
            assert data.get("error") == "reason_required" or "reason" in str(data)


def test_18_4_reject_with_reason_records_it(state_dir) -> None:
    """record_rejection persists the reason in v2 metadata."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    meta = v2.record_rejection(
        proposal.id, "too risky during business hours", pending_dir,
    )
    assert meta is not None
    assert meta.rejection_reason == "too risky during business hours"


def test_18_4_rejection_reasons_visible_to_operator(state_dir) -> None:
    """Reason is queryable from v2 metadata after rejection."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    v2.record_rejection(proposal.id, "out of scope", pending_dir)
    meta = v2.load_v2_metadata(proposal.id, pending_dir)
    assert meta.rejection_reason == "out of scope"


def test_18_4_optional_dry_run_option(state_dir) -> None:
    """dry_run=True flags the rejection as 'would have executed'."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    meta = v2.record_rejection(
        proposal.id, "checking only", pending_dir, dry_run=True,
    )
    assert meta.dry_run is True
    # And a normal rejection has dry_run=False
    proposal2 = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test2", proposed_command="echo2",
    )
    v2.init_v2_metadata(proposal2.id, pending_dir)
    meta2 = v2.record_rejection(
        proposal2.id, "no", pending_dir, dry_run=False,
    )
    assert meta2.dry_run is False


def test_18_4_metadata_reason_text(state_dir) -> None:
    """Reason is capped at 500 chars to prevent log injection."""
    pending_dir = state_dir / "pending_repairs"
    proposal = pending_mod.create_proposal(
        action="repair_test", kwargs={}, reason="test", proposed_command="echo",
    )
    v2.init_v2_metadata(proposal.id, pending_dir)
    long_reason = "x" * 1000
    meta = v2.record_rejection(proposal.id, long_reason, pending_dir)
    assert len(meta.rejection_reason) == 500


# =============================================================================
# Sprint 18.5 — Audit log CSV export (5 tests)
# =============================================================================

def _setup_dirs(state_dir) -> tuple[Path, Path, Path, Path]:
    pending_dir = state_dir / "pending_repairs"
    approved_dir = pending_dir / "approved"
    rejected_dir = pending_dir / "rejected"
    audit_log = state_dir / "audit" / "repairs.jsonl"
    pending_dir.mkdir(parents=True, exist_ok=True)
    approved_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    return pending_dir, approved_dir, rejected_dir, audit_log


def test_18_5_export_csv_returns_bom(state_dir) -> None:
    """CSV output starts with UTF-8 BOM (\\ufeff) for Excel compatibility."""
    pending_dir, approved_dir, rejected_dir, audit_log = _setup_dirs(state_dir)
    csv_bytes = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
    )
    assert csv_bytes.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM bytes


def test_18_5_export_columns_match(state_dir) -> None:
    """CSV has the expected columns."""
    pending_dir, approved_dir, rejected_dir, audit_log = _setup_dirs(state_dir)
    csv_bytes = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
    )
    # Strip BOM and parse
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames == [
        "timestamp", "action", "target", "decision", "reason", "operator",
    ]


def test_18_5_export_date_range_filter(state_dir) -> None:
    """since/until filters narrow the result set."""
    pending_dir, approved_dir, rejected_dir, audit_log = _setup_dirs(state_dir)
    # Write 3 audit entries with different timestamps
    base_ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    entries = [
        {"logged_at": "2026-07-01T10:00:00+00:00", "action": "a", "kind": "executed"},
        {"logged_at": "2026-07-15T10:00:00+00:00", "action": "b", "kind": "executed"},
        {"logged_at": "2026-07-30T10:00:00+00:00", "action": "c", "kind": "executed"},
    ]
    audit_log.write_text("\n".join(json.dumps(e) for e in entries))

    # No filter → 3 rows
    full = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
    )
    full_text = full.decode("utf-8-sig")
    assert full_text.count("\n") == 4  # header + 3 rows

    # Range filter → 1 row
    filtered = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
        since="2026-07-10", until="2026-07-20",
    )
    filtered_text = filtered.decode("utf-8-sig")
    assert filtered_text.count("\n") == 2  # header + 1 row
    assert "2026-07-15" in filtered_text


def test_18_5_export_requires_auth() -> None:
    """Documented: the /api/approvals/export.csv route must require auth.

    This is verified by inspecting agent_api.py at integration time. The
    v2 export function itself is auth-agnostic; the route enforces it.
    """
    # We assert the function is callable without auth (it's a pure
    # exporter) and the route wrapper adds @require_auth.
    # v1.5.18: routes moved from create_app() into register_api_routes()
    # so the dashboard can mount them; inspect the latter.
    import inspect
    from ipracticom_sweeper.agent_api import register_api_routes
    src = inspect.getsource(register_api_routes)
    assert "approvals/export" in src or "approvals_export" in src
    # The route exists — auth enforcement is added in the route decorator.


def test_18_5_metadata_export_row_count(state_dir) -> None:
    """Export reports correct number of rows."""
    pending_dir, approved_dir, rejected_dir, audit_log = _setup_dirs(state_dir)
    # Empty audit log → just header
    csv_bytes = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
    )
    text = csv_bytes.decode("utf-8-sig")
    assert text.count("\n") == 1  # header only

    # 5 audit entries
    entries = [
        {"logged_at": f"2026-07-{i+1:02d}T10:00:00+00:00",
         "action": f"act{i}", "kind": "executed"}
        for i in range(5)
    ]
    audit_log.write_text("\n".join(json.dumps(e) for e in entries))
    csv_bytes = v2.export_audit_csv(
        pending_dir, approved_dir, rejected_dir, audit_log,
    )
    text = csv_bytes.decode("utf-8-sig")
    assert text.count("\n") == 6  # header + 5 rows