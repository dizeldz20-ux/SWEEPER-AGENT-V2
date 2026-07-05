"""Evidence manifest signer: SHA256 over a snapshot, returns a tamper-evident receipt.

Used to prove that a snapshot uploaded to S3 is the exact bytes the agent emitted.
The manifest bundles: snapshot_id, host, timestamp, sha256 of the JSON body,
and an optional chain hash that ties to the previous manifest.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class EvidenceManifest:
    snapshot_id: str
    host: str
    timestamp: float
    body_sha256: str
    chain_sha256: str  # hash(previous_chain + body_sha256)
    body_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def hash_body(body: dict[str, Any] | str | bytes) -> str:
    """Return hex SHA256 of the canonical JSON serialization (or raw bytes)."""
    if isinstance(body, bytes):
        return hashlib.sha256(body).hexdigest()
    if isinstance(body, str):
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
    # dict -> canonical JSON (sorted keys, no spaces) for reproducibility
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ManifestSigner:
    """Maintains a chain of evidence manifests (each ties to the previous one)."""

    def __init__(self, host: str, genesis: str = "0" * 64):
        self.host = host
        self._last_chain = genesis
        self._signed: list[EvidenceManifest] = []

    @property
    def last_chain(self) -> str:
        return self._last_chain

    @property
    def signed_count(self) -> int:
        return len(self._signed)

    def sign(self, snapshot_id: str, body: dict[str, Any] | bytes) -> EvidenceManifest:
        """Sign a snapshot. Returns the manifest and updates the chain."""
        if isinstance(body, bytes):
            body_size = len(body)
            body_sha = hashlib.sha256(body).hexdigest()
        else:
            body_size = len(json.dumps(body))
            body_sha = hash_body(body)

        chain_input = self._last_chain + body_sha
        chain_sha = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

        manifest = EvidenceManifest(
            snapshot_id=snapshot_id,
            host=self.host,
            timestamp=time.time(),
            body_sha256=body_sha,
            chain_sha256=chain_sha,
            body_size=body_size,
        )
        self._signed.append(manifest)
        self._last_chain = chain_sha
        return manifest

    def verify_chain(self) -> bool:
        """Replay the chain and confirm each link hashes correctly."""
        prev = "0" * 64
        for m in self._signed:
            expected_chain = hashlib.sha256(
                (prev + m.body_sha256).encode("utf-8")
            ).hexdigest()
            if expected_chain != m.chain_sha256:
                return False
            prev = m.chain_sha256
        return True


def verify_manifest(manifest_dict: dict[str, Any], previous_chain: str) -> bool:
    """Verify a single manifest against its body hash and the previous chain link."""
    body_sha = manifest_dict.get("body_sha256", "")
    chain_sha = manifest_dict.get("chain_sha256", "")
    expected = hashlib.sha256(
        (previous_chain + body_sha).encode("utf-8")
    ).hexdigest()
    return hmac_compare(expected, chain_sha)


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0
