#!/usr/bin/env bash
# Smoke-test a built release tarball the way a user would run it: extract it to a temporary folder,
# start the single-file binary with SIMULATED demo data, then check health, the UI, sign-in, a
# backtest, the MCP bridge and a clean shutdown. Usage: scripts/verify_release.sh [tarball]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${OMOS_PY:-$ROOT/.venv/bin/python}"   # used only as an MCP *client* and to parse JSON
TARBALL="${1:-$(ls -t "$ROOT"/releases/omos-*.tar.gz | head -1)}"
PORT="${OMOS_VERIFY_PORT:-8794}"
X="$(mktemp -d)"
cleanup() { [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true; rm -rf "$X"; }
trap cleanup EXIT
tar -C "$X" -xzf "$TARBALL"
B="$(ls -d "$X"/omos-*/)omos"
export OMOS_HOME="$X/home" OMOS_DISABLE_KEYRING=1 OMOS_VAULT_PASSPHRASE="release verification only"
ok() { echo "  ok  $*"; }

"$B" --version
"$B" start --no-browser --demo --port "$PORT" > "$X/host.out" 2>&1 &
PID=$!
for _ in $(seq 1 120); do curl -sf -m 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && ok "health"
curl -s "http://127.0.0.1:$PORT/" | grep -q "<title>Open Market OS</title>" && ok "web UI served"
curl -sf "http://127.0.0.1:$PORT/third-party/klinecharts-NOTICE.txt" | grep -q "TradingView" && ok "third-party notices served"

CODE="$("$B" open --print-only | sed 's/.*code=//')"
TOK="$(curl -s -X POST "http://127.0.0.1:$PORT/api/auth/exchange" -H 'Content-Type: application/json' \
  -d "{\"code\":\"$CODE\",\"device_name\":\"release-check\"}" | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["token"])')"
ok "sign-in code exchanged"
curl -s -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -X POST "http://127.0.0.1:$PORT/api/backtest" \
  -d '{"source":"long_entry = crossover(ema(close,12), ema(close,26))\nlong_exit = crossunder(ema(close,12), ema(close,26))\n","instrument":"SIM:DEMO-TREND","tf":"1h","limit":1000,"save":false}' \
  | "$PY" -c 'import sys,json; d=json.load(sys.stdin); assert d["data"]["status"]=="SIMULATED" and d["metrics"]["trades"]>0; print("  ok  backtest:", d["metrics"]["trades"], "trades on", d["data"]["status"], "data")'

curl -sf -X POST -H "Authorization: Bearer $TOK" "http://127.0.0.1:$PORT/api/mcp/token" >/dev/null && ok "MCP bridge token created"
"$PY" - "$B" <<'EOF'
import asyncio, os, sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters

async def main():
    async with Client(StdioServerParameters(command=sys.argv[1], args=["mcp"], env=dict(os.environ)), read_timeout_seconds=60) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "get_quote" in names and "get_portfolio" not in names, names  # private tools are off by default
        r = await c.call_tool("get_quote", {"instrument_id": "SIM:DEMO-TREND"})
        assert not r.is_error and "SIMULATED" in r.content[0].text, r
        print(f"  ok  MCP bridge: {len(names)} tools, get_quote returns SIMULATED-labelled data")

asyncio.run(asyncio.wait_for(main(), 120))
EOF

kill -TERM "$PID"
wait "$PID" 2>/dev/null || true
PID=""
grep -q "host stopped cleanly" "$OMOS_HOME/logs/omos.log" && ok "clean shutdown on SIGTERM"
[ ! -e "$OMOS_HOME/host.runtime.json" ] && ok "runtime file removed"
echo "RELEASE VERIFIED: $(basename "$TARBALL")"
