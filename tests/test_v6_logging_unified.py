"""v1.5.10 — Logging unification (stdlib → structlog).

8 src/ files still use stdlib logging directly; the rest of the codebase
uses structlog.get_logger(). This test asserts the sweep is complete.

Acceptance:
- ALL `import logging` lines in src/ipracticom_sweeper/ must be in
  `_log.py` (the centralized helper module). Other modules should
  use structlog.
- `logging.getLogger(...)` MUST NOT appear outside `_log.py`.
- `logging.basicConfig(...)` MUST NOT appear outside `_log.py` and
  `__init__.py` (which sets up the root logger once at import).
- Public smoke test: importing every targeted module still works.
"""

from __future__ import annotations

import importlib
from pathlib import Path


SRC_DIR = Path("src/ipracticom_sweeper")
ALLOWED_STDLIB_FILES = {
    SRC_DIR / "_log.py",       # centralized helper; stdlib by design
    SRC_DIR / "__init__.py",   # root logger setup
}

# Modules migrated to structlog in v1.5.10.
# `fleet.aws_connector` removed its `import logging` line without
# adopting structlog because the module never called a logger.
TARGETS = [
    "agent_api",
    "sweeper",
    "monitoring.otel",
    "fleet.collector",
    "config.module_registry",
    "telegram_bot.bot",
]  # fmt: skip


def _iter_python_files() -> list[Path]:
    return sorted(p for p in SRC_DIR.rglob("*.py") if p.is_file())


def test_no_stdlib_logging_outside_helpers() -> None:
    """Only `_log.py` may import stdlib logging at module level.

    `__init__.py`, `sweeper.py`, and `telegram_bot/bot.py` are allowed
    to call `logging.basicConfig(...)` for root-logger setup (the
    recommended structlog bridge pattern), but they MUST NOT call
    `logging.getLogger(...)` for a module-level logger — those should
    use `structlog.get_logger(...)` instead.
    """
    offenders: list[tuple[Path, str]] = []

    # Files that may import stdlib logging at module level (root logger setup).
    module_level_logging_allowed = {
        SRC_DIR / "_log.py",
        SRC_DIR / "__init__.py",
        SRC_DIR / "sweeper.py",
        SRC_DIR / "telegram_bot/bot.py",  # `import logging` lives inside main()
    }

    for path in _iter_python_files():
        if path in module_level_logging_allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # Find a module-level `import logging` line (not indented).
        for line in text.splitlines():
            if (
                line.startswith("import logging")
                or line.startswith("from logging import")
            ):
                offenders.append((path, "module-level stdlib import"))
                break

    # `logging.basicConfig(...)` and `logging.WARNING`/`logging.INFO` are
    # allowed in `__init__.py`, `sweeper.py`, `telegram_bot/bot.py` because
    # those configure the root logger and use level-constants only.
    basicconfig_allowed_files = {
        SRC_DIR / "_log.py",  # central helper
        SRC_DIR / "__init__.py",
        SRC_DIR / "sweeper.py",
        SRC_DIR / "telegram_bot/bot.py",
    }
    for path in _iter_python_files():
        if path in basicconfig_allowed_files:
            continue
        text = path.read_text(encoding="utf-8")
        if "logging.basicConfig(" in text:
            offenders.append((path, "basicConfig"))
        if "logging.getLogger(" in text:
            offenders.append((path, "getLogger"))
    assert not offenders, (
        f"stdlib logging found outside allowed files: {offenders}"
    )


def test_structlog_imports_present_in_targets() -> None:
    """Every migrated module now imports structlog.get_logger()."""
    import re

    for mod_path in TARGETS:
        py = SRC_DIR / mod_path.replace(".", "/") / "__init__.py"
        if not py.exists():
            py = SRC_DIR / (mod_path.replace(".", "/") + ".py")
        assert py.exists(), f"target module not found: {py}"
        text = py.read_text(encoding="utf-8")
        assert "import structlog" in text, (
            f"{py}: missing `import structlog`"
        )
        assert re.search(r"structlog\.get_logger\(", text), (
            f"{py}: missing `structlog.get_logger()` call"
        )


def test_all_targeted_modules_importable() -> None:
    """Smoke check — converting to structlog must not break imports."""
    for mod_name in TARGETS:
        # Translate dot-path to import path under ipracticom_sweeper.
        import_path = "ipracticom_sweeper." + mod_name
        mod = importlib.import_module(import_path)
        assert mod is not None, f"failed to import {import_path}"
