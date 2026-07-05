"""Tests for evidence manifest signer (SHA256 + chain)."""
import pytest
from ipracticom_sweeper.evidence import (
    EvidenceManifest,
    ManifestSigner,
    hash_body,
    verify_manifest,
)


def test_hash_body_dict_is_deterministic():
    body = {"b": 2, "a": 1}
    h1 = hash_body(body)
    h2 = hash_body(body)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_hash_body_dict_canonical_order_independent():
    # sorted_keys=True in canonical JSON means key order doesn't matter
    assert hash_body({"a": 1, "b": 2}) == hash_body({"b": 2, "a": 1})


def test_hash_body_string():
    h1 = hash_body("hello")
    assert h1 == hash_body("hello")
    assert h1 != hash_body("world")


def test_hash_body_bytes():
    assert hash_body(b"hello") == hash_body("hello")
    assert hash_body(b"hello") != hash_body("world")


def test_signer_signs_first_manifest_with_genesis_chain():
    signer = ManifestSigner(host="h1")
    m = signer.sign("snap-1", {"defcon": 4})
    assert m.snapshot_id == "snap-1"
    assert m.host == "h1"
    assert m.body_sha256 == hash_body({"defcon": 4})
    # First chain: SHA256("0"*64 + body_sha)
    assert len(m.chain_sha256) == 64
    assert m.body_size > 0
    assert signer.signed_count == 1


def test_signer_chain_progresses():
    signer = ManifestSigner(host="h1")
    m1 = signer.sign("snap-1", {"x": 1})
    m2 = signer.sign("snap-2", {"x": 2})
    # m2's chain should depend on m1's chain
    assert m2.chain_sha256 != m1.chain_sha256
    assert signer.last_chain == m2.chain_sha256
    assert signer.signed_count == 2


def test_signer_verify_chain_valid():
    signer = ManifestSigner(host="h1")
    signer.sign("a", {"v": 1})
    signer.sign("b", {"v": 2})
    signer.sign("c", {"v": 3})
    assert signer.verify_chain() is True


def test_signer_verify_chain_detects_tampering():
    signer = ManifestSigner(host="h1")
    signer.sign("a", {"v": 1})
    signer.sign("b", {"v": 2})
    # Tamper with the second manifest's body hash
    signer._signed[1].body_sha256 = "f" * 64
    assert signer.verify_chain() is False


def test_verify_manifest_standalone():
    signer = ManifestSigner(host="h1")
    m1 = signer.sign("first", {"v": 1})
    # Verify against the genesis chain
    assert verify_manifest(m1.to_dict(), "0" * 64) is True
    # Verify against wrong previous chain
    assert verify_manifest(m1.to_dict(), "a" * 64) is False


def test_signer_accepts_bytes():
    signer = ManifestSigner(host="h1")
    payload = b'{"k": 1}'
    m = signer.sign("snap-bytes", payload)
    assert m.body_size == len(payload)
    assert m.body_sha256 == hash_body(payload)


def test_manifest_to_dict_round_trip():
    signer = ManifestSigner(host="h1")
    m = signer.sign("z", {"k": "v"})
    d = m.to_dict()
    assert d["snapshot_id"] == "z"
    assert d["host"] == "h1"
    assert d["body_sha256"] == m.body_sha256
    assert d["chain_sha256"] == m.chain_sha256
