#!/usr/bin/env bash
# Linux packages from the PyInstaller folder (packaging/build_app.py):
#   open-market-os_<ver>_<arch>.deb       Debian/Ubuntu/Mint: double-click to install, or the Software app
#   OpenMarketOS-<ver>-<arch>.AppImage    any distribution: make executable and double-click
#   open-market-os-<ver>-linux-<arch>.tar.gz
# Usage: packaging/linux/make_packages.sh [outdir]   (needs dpkg-deb; appimagetool is downloaded if missing)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPDIR_SRC="$ROOT/dist/Open Market OS"
OUT="${1:-$ROOT/releases}"
VER="$(cat "$APPDIR_SRC/VERSION.txt")"
MACH="$(uname -m)"
case "$MACH" in x86_64) DEB_ARCH=amd64 ;; aarch64|arm64) DEB_ARCH=arm64 ;; *) DEB_ARCH="$MACH" ;; esac
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"
[ -x "$APPDIR_SRC/open-market-os" ] || { echo "build the app first: python packaging/build_app.py" >&2; exit 2; }

echo "== .deb"
PKG="$WORK/deb"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/usr/share/doc/open-market-os"
cp -a "$APPDIR_SRC" "$PKG/opt/open-market-os"
ln -s /opt/open-market-os/omos "$PKG/usr/bin/omos"
ln -s /opt/open-market-os/open-market-os "$PKG/usr/bin/open-market-os"
sed 's#^Exec=open-market-os#Exec=/opt/open-market-os/open-market-os#' "$ROOT/packaging/linux/open-market-os.desktop" \
  > "$PKG/usr/share/applications/open-market-os.desktop"
for s in 16 32 48 64 128 256 512; do
  install -Dm644 "$ROOT/packaging/icons/omos-$s.png" "$PKG/usr/share/icons/hicolor/${s}x${s}/apps/open-market-os.png"
done
cp "$ROOT/LICENSE" "$PKG/usr/share/doc/open-market-os/copyright"
SIZE_KB="$(du -sk "$PKG/opt" | cut -f1)"
GLIBC="$(ldd --version | head -1 | grep -oE '[0-9]+\.[0-9]+$')"
cat > "$PKG/DEBIAN/control" <<CTRL
Package: open-market-os
Version: $VER
Section: misc
Priority: optional
Architecture: $DEB_ARCH
Depends: libc6 (>= $GLIBC)
Recommends: xdg-utils
Installed-Size: $SIZE_KB
Maintainer: Open Market OS contributors <noreply@openmarketos.invalid>
Homepage: https://github.com/pjenil280505-max/Trading-Agent
Description: Local-first market workstation
 Charts, indicators, research, strategy testing, paper trading and bots behind
 a risk engine, with optional local AI. Runs on your own computer; your data
 stays in your home folder and is kept when the package is removed.
CTRL
cat > "$PKG/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
# Stop running copies cleanly (they shut down on SIGTERM and resume bots on the next start).
pkill -TERM -f '^/opt/open-market-os/' 2>/dev/null || true
exit 0
SCRIPT
cat > "$PKG/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
SCRIPT
chmod 0755 "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postinst"
DEB="$OUT/open-market-os_${VER}_${DEB_ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$DEB" >/dev/null
echo "   $DEB"

echo "== tar.gz"
TGZ_DIR="$WORK/open-market-os-$VER-linux-$MACH"
cp -a "$APPDIR_SRC" "$TGZ_DIR"
cp "$ROOT/packaging/linux/open-market-os.desktop" "$ROOT/packaging/icons/omos-256.png" "$TGZ_DIR/"
tar -C "$WORK" -czf "$OUT/open-market-os-$VER-linux-$MACH.tar.gz" "open-market-os-$VER-linux-$MACH"
echo "   $OUT/open-market-os-$VER-linux-$MACH.tar.gz"

echo "== AppImage"
TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
  TOOL="$WORK/appimagetool"
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$MACH.AppImage"
  chmod +x "$TOOL"
fi
AD="$WORK/AppDir"
mkdir -p "$AD/usr/lib"
cp -a "$APPDIR_SRC" "$AD/usr/lib/open-market-os"
cp "$ROOT/packaging/linux/open-market-os.desktop" "$AD/open-market-os.desktop"
cp "$ROOT/packaging/icons/omos-256.png" "$AD/open-market-os.png"
cat > "$AD/AppRun" <<'SCRIPT'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/open-market-os/open-market-os" "$@"
SCRIPT
chmod +x "$AD/AppRun"
APPIMG="$OUT/OpenMarketOS-$VER-$MACH.AppImage"
ARCH="$MACH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$AD" "$APPIMG" >/dev/null 2>&1 || \
  ARCH="$MACH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$AD" "$APPIMG"
chmod +x "$APPIMG"
echo "   $APPIMG"
