#!/usr/bin/env bash
# End-to-end UI tests. Starts an isolated host (temporary data folder, SIMULATED demo data on),
# drives the built UI in Chromium with Playwright, then stops the host and deletes the folder.
# Needs: the UI built (scripts/build_web.sh), Node.js 18+, and the 'playwright' package with
# Chromium (npm i -g playwright && npx playwright install chromium).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export OMOS_PY="${OMOS_PY:-$ROOT/.venv/bin/python}"
PORT="${OMOS_E2E_PORT:-8798}"
export OMOS_HOME="$(mktemp -d)"
export OMOS_E2E_ARTIFACTS="${OMOS_E2E_ARTIFACTS:-$ROOT/e2e-artifacts}"
[ -f "$ROOT/src/omos/web/dist/index.html" ] || { echo "Build the UI first: scripts/build_web.sh" >&2; exit 2; }

# OMOS_E2E_LAN=1 also tests pairing a phone over the LAN; that binds the temporary test host to all
# interfaces for the duration of the run, so it is opt-in.
EXTRA=()
if [ "${OMOS_E2E_LAN:-0}" = "1" ]; then
  EXTRA=(--allow-remote)
  export OMOS_E2E_LAN_IP="$("$OMOS_PY" -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); print(s.getsockname()[0])')"
fi
"$OMOS_PY" -m omos start --no-browser --demo --port "$PORT" "${EXTRA[@]}" >"$OMOS_HOME/host.out" 2>&1 &
HOST_PID=$!
cleanup() { kill "$HOST_PID" 2>/dev/null || true; wait "$HOST_PID" 2>/dev/null || true; rm -rf "$OMOS_HOME"; }
trap cleanup EXIT

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "host did not start:" >&2; cat "$OMOS_HOME/host.out" >&2; exit 2; }

export OMOS_E2E_BASE="http://127.0.0.1:$PORT"
node "$ROOT/tests/e2e/ui.e2e.cjs"
