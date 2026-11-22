#!/usr/bin/env bash
# Build release artifacts into releases/:
#   open_market_os-<ver>-py3-none-any.whl     (pip install; dependencies come from PyPI)
#   omos-<ver>-<os>-<arch>.tar.gz             (single-file PyInstaller binary for THIS platform,
#                                              plus LICENSE, NOTICE and the license texts of every
#                                              bundled Python package)
# Needs: pip install -e '.[dev]' (PyInstaller), Node.js for the UI. PyInstaller cannot cross-compile:
# run this on each target OS to get its binary. Only Linux x86_64 has been built and tested so far.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${OMOS_PY:-$ROOT/.venv/bin/python}"
BIN="$(dirname "$PY")"
cd "$ROOT"
VER="$("$PY" -c 'import omos; print(omos.__version__)')"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
OUT="$ROOT/releases"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

echo "== web UI"
scripts/build_web.sh

echo "== wheel"
# setuptools reuses build/ between runs; stale files there (e.g. an older UI bundle) would be packed
# into the wheel, so always start from a clean build directory.
rm -rf "$ROOT/build"
"$PY" -m pip wheel . --no-deps --no-build-isolation -q -w "$OUT"
"$PY" - "$OUT"/open_market_os-"$VER"-*.whl <<'EOF'
import re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
html = z.read("omos/web/dist/index.html").decode()
assets = [n for n in names if n.startswith("omos/web/dist/assets/")]
unused = [a for a in assets if a.rsplit("/", 1)[1] not in html]
if unused:
    sys.exit(f"wheel contains UI assets not referenced by index.html (stale build?): {unused}")
print(f"  wheel ok: {len(names)} files, {len(assets)} UI assets, all referenced")
EOF

echo "== single-file binary (PyInstaller)"
"$BIN/pyinstaller" --noconfirm --clean --onefile --name omos --log-level WARN \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" --paths src \
  --collect-data omos --collect-submodules omos \
  --collect-submodules uvicorn --collect-submodules mcp.server --collect-submodules mcp.client \
  --collect-submodules mcp.shared --copy-metadata mcp \
  --collect-submodules keyring --copy-metadata keyring \
  src/omos/__main__.py

echo "== license texts of bundled packages"
PKG="$WORK/omos-$VER-$OS-$ARCH"
mkdir -p "$PKG/third_party_licenses"
cp "$WORK/dist/omos" "$PKG/omos"
cp LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md "$PKG/"
cp -r src/omos/web/dist/third-party "$PKG/third_party_licenses/web"
"$PY" - "$PKG/third_party_licenses" <<'EOF'
import re, shutil, sys
from importlib import metadata as md
from pathlib import Path
from packaging.requirements import Requirement
out = Path(sys.argv[1])
seen = set()
def walk(name):
    key = re.sub(r"[-_.]+", "-", name).lower()
    if key in seen:
        return
    try:
        d = md.distribution(name)
    except md.PackageNotFoundError:
        return
    seen.add(key)
    files = [f for f in (d.files or []) if re.search(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", f.name, re.I) and ".dist-info" in str(f)]
    dest = out / f"{d.metadata['Name']}-{d.version}"
    for f in files:
        src = Path(d.locate_file(f))
        if src.is_file():
            target = dest / "/".join(f.parts[1:])  # keep sub-paths below .dist-info
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    if not files:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "LICENSE-METADATA.txt").write_text(f"License per package metadata: {d.metadata.get('License-Expression') or d.metadata.get('License') or 'see project'}\n")
    for r in d.requires or []:
        req = Requirement(r)
        if req.marker is None or req.marker.evaluate({"extra": ""}):
            walk(req.name)
walk("open-market-os")
print(f"  {len(seen) - 1} packages")
EOF
tar -C "$WORK" -czf "$OUT/omos-$VER-$OS-$ARCH.tar.gz" "omos-$VER-$OS-$ARCH"
(cd "$OUT" && sha256sum ./*.whl "omos-$VER-$OS-$ARCH.tar.gz" > SHA256SUMS)
ls -la "$OUT"
