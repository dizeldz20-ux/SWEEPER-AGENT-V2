"""Tests for pending repair approval flow."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.repair import pending


@pytest.fixture
def pending_dirs(tmp_path, monkeypatch):
    """Redirect pending dirs to a tmp path so we don't touch real state."""
    pending_dir = tmp_path / "pending_repairs"
    approved_dir = pending_dir / "approved"
    rejected_dir = pending_dir / "rejected"
    audit_log = tmp_path / "audit" / "repairs.jsonl"

    monkeypatch.setattr(pending, "PENDING_DIR", pending_dir)
    monkeypatch.setattr(pending, "APPROVED_DIR", approved_dir)
    monkeypatch.setattr(pending, "REJECTED_DIR", rejected_dir)
    monkeypatch.setattr(pending, "AUDIT_LOG", audit_log)

    return {
        "pending": pending_dir,
        "approved": approved_dir,
        "rejected": rejected_dir,
        "audit": audit_log,
    }


def test_create_proposal_writes_file(pending_dirs):
    p = pending.create_proposal(
        action="service_restart",
        kwargs={"unit": "nginx"},
        reason="nginx is down",
        problem={"kind": "service_down", "severity": "crit", "detail": "x"},
        proposed_command="systemctl restart nginx",
    )
    assert p.id
    assert p.action == "service_restart"
    assert p.status == "pending"
    assert p.created_at
    assert (pending_dirs["pending"] / f"{p.id}.json").exists()


def test_list_pending_returns_newest_first(pending_dirs):
    import time
    p1 = pending.create_proposal(action="service_restart", kwargs={"unit": "a"}, reason="x", proposed_command="x")
    time.sleep(0.01)
    p2 = pending.create_proposal(action="service_restart", kwargs={"unit": "b"}, reason="y", proposed_command="y")
    out = pending.list_pending()
    assert [x.id for x in out] == [p2.id, p1.id]


def test_get_proposal_round_trip(pending_dirs):
    p = pending.create_proposal(action="service_restart", kwargs={"unit": "x"}, reason="r", proposed_command="c")
    out = pending.get_proposal(p.id)
    assert out is not None
    assert out.id == p.id
    assert out.kwargs == {"unit": "x"}


def test_get_proposal_missing_returns_none(pending_dirs):
    assert pending.get_proposal("nonexistent") is None


def test_set_status_updates_in_place(pending_dirs):
    p = pending.create_proposal(action="service_restart", kwargs={"unit": "x"}, reason="r", proposed_command="c")
    out = pending.set_status(p.id, "approved")
    assert out.status == "approved"
    re_read = pending.get_proposal(p.id)
    assert re_read.status == "approved"


def test_archive_moves_file(pending_dirs):
    p = pending.create_proposal(action="service_restart", kwargs={"unit": "x"}, reason="r", proposed_command="c")
    assert (pending_dirs["pending"] / f"{p.id}.json").exists()
    assert pending.archive(p.id, "approved") is True
    assert not (pending_dirs["pending"] / f"{p.id}.json").exists()
    assert (pending_dirs["approved"] / f"{p.id}.json").exists()


def test_log_audit_appends_jsonl(pending_dirs):
    pending.log_audit({"kind": "test", "action": "service_restart"})
    pending.log_audit({"kind": "test", "action": "drop_caches"})
    lines = pending_dirs["audit"].read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        d = json.loads(line)
        assert "logged_at" in d
        assert d["kind"] == "test"


def test_cleanup_stale_archives_old(pending_dirs):
    p = pending.create_proposal(action="service_restart", kwargs={"unit": "old"}, reason="r", proposed_command="c")
    # Make it look ancient — set timestamp to 10 years ago (not 0, which
    # the cleanup function deliberately skips as "unknown age").
    old_path = pending_dirs["pending"] / f"{p.id}.json"
    data = json.loads(old_path.read_text())
    data["created_at_ts"] = time.time() - (10 * 365 * 24 * 3600)  # 10 years ago
    old_path.write_text(json.dumps(data))
    moved = pending.cleanup_stale_pending(max_age_seconds=1)
    assert moved == 1
    assert not old_path.exists()
    assert (pending_dirs["rejected"] / f"{p.id}.json").exists()


def test_cleanup_stale_keeps_fresh(pending_dirs):
    import time
    p = pending.create_proposal(action="service_restart", kwargs={"unit": "new"}, reason="r", proposed_command="c")
    moved = pending.cleanup_stale_pending(max_age_seconds=3600)
    assert moved == 0
    assert (pending_dirs["pending"] / f"{p.id}.json").exists()


# --- server field + dedup (multi-channel approval flow) --------------------


def test_create_proposal_stores_server(pending_dirs):
    p = pending.create_proposal(
        action="drop_caches", kwargs={"level": 3}, reason="mem",
        proposed_command="echo 3 > /proc/sys/vm/drop_caches",
        server="prod-pbx-01",
    )
    out = pending.get_proposal(p.id)
    assert out is not None
    assert out.server == "prod-pbx-01"


def test_proposal_without_server_defaults_empty(pending_dirs):
    """An old proposal file lacking the `server` field still loads."""
    p = pending.create_proposal(action="drop_caches", kwargs={}, reason="r", proposed_command="c")
    path = pending_dirs["pending"] / f"{p.id}.json"
    data = json.loads(path.read_text())
    data.pop("server", None)  # simulate a proposal written before the field existed
    path.write_text(json.dumps(data))
    out = pending.get_proposal(p.id)
    assert out is not None
    assert out.server == ""


def test_find_pending_matches_action_and_server(pending_dirs):
    pending.create_proposal(
        action="service_restart", kwargs={"unit": "nginx"}, reason="r",
        proposed_command="c", server="web-01",
    )
    found = pending.find_pending("service_restart", "web-01")
    assert found is not None
    assert found.action == "service_restart"
    assert found.server == "web-01"


def test_find_pending_no_match_returns_none(pending_dirs):
    pending.create_proposal(
        action="service_restart", kwargs={"unit": "nginx"}, reason="r",
        proposed_command="c", server="web-01",
    )
    assert pending.find_pending("service_restart", "web-02") is None
    assert pending.find_pending("drop_caches", "web-01") is None