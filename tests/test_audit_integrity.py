"""Sprint 13.3 — audit log integrity seal tests."""
from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from ipracticom_sweeper.audit.integrity import (
    compute_seal,
    seal_audit_file,
    verify_seal,
    seal_existing_rotations,
    IntegrityError,
    SEAL_SUFFIX,
    ALGO,
)


KEY = "supersecret-test-key-32-bytes-ok"


def _write_audit_gz(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.write(content)


# ============= Basic sealing ================================================

def test_seal_creates_sig_sidecar(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, '{"e":1}\n')
    sig = seal_audit_file(f, key=KEY)
    assert sig is not None
    assert sig.exists()
    assert sig.name == f"audit.jsonl.1.gz{SEAL_SUFFIX}"


def test_seal_writes_algorithm_prefix(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    seal_audit_file(f, key=KEY)
    sig_text = (tmp_path / f"audit.jsonl.1.gz{SEAL_SUFFIX}").read_text()
    assert sig_text.startswith(f"{ALGO}:")


def test_seal_returns_none_without_key(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    # No explicit key, env not set
    os.environ.pop("AUDIT_SEAL_KEY", None)
    result = seal_audit_file(f)
    assert result is None
    # No .sig should be created
    assert not (tmp_path / f"audit.jsonl.1.gz{SEAL_SUFFIX}").exists()


def test_seal_uses_explicit_key(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "hello")
    sig = seal_audit_file(f, key=KEY)
    assert sig is not None
    # Recompute manually
    import hmac, hashlib
    h = hmac.new(KEY.encode(), f.read_bytes(), hashlib.sha256).hexdigest()
    assert f"{ALGO}:{h}" in sig.read_text()


def test_seal_uses_env_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    monkeypatch.setenv("AUDIT_SEAL_KEY", KEY)
    sig = seal_audit_file(f)
    assert sig is not None


def test_seal_handles_no_env_no_key(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    os.environ.pop("AUDIT_SEAL_KEY", None)
    assert seal_audit_file(f) is None


# ============= Verification ==================================================

def test_verify_recovers_original(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, '{"e":1}\n{"e":2}\n')
    seal_audit_file(f, key=KEY)
    assert verify_seal(f, key=KEY) is True


def test_verify_detects_tampered(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, '{"e":1}\n')
    seal_audit_file(f, key=KEY)
    # Tamper: append a line
    with open(f, "ab") as fp:
        fp.write(b"\n")
    assert verify_seal(f, key=KEY) is False


def test_verify_detects_missing_seal(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    # No seal at all
    assert verify_seal(f, key=KEY) is False


def test_verify_returns_true_when_disabled(tmp_path: Path) -> None:
    """No key → sealing disabled → verify always True."""
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    os.environ.pop("AUDIT_SEAL_KEY", None)
    assert verify_seal(f) is True


def test_verify_wrong_key_fails(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    seal_audit_file(f, key=KEY)
    # Verify with a different key
    assert verify_seal(f, key="other-key") is False


def test_verify_strict_raises_on_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    seal_audit_file(f, key=KEY)
    # Tamper
    with open(f, "ab") as fp:
        fp.write(b"extra")
    with pytest.raises(IntegrityError):
        verify_seal(f, key=KEY, strict=True)


def test_verify_strict_raises_on_missing(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    # No seal at all
    with pytest.raises(IntegrityError):
        verify_seal(f, key=KEY, strict=True)


def test_verify_strict_raises_on_malformed_sig(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    seal_audit_file(f, key=KEY)
    # Corrupt the .sig file
    sig_path = f.parent / (f.name + SEAL_SUFFIX)
    sig_path.write_text("not-a-hex-digest\n")
    with pytest.raises(IntegrityError):
        verify_seal(f, key=KEY, strict=True)


# ============= seal_existing_rotations ======================================

def test_seal_existing_rotations_count(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    for i in range(1, 4):
        _write_audit_gz(audit / f"audit.jsonl.{i}.gz", f"line {i}\n")
    n = seal_existing_rotations(tmp_path, key=KEY)
    assert n == 3


def test_seal_existing_rotations_no_audit_dir(tmp_path: Path) -> None:
    n = seal_existing_rotations(tmp_path, key=KEY)
    assert n == 0


def test_seal_existing_rotations_without_key(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    _write_audit_gz(audit / "audit.jsonl.1.gz", "x")
    os.environ.pop("AUDIT_SEAL_KEY", None)
    n = seal_existing_rotations(tmp_path)
    assert n == 0


# ============= Metadata / algorithm =========================================

def test_seal_metadata_algorithm_in_sig(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    sig = seal_audit_file(f, key=KEY)
    text = sig.read_text()
    assert ALGO in text


def test_compute_seal_returns_hex(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "hello")
    sig = compute_seal(f, KEY.encode())
    # SHA-256 hex is 64 hex chars
    assert len(sig) == 64
    int(sig, 16)  # parses as hex


def test_compute_seal_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "hello")
    a = compute_seal(f, KEY.encode())
    b = compute_seal(f, KEY.encode())
    assert a == b


def test_compute_seal_different_keys_differ(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    a = compute_seal(f, b"key1")
    b = compute_seal(f, b"key2")
    assert a != b


def test_seal_handles_empty_file(tmp_path: Path) -> None:
    """An empty gzipped file can still be sealed."""
    f = tmp_path / "audit.jsonl.1.gz"
    f.write_bytes(gzip.compress(b""))
    sig = seal_audit_file(f, key=KEY)
    assert sig is not None
    assert verify_seal(f, key=KEY) is True


def test_seal_does_not_modify_original(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl.1.gz"
    _write_audit_gz(f, "x")
    original_size = f.stat().st_size
    seal_audit_file(f, key=KEY)
    assert f.stat().st_size == original_size