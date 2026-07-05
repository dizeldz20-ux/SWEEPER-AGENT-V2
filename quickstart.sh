#!/usr/bin/env bash
# Quick start: run a single sweep without installing or systemd.
# Usage: bash quickstart.sh [--rules path/to/rules.yaml]
set -euo pipefail

cd "$(dirname "$0")"

RULES_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --rules) RULES_ARG="--rules $2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# Load .env if exists
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

echo "[quickstart] Running single sweep..."
python3 -m ipracticom_sweeper.sweeper $RULES_ARG
echo "[quickstart] Done."
