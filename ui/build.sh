#!/usr/bin/env bash
# Build the React UI into src/adviewer/ui/static (committed, so users need no Node.js).
# Uses a local Node.js (18+) if there is one, otherwise the official node:22 Docker image.
#   ui/build.sh           type-check, test, build
#   ui/build.sh install   refresh package-lock.json (after changing package.json)
set -euo pipefail
cd "$(dirname "$0")"
STEPS="npm ci && npm run typecheck && npm test && npm run build"
[ "${1:-}" = "install" ] && STEPS="npm install"
if command -v npm >/dev/null; then
  sh -c "$STEPS"
else
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_update_notifier=false \
    -v "$(cd .. && pwd)":/repo -w /repo/ui node:22-bookworm-slim sh -c "$STEPS"
fi
