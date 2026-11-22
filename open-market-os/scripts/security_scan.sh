#!/usr/bin/env bash
# Security and license checks. Reports go to scan-reports/ (or $OMOS_SCAN_OUT).
# Fails (exit 1) on: a HIGH-severity bandit finding, a Python or shipped-npm package with a known
# vulnerability, or a potential secret in tracked files.
# Needs: pip install -e '.[dev]' (bandit, pip-audit, detect-secrets, pip-licenses), npm ci in web/,
# and network access to the PyPI and npm advisory databases.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${OMOS_PY:-$ROOT/.venv/bin/python}"
BIN="$(dirname "$PY")"
OUT="${OMOS_SCAN_OUT:-$ROOT/scan-reports}"
mkdir -p "$OUT"
cd "$ROOT"
fail=0

echo "== bandit (static analysis of src/omos)"
"$BIN/bandit" -r src/omos -q -f json -o "$OUT/bandit.json" || true
"$PY" - "$OUT/bandit.json" <<'EOF' || fail=1
import json, sys, collections
res = json.load(open(sys.argv[1]))["results"]
print("  findings by severity:", dict(collections.Counter(r["issue_severity"] for r in res)))
high = [r for r in res if r["issue_severity"] == "HIGH"]
for r in high:
    print(f'  HIGH {r["test_id"]} {r["filename"]}:{r["line_number"]} {r["issue_text"]}')
sys.exit(1 if high else 0)
EOF

echo "== pip-audit (known vulnerabilities in installed Python packages)"
"$PY" -m pip freeze --exclude-editable --disable-pip-version-check > "$OUT/requirements.frozen.txt"
"$BIN/pip-audit" -r "$OUT/requirements.frozen.txt" --no-deps --disable-pip --progress-spinner off -f json -o "$OUT/pip-audit.json" 2>/dev/null
"$PY" - "$OUT/pip-audit.json" <<'EOF' || fail=1
import json, sys
deps = json.load(open(sys.argv[1]))["dependencies"]
bad = [(d["name"], d["version"], v["id"]) for d in deps for v in d.get("vulns", [])]
print(f"  {len(deps)} packages audited, {len(bad)} known vulnerabilities")
for b in bad:
    print("  VULN", *b)
sys.exit(1 if bad else 0)
EOF

echo "== npm audit (shipped web dependencies)"
if (cd web && npm audit --omit=dev --json > "$OUT/npm-audit.json" 2>/dev/null); then
  echo "  0 vulnerabilities in shipped packages"
else
  "$PY" -c "import json,sys; print('  ', json.load(open(sys.argv[1]))['metadata']['vulnerabilities'])" "$OUT/npm-audit.json" || true
  fail=1
fi
(cd web && npm audit --json > "$OUT/npm-audit-all.json" 2>/dev/null) || echo "  note: build-time (dev) packages have advisories; see npm-audit-all.json"

echo "== detect-secrets (tracked files)"
git ls-files -z | xargs -0 "$BIN/detect-secrets" scan > "$OUT/secrets.json" 2>/dev/null
"$PY" - "$OUT/secrets.json" <<'EOF' || fail=1
import json, sys
res = json.load(open(sys.argv[1]))["results"]
n = sum(len(v) for v in res.values())
print(f"  {n} potential secret(s)")
for f, items in res.items():
    for i in items:
        print(f'  SECRET? {f}:{i["line_number"]} {i["type"]}')
sys.exit(1 if n else 0)
EOF

echo "== licenses (inventory only)"
"$BIN/pip-licenses" --python "$PY" --format=csv --with-urls --output-file "$OUT/python-licenses.csv" >/dev/null 2>&1 \
  && echo "  python: $OUT/python-licenses.csv" || echo "  pip-licenses failed"
node -e "const p=require('./web/package-lock.json').packages;const r=Object.entries(p).filter(([k])=>k.startsWith('node_modules/')).map(([k,v])=>[k.slice(13),v.version,v.license||'UNKNOWN',v.dev?'build':'shipped'].join(','));require('fs').writeFileSync(process.argv[1],'name,version,license,use\n'+r.join('\n')+'\n')" "$OUT/npm-licenses.csv" \
  && echo "  npm: $OUT/npm-licenses.csv"

if [ "$fail" -ne 0 ]; then echo "SECURITY SCAN: FAILED (see above)"; exit 1; fi
echo "SECURITY SCAN: passed (this is not a guarantee of security)"
