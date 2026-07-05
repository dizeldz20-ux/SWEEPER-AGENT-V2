#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEBUI_SRC="${SWEEPER_WEBUI_SRC:-${REPO_ROOT}/frontend}"
WEBUI_DIST="${REPO_ROOT}/src/ipracticom_sweeper/webui/dist"

if [ ! -f "${WEBUI_SRC}/package.json" ]; then
  echo "[webui] no frontend package found at ${WEBUI_SRC}; skipping build"
  exit 0
fi

echo "[webui] building ${WEBUI_SRC}"
(
  cd "${WEBUI_SRC}"
  if [ -f package-lock.json ]; then
    npm ci
  else
    npm install
  fi
  npm run build
)

if [ ! -f "${WEBUI_SRC}/dist/index.html" ]; then
  echo "[webui] build did not produce dist/index.html" >&2
  exit 1
fi

echo "[webui] copying dist to ${WEBUI_DIST}"
rm -rf "${WEBUI_DIST}"
mkdir -p "$(dirname "${WEBUI_DIST}")"
cp -R "${WEBUI_SRC}/dist" "${WEBUI_DIST}"
echo "[webui] done"
