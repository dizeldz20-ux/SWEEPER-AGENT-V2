"""Local evidence export: bundle of audit log + repairs history.

Produces a self-contained evidence bundle (JSON) that includes:
- All audit log entries (repairs, problems, etc.)
- Current snapshot (or last N snapshots)
- Signature for integrity verification
- Bundle metadata (host, version, time range)

This is local-only (no S3 dependency). For cloud export, see
evidence/exporter.py (S3Exporter).
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvidenceBundle:
    """A self-contained evidence bundle."""

    host: str
    agent_version: str
    created_at: float
    time_range: dict[str, float | None]
    audit_entries: list[dict[str, Any]]
    repair_entries: list[dict[str, Any]]
    snapshot_summary: dict[str, Any]
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "agent_version": self.agent_version,
            "created_at": self.created_at,
            "time_range": self.time_range,
            "audit_entries": self.audit_entries,
            "repair_entries": self.repair_entries,
            "snapshot_summary": self.snapshot_summary,
            "signature": self.signature,
        }


def compute_signature(payload: dict[str, Any]) -> str:
    """Compute SHA-256 signature over a deterministic JSON dump."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_evidence_bundle(
    host: str = "localhost",
    agent_version: str = "0.4.0",
    audit_entries: list[dict[str, Any]] | None = None,
    repair_entries: list[dict[str, Any]] | None = None,
    snapshot_summary: dict[str, Any] | None = None,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> EvidenceBundle:
    """Build an evidence bundle from raw entries."""
    now = time.time()
    bundle = EvidenceBundle(
        host=host,
        agent_version=agent_version,
        created_at=now,
        time_range={"since": since_ts, "until": until_ts},
        audit_entries=audit_entries or [],
        repair_entries=repair_entries or [],
        snapshot_summary=snapshot_summary or {},
        signature="",
    )
    # Sign over all fields except signature itself
    payload = bundle.to_dict()
    payload.pop("signature")
    bundle.signature = compute_signature(payload)
    return bundle


def export_bundle_to_json(bundle: EvidenceBundle, path: Path | str) -> Path:
    """Write bundle to a JSON file. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(bundle.to_dict(), f, indent=2, default=str)
    return path


def verify_bundle(bundle: EvidenceBundle) -> bool:
    """Verify a bundle's signature matches its content."""
    payload = bundle.to_dict()
    payload.pop("signature")
    expected = compute_signature(payload)
    return expected == bundle.signature
