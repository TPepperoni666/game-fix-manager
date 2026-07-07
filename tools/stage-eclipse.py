#!/usr/bin/env python3
"""Stage Eclipse mod .rar files into a local-payloads folder.

Eclipse perf mods contain modified copyrighted game exes, so they never go
in the git store — they live in your local-payloads folder (NAS/SD). This
script extracts each Eclipse .rar and lays it out where the recipes expect:
  <local-payloads>/<recipe-id>/payload/mod/<game-dir mirror>

Usage:
  python tools/stage-eclipse.py <rar-folder> <local-payloads-folder>

Needs 7z (Windows: 7-Zip; Linux: p7zip) for .rar extraction. Run it wherever
the .rar files and the (synced/mounted) local-payloads folder are both
reachable — e.g. your Windows box if local-payloads is a Syncthing folder.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# rar filename fragment -> (recipe id, inner game-folder path to copy FROM)
MAP = {
    "Alan_Wake": ("eclipse-alan-wake-2", "Alan Wake 2"),
    "Clair_Obscur": ("eclipse-clair-obscur", "Clair Obscur Expedition 33"),
    "Cronos": ("eclipse-cronos", "Cronos The New Dawn"),
    "Hell-Is-Us": ("eclipse-hell-is-us", "Hell is Us"),
    "Indiana_Jones": ("eclipse-indiana-jones",
                      "For licence/Indiana Jones and the Great Circle"),
    "Mafia": ("eclipse-mafia-old-country", "Mafia The Old Country"),
    "MGS": ("eclipse-mgs-delta", "Metal Gear Solid Delta Snake Eater"),
    "Silent_Hill": ("eclipse-silent-hill-2", "Silent Hill 2"),
    "STALKER": ("eclipse-stalker-2", "v2/S.T.A.L.K.E.R. 2 Heart of Chornobyl"),
    "Jedi": ("eclipse-jedi-survivor", "STAR WARS Jedi Survivor"),
    "Wuchang": ("eclipse-wuchang", None),  # flat — paks placed specially
}
WUCHANG_SUBPATH = "Project_Plague/Content/Paks"


def _sevenzip() -> str:
    for c in ("7z", r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if shutil.which(c) or Path(c).is_file():
            return c
    sys.exit("7z not found — install 7-Zip (Windows) or p7zip (Linux).")


def _match(rar_name: str):
    for frag, target in MAP.items():
        if frag.lower() in rar_name.lower():
            return target
    return None, None


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    rar_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    sevenzip = _sevenzip()
    rars = sorted(rar_dir.glob("ECLIPSE*.rar"))
    if not rars:
        sys.exit(f"No ECLIPSE*.rar in {rar_dir}")

    for rar in rars:
        rid, inner = _match(rar.name)
        if rid is None:
            print(f"SKIP (unmapped): {rar.name}")
            continue
        print(f"\n{rar.name}\n  -> {rid}")
        dest = out_dir / rid / "payload" / "mod"
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sevenzip, "x", str(rar), f"-o{tmp}", "-y"],
                           stdout=subprocess.DEVNULL, check=True)
            top = next(Path(tmp).iterdir())  # the single mod folder
            if rid == "eclipse-wuchang":
                src = top  # bare paks at the top level
                dst = dest / WUCHANG_SUBPATH
            else:
                src = top / inner
                dst = dest
            if not src.is_dir():
                print(f"  ! expected folder not found: {src}")
                continue
            if dest.exists():
                shutil.rmtree(dest)
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.name.lower() == "readme.txt":
                    continue
                target = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            print(f"  staged -> {dest}")
    print("\nDone. Point GFM at this folder with:  --local-payloads "
          f"{out_dir}")


if __name__ == "__main__":
    main()
