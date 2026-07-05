"""iPracticom AWS Linux Sweeper.

Public API:
    from ipracticom_sweeper import monitor, diagnose, repair, audit
"""

import logging
import sys

import structlog

# CRITICAL: configure structlog to stderr at import time.
# The default PrintLoggerFactory writes to stdout which pollutes JSON output.
# We do this BEFORE importing any submodules that might log.
structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    cache_logger_on_first_use=True,
)

logging.basicConfig(
    format="%(message)s",
    stream=sys.stderr,
    level=logging.WARNING,
)

from . import diagnose, monitor, repair

__version__ = "1.6.0"
__all__ = ["diagnose", "monitor", "repair"]