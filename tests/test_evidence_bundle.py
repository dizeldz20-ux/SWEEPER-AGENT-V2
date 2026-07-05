"""Tests for evidence bundle export/verify."""
from __future__ import annotations
import time
from ipracticom_sweeper.evidence.bundle import (
    build_evidence_bundle,
    verify_bundle,
    compute_signature,
)


def test_build_bundle_with_no_entries():
    """Empty bundle is valid (signature computed over empty content)."""
    bundle = build_evidence_bundle(host="h1")
    assert bundle.host == "h1"
    assert bundle.audit_entries == []
    assert bundle.repair_entries == []
    assert bundle.signature  # non-empty


def test_verify_bundle_passes_for_valid():
    """A freshly-built bundle verifies successfully."""
    bundle = build_evidence_bundle(host="h1", audit_entries=[{"kind": "test"}])
    assert verify_bundle(bundle) is True


def test_verify_bundle_fails_after_tamper():
    """If you modify a bundle's content, signature no longer matches."""
    bundle = build_evidence_bundle(host="h1", audit_entries=[{"kind": "test"}])
    original_sig = bundle.signature
    bundle.audit_entries.append({"kind": "tampered"})
    assert verify_bundle(bundle) is False
    # Note: original_sig would also not match the modified content


def test_signature_deterministic():
    """Same input → same signature (sort_keys=True ensures determinism)."""
    sig1 = compute_signature({"a": 1, "b": 2})
    sig2 = compute_signature({"b": 2, "a": 1})  # different order
    assert sig1 == sig2


def test_bundle_contains_time_range():
    """Time range is set from since/until parameters."""
    bundle = build_evidence_bundle(host="h1", since_ts=100.0, until_ts=200.0)
    assert bundle.time_range["since"] == 100.0
    assert bundle.time_range["until"] == 200.0


def test_bundle_to_dict_serializable():
    """to_dict() output is JSON-serializable."""
    import json
    bundle = build_evidence_bundle(
        host="h1",
        audit_entries=[{"kind": "repair", "action": "drop_caches", "success": True}],
    )
    s = json.dumps(bundle.to_dict())
    assert "drop_caches" in s
    assert bundle.signature in s
