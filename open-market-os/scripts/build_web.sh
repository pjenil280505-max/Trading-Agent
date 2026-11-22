#!/usr/bin/env bash
# Build the web UI into src/omos/web/dist, where the host serves it from.
# Needs Node.js 20+ and npm. Installs exact versions from web/package-lock.json.
set -euo pipefail
cd "$(dirname "$0")/../web"
if [ ! -d node_modules ]; then
  npm ci --no-audit --no-fund
fi
npm run build
echo "Web UI built into src/omos/web/dist"
