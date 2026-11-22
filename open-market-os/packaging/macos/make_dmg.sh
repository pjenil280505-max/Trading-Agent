#!/usr/bin/env bash
# macOS disk image from dist/Open Market OS.app (packaging/build_app.py): drag the app to Applications.
# The app is ad-hoc signed (PyInstaller does this), not notarized: on first open macOS asks for
# confirmation (System Settings → Privacy & Security → Open Anyway). See docs/guides/INSTALLATION_GUIDE.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/dist/Open Market OS.app"
OUT="${1:-$ROOT/releases}"
VER="$(cat "$APP/Contents/Resources/VERSION.txt")"
ARCH="$(uname -m)"
[ -d "$APP" ] || { echo "build the app first: python packaging/build_app.py" >&2; exit 2; }
mkdir -p "$OUT"
# re-sign the whole bundle ad hoc after build_app.py added license files, so the signature is valid
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/READ ME FIRST.txt" <<TXT
Open Market OS $VER

1. Drag "Open Market OS" onto the Applications folder.
2. Open it from Applications or Launchpad.
3. The first time, macOS says it cannot check the app for malicious software (the app is not
   notarized by Apple). Click "Done", then open System Settings -> Privacy & Security, scroll down
   and click "Open Anyway" next to "Open Market OS". This is needed only once.

The app opens in your web browser. It keeps running in the background (no Dock icon) so bots and
alerts work while the browser is closed; quit it from Settings -> General -> Quit Open Market OS.
TXT
DMG="$OUT/OpenMarketOS-$VER-macos-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "Open Market OS" -srcfolder "$STAGE" -ov -format UDZO -fs HFS+ "$DMG" >/dev/null
echo "   $DMG"
