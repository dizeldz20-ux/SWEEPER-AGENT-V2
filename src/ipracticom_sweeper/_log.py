"""Logging helpers — soften the boilerplate of "log + continue" patterns.

Replaces the long-standing pattern of swallowing an exception with
a single ``log_suppressed(context, exc)`` call so silent failures
leave a structured line in the journal instead of vanishing.

Callers MUST NOT use this for failures that should propagate —
wrap in ``raise`` for those.

Example::

    try:
        risky()
    except Exception as exc:
        log_suppressed("module.thing", exc)
"""
from __future__ import annotations

import logging
import os
import traceback
from typing import Any, Optional

# Module-level logger; configure the level from env if set so tests
# can crank it up without monkeypatching the helper.
_LOG_LEVEL = os.environ.get("IPRACTICOM_SWEEPER_LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("ipracticom_sweeper.errors")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False  # don't double-log via root


def log_suppressed(
    context: str,
    exc: BaseException,
    *,
    level: int = logging.WARNING,
    extras: Optional[dict[str, Any]] = None,
) -> None:
    """Log a swallowed exception and continue.

    Args:
        context: Dotted path identifying where the failure happened,
            e.g. ``"fleet.aws_connector.read_uptime"``. Becomes the
            log record's ``context`` field.
        exc: The exception instance. Logged with its repr.
        level: Logging level (default WARNING). Use ``logging.DEBUG``
            for noisy collectors.
        extras: Optional key/value pairs appended to the log record.
            Useful for adding metrics like ``{"host_id": host}``.

    The exception's full traceback is also captured at DEBUG level
    so journalctl -f doesn't fill up with stack frames on every miss.
    """
    msg = "%s: %s" % (context, type(exc).__name__)
    detail = str(exc) if str(exc) else "(no message)"
    if extras:
        kv = " ".join(f"{k}={v!r}" for k, v in extras.items())
        msg = f"{msg}: {detail} [{kv}]"
    else:
        msg = f"{msg}: {detail}"

    logger.log(level, msg)
    # Stash the traceback at DEBUG so an operator can re-enable it
    # without losing performance in normal operation.
    logger.debug(
        "traceback for %s", context, exc_info=(type(exc), exc, exc.__traceback__),
    )


def install_root_handler() -> None:
    """Install a sane default handler on the root logger.

    Idempotent — safe to call from ``main()`` to make sure the
    helper above is wired into whatever the runtime expects (journald
    needs stdlib logging, not structlog). Skips if the root already
    has handlers.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)
    root.setLevel(_LOG_LEVEL)


__all__ = ["log_suppressed", "install_root_handler", "logger"]
