"""Main sweeper entry point.

Run as: python -m ipracticom_sweeper.sweeper
Or via systemd timer.

Week 2 scope: full pipeline (monitor → diagnose → repair).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ipracticom_sweeper import __version__

import structlog

from ipracticom_sweeper.config import load_rules
from ipracticom_sweeper.pipeline import run_pipeline

logger = structlog.get_logger()


def setup_logging(quiet: bool = False, json_mode: bool = False) -> None:
    """Configure logging.

    - Always send structlog to stderr (so stdout can carry JSON)
    - Suppress all but errors if quiet
    """
    # Send everything to stderr
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.WARNING if quiet else logging.INFO,
    )

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.WARNING if quiet else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


DEFCON_ICONS = {
    "green": "✅",
    "yellow": "⚠️",
    "orange": "🟠",
    "red": "🔴",
    "black": "🚨",
}


def format_summary(result) -> str:
    """Format pipeline result as a short human-readable summary."""
    icon = DEFCON_ICONS.get(result.defcon_label, "❓")

    lines = [
        f"{icon} DEFCON {result.defcon} ({result.defcon_label})",
        f"   {result.diagnosis.get('summary', '')}",
        "",
        f"   monitor: {result.monitor_overall}",
        f"   problems: {result.problems_found}",
        f"   repairs attempted: {result.repairs_attempted}",
        f"   repairs succeeded: {result.repairs_succeeded}",
        f"   repairs failed: {result.repairs_failed}",
        f"   needs human: {result.needs_human}",
    ]

    if result.repair_results:
        lines.append("")
        lines.append("   Repairs:")
        for r in result.repair_results:
            if r.get("dry_run"):
                lines.append(f"     [DRY-RUN] {r['action']}({r.get('kwargs', {})})")
            else:
                mark = "✓" if r["success"] else "✗"
                lines.append(f"     {mark} {r['action']}({r['target']}): {r['message']}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="iPracticom AWS Linux Server Health Sweeper"
    )
    parser.add_argument(
        "--rules",
        type=Path,
        help="Path to a YAML rules file. Default: the packaged "
             "ipracticom_sweeper/rules/default.yaml, merged over built-in "
             "defaults and any rules.local.yaml override in the state dir.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full pipeline result as JSON to stdout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all output except errors",
    )
    parser.add_argument(
        "--no-repair",
        action="store_true",
        help="Diagnose only, do not execute any repairs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually execute repairs — log intent only",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="ipracticom-sweeper " + __version__,
    )
    args = parser.parse_args()

    setup_logging(quiet=args.quiet, json_mode=args.json)
    rules = load_rules(args.rules) if args.rules else load_rules()

    result = run_pipeline(
        rules,
        auto_repair=not args.no_repair,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    elif not args.quiet:
        print(format_summary(result))

    # Exit code:
    #   0 = green (all good)
    #   1 = yellow (warn only)
    #   2 = orange/red (crit, auto-repair armed)
    #   3 = black (monitor error or human needed)
    if result.defcon >= 5:
        return 0
    elif result.defcon == 4:
        return 1
    elif result.defcon >= 2:
        return 2
    else:
        return 3


if __name__ == "__main__":
    sys.exit(main())