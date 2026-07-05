"""Evidence retention: cleanup old local evidence (S3 lifecycle is separate)."""
from __future__ import annotations
import time
from pathlib import Path


def cleanup_local(dir_path: str | Path, older_than_days: float = 90.0) -> int:
    """Delete files older than N days. Returns count deleted."""
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return 0
    cutoff = time.time() - (older_than_days * 86400)
    deleted = 0
    for f in dir_path.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    return deleted
