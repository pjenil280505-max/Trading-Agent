#!/usr/bin/env bash
# End-to-end test of the Android app in an emulator, against a real host on this machine:
# pairing link (as in the computer's QR code) → pinned HTTPS → code exchange → the app opens the web UI
# signed in. The emulator reaches this machine as 10.0.2.2.
# Usage: scripts/android_e2e.sh path/to/app.apk   (adb on PATH, an emulator running, omos installed)
set -euo pipefail
APK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OMOS_E2E_ARTIFACTS:-$ROOT/e2e-artifacts}"
mkdir -p "$OUT"
export OMOS_HOME="$(mktemp -d)" OMOS_DISABLE_KEYRING=1 OMOS_VAULT_PASSPHRASE="android e2e vault passphrase" OMOS_NO_BROWSER=1
PY="${OMOS_PY:-python}"
"$PY" -m omos config --set port=8765 lan_port=8766 extra_allowed_hosts=10.0.2.2 >/dev/null
"$PY" -m omos start --no-browser --allow-remote >"$OMOS_HOME/host.out" 2>&1 &
HOST_PID=$!
trap 'kill $HOST_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8765/api/health >/dev/null && break; sleep 1; done
TOK="$("$PY" -m omos token --scopes read,admin --days 1 | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
api() { curl -sf -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' "$@"; }
FP="$(api http://127.0.0.1:8765/api/system/lan | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
CODE="$(api -X POST -d '{"preset":"monitor","label":"emulator"}' http://127.0.0.1:8765/api/auth/pairing | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
LINK="omos://pair?u=https%3A%2F%2F10.0.2.2%3A8766&c=${CODE}&f=${FP}"
echo "certificate ${FP:0:8}…, code $CODE"

adb install -r "$APK"
adb shell am start -W -a android.intent.action.VIEW -d "\"$LINK\"" org.openmarketos.app
signed_in() {
  api http://127.0.0.1:8765/api/auth/tokens | "$PY" -c '
import json, sys
t = [x for x in json.load(sys.stdin) if x["name"].startswith("Android") and not x.get("revoked_at")]
print("yes" if t and t[0].get("last_used_at") else "")'
}
OK=""
for _ in $(seq 1 120); do OK="$(signed_in)"; [ -n "$OK" ] && break; sleep 1; done
sleep 5
adb exec-out screencap -p >"$OUT/android-app.png" || true
adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 && adb pull /sdcard/ui.xml "$OUT/android-ui.xml" >/dev/null 2>&1 || true
if [ -z "$OK" ]; then
  echo "FAIL: the phone did not sign in"
  adb logcat -d | grep -iE "openmarketos|chromium|AndroidRuntime" | tail -150 || true
  tail -50 "$OMOS_HOME/host.out"
  exit 1
fi
echo "ok: paired over pinned HTTPS and the web app is signed in on the phone"
api http://127.0.0.1:8765/api/auth/tokens | "$PY" -c '
import json, sys
t = [x for x in json.load(sys.stdin) if x["name"].startswith("Android")][0]
print("   device:", t["name"], "| permissions:", ", ".join(t["scopes"]))
assert "admin" not in t["scopes"] and "trade:live" not in t["scopes"], "phone got more than the monitor preset"'
if [ -f "$OUT/android-ui.xml" ] && grep -qE 'Dashboard|Open Market OS' "$OUT/android-ui.xml"; then echo "ok: app interface visible on the phone"; fi

# A fresh install pinned to a different certificate must refuse this computer.
adb shell pm clear org.openmarketos.app >/dev/null
CODE2="$(api -X POST -d '{"preset":"monitor","label":"emulator2"}' http://127.0.0.1:8765/api/auth/pairing | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
WRONG="$(printf '0%.0s' $(seq 1 64))"
adb shell am start -W -a android.intent.action.VIEW -d "\"omos://pair?u=https%3A%2F%2F10.0.2.2%3A8766&c=${CODE2}&f=${WRONG}\"" org.openmarketos.app
sleep 15
COUNT="$(api http://127.0.0.1:8765/api/auth/tokens | "$PY" -c 'import json,sys; print(sum(1 for x in json.load(sys.stdin) if x["name"].startswith("Android")))')"
adb exec-out screencap -p >"$OUT/android-wrong-certificate.png" || true
[ "$COUNT" = "1" ] || { echo "FAIL: the app paired although the certificate did not match"; exit 1; }
echo "ok: a mismatching certificate is refused (no code was sent)"
