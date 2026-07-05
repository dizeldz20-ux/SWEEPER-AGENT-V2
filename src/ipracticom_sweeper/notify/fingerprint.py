"""Fingerprint generation for alert dedup."""
import hashlib


def make_fingerprint(host: str, module: str, defcon: int) -> str:
    """Stable fingerprint: sha256(host:module:defcon)[:16]."""
    raw = f"{host}:{module}:{defcon}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
