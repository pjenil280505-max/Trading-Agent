# PyInstaller spec for the installable app: one folder with two executables sharing one runtime.
#   "Open Market OS" (open-market-os on Linux): windowed launcher - what the desktop/start-menu icon runs
#   "omos":                                     console CLI (MCP bridge for Claude Desktop, diagnostics)
# On macOS the folder is wrapped in "Open Market OS.app".
# Build: pyinstaller --noconfirm --clean packaging/omos.spec   (from the project root; the web UI must be built)
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICONS = os.path.join(SPECPATH, "icons")
sys.path.insert(0, os.path.join(ROOT, "src"))
from omos import __version__  # noqa: E402

if not os.path.exists(os.path.join(ROOT, "src", "omos", "web", "dist", "index.html")):
    raise SystemExit("build the web UI first (scripts/build_web.sh)")

hidden = []
for pkg in ("omos", "uvicorn", "mcp.server", "mcp.client", "mcp.shared", "keyring", "zstandard"):
    hidden += collect_submodules(pkg)
datas = collect_data_files("omos")
for dist in ("mcp", "keyring", "open-market-os"):
    datas += copy_metadata(dist)

a = Analysis(
    [os.path.join(SPECPATH, "entry_app.py")],
    pathex=[os.path.join(ROOT, "src")],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui_name = {"win32": "Open Market OS", "darwin": "Open Market OS"}.get(sys.platform, "open-market-os")
icon = os.path.join(ICONS, "omos.ico" if sys.platform == "win32" else "omos.icns" if sys.platform == "darwin" else "omos-256.png")
win_version = None
if sys.platform == "win32":
    nums = [int(x) for x in __version__.split(".")[:3]] + [0]
    vfile = os.path.join(SPECPATH, "build-version.txt")
    with open(vfile, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Open Market OS contributors'),
    StringStruct('FileDescription', 'Open Market OS'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductName', 'Open Market OS'),
    StringStruct('ProductVersion', '{__version__}'),
    StringStruct('LegalCopyright', 'Apache License 2.0')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""")
    win_version = vfile

common = dict(exclude_binaries=True, debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
              icon=icon, version=win_version)
exe_gui = EXE(pyz, a.scripts, [], name=gui_name, console=False, **common)
exe_cli = EXE(pyz, a.scripts, [], name="omos", console=True, **common)
coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="Open Market OS")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Open Market OS.app",
        icon=icon,
        bundle_identifier="org.openmarketos.app",
        version=__version__,
        info_plist={
            "CFBundleName": "Open Market OS",
            "CFBundleDisplayName": "Open Market OS",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "LSUIElement": True,  # no Dock icon: the app's window is the browser tab it opens
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Apache License 2.0",
        },
    )
