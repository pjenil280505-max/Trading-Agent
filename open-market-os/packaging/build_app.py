"""Build the installable app folder for THIS operating system with PyInstaller.

Output: dist/Open Market OS/ (Windows, Linux) or dist/Open Market OS.app (macOS), containing the
windowed launcher, the 'omos' command, the license texts of everything bundled, and the docs.
Usage (project root, web UI built, `pip install -e '.[dev]'` done): python packaging/build_app.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))

from collect_licenses import collect  # noqa: E402

from omos import __version__  # noqa: E402


def main() -> int:
    if not (ROOT / "src/omos/web/dist/index.html").exists():
        sys.exit("build the web UI first: scripts/build_web.sh")
    dist, work = ROOT / "dist", ROOT / "build" / "pyinstaller"
    shutil.rmtree(dist / "Open Market OS", ignore_errors=True)
    shutil.rmtree(dist / "Open Market OS.app", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(dist), "--workpath", str(work), str(ROOT / "packaging" / "omos.spec")],
                   check=True, cwd=ROOT)
    if sys.platform == "darwin":
        app = dist / "Open Market OS.app"
        docs = app / "Contents" / "Resources" / "licenses"
        shutil.rmtree(dist / "Open Market OS", ignore_errors=True)  # the .app holds everything
    else:
        app = dist / "Open Market OS"
        docs = app / "licenses"
    docs.mkdir(parents=True, exist_ok=True)
    for f in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copyfile(ROOT / f, docs / f)
    n = collect(docs / "third_party")
    shutil.copytree(ROOT / "src/omos/web/dist/third-party", docs / "third_party" / "web", dirs_exist_ok=True)
    (docs.parent / "VERSION.txt").write_text(__version__ + "\n")
    print(f"built {app} (version {__version__}; license texts of {n} bundled packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
