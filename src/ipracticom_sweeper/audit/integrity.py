"""Sprint 13.3 — audit log integrity seal (HMAC-SHA256).

Each rotated audit file (audit.jsonl.N.gz) gets a sidecar .sig file
containing an HMAC-SHA256 of the gzip contents. The HMAC key comes from
AUDIT_SEAL_KEY env var. If unset, sealing is disabled (not failed).

Use:
  seal_audit_file(path) -> sig path  (creates .sig sidecar)
  verify_seal(path) -> bool           (recomputes + compares)
  IntegrityError is raised on seal mismatch when verify_strict=True.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Optional

SEAL_SUFFIX = ".sig"
ALGO = "hmac-sha256"


def _resolve_key(explicit: Optional[str] = None) -> Optional[bytes]:
    """Return the sealing key from explicit arg, env, or None (disabled)."""
    if explicit:
        return explicit.encode("utf-8")
    env_key = os.environ.get("AUDIT_SEAL_KEY", "").strip()
    if not env_key:
        return None
    return env_key.encode("utf-8")


def compute_seal(file_path: Path | str, key: bytes) -> str:
    """Compute the HMAC-SHA256 hex digest of a file's contents."""
    h = hmac.new(key, digestmod=hashlib.sha256)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def seal_audit_file(
    file_path: Path | str,
    key: Optional[str] = None,
) -> Optional[Path]:
    """Seal a rotated audit file with an HMAC-SHA256 sidecar.

    Returns the .sig path on success, or None if sealing is disabled
    (no key configured).
    """
    file_path = Path(file_path)
    k = _resolve_key(key)
    if k is None:
        return None

    sig = compute_seal(file_path, k)
    sig_path = file_path.with_name(file_path.name + SEAL_SUFFIX)
    sig_path.write_text(f"{ALGO}:{sig}\n")
    return sig_path


def verify_seal(
    file_path: Path | str,
    key: Optional[str] = None,
    strict: bool = False,
) -> bool:
    """Verify a sealed audit file.

    Returns True if:
      - sealing is disabled (no key), OR
      - the .sig sidecar exists and matches the file's HMAC

    Returns False if:
      - the .sig sidecar is missing
      - the sidecar exists but the digest doesn't match
      - the sidecar exists but is malformed

    If `strict=True` and the seal is invalid, raises IntegrityError.
    """
    file_path = Path(file_path)
    k = _resolve_key(key)
    if k is None:
        # Sealing is disabled — everything verifies by default
        return True

    sig_path = file_path.with_name(file_path.name + SEAL_SUFFIX)
    if not sig_path.exists():
        if strict:
            raise IntegrityError(f"No seal at {sig_path}")
        return False

    expected = compute_seal(file_path, k)
    actual = sig_path.read_text().strip()
    # Strip "hmac-sha256:" prefix if present
    if actual.startswith(f"{ALGO}:"):
        actual = actual[len(f"{ALGO}:"):]

    if not hmac.compare_digest(expected, actual):
        if strict:
            raise IntegrityError(f"Seal mismatch for {file_path}")
        return False
    return True


class IntegrityError(Exception):
    """Raised when audit log integrity verification fails (strict mode)."""
    pass


def seal_existing_rotations(
    state_dir: Path | str,
    key: Optional[str] = None,
) -> int:
    """Convenience: seal all audit.jsonl.N.gz files in state_dir.

    Returns the number of files sealed (0 if key not configured).
    """
    state_dir = Path(state_dir)
    audit_dir = state_dir / "audit"
    if not audit_dir.is_dir():
        return 0
    n = 0
    for f in sorted(audit_dir.glob("audit.jsonl.*.gz")):
        if seal_audit_file(f, key=key) is not None:
            n += 1
    return n
