"""Config hot-reload via file mtime tracking."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any


class ConfigLoader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._cache: dict[str, Any] | None = None
        self._last_mtime: float = 0.0

    def get(self) -> dict[str, Any]:
        """Returns current config, reloading if file changed."""
        if not self.path.exists():
            return self._cache or {}
        mtime = self.path.stat().st_mtime
        if self._cache is None or mtime > self._last_mtime:
            try:
                data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                # Partial/corrupt file (e.g. an external editor mid-save) must
                # not crash every reader — keep the last good cache.
                return self._cache or {}
            self._cache = data
            self._last_mtime = mtime
        return self._cache

    def reload(self) -> dict[str, Any]:
        """Force reload regardless of mtime."""
        self._cache = None
        self._last_mtime = 0.0
        return self.get()

    def save(self, config: dict[str, Any]) -> None:
        """Write config atomically and invalidate cache.

        tmp-write + rename so a crash mid-write can never leave a truncated
        JSON file that makes every subsequent get() raise.
        """
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(config, indent=2))
        tmp.replace(self.path)
        self._cache = None
        self._last_mtime = 0.0
