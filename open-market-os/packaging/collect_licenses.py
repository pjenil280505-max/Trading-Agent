"""Copy the license files of every bundled Python package (and the web UI's) into a folder."""

from __future__ import annotations

import re
import shutil
import sys
from importlib import metadata as md
from pathlib import Path

from packaging.requirements import Requirement


def collect(out: Path, root_dist: str = "open-market-os") -> int:
    seen: set[str] = set()

    def walk(name: str) -> None:
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            return
        try:
            d = md.distribution(name)
        except md.PackageNotFoundError:
            return
        seen.add(key)
        files = [f for f in (d.files or []) if re.search(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", f.name, re.I)
                 and ".dist-info" in str(f)]
        dest = out / f"{d.metadata['Name']}-{d.version}"
        for f in files:
            src = Path(d.locate_file(f))
            if src.is_file():
                target = dest / "/".join(f.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
        if not files:
            dest.mkdir(parents=True, exist_ok=True)
            lic = d.metadata.get("License-Expression") or d.metadata.get("License") or "see project"
            (dest / "LICENSE-METADATA.txt").write_text(f"License per package metadata: {lic}\n")
        for r in d.requires or []:
            req = Requirement(r)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                walk(req.name)

    walk(root_dist)
    return len(seen) - 1


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    web = Path(__file__).resolve().parents[1] / "src" / "omos" / "web" / "dist" / "third-party"
    if web.exists():
        shutil.copytree(web, out / "web", dirs_exist_ok=True)
    print(f"license texts of {collect(out)} bundled packages written to {out}")
