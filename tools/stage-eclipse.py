#!/usr/bin/env python3
"""Stage Eclipse mod archives into a local-payloads folder.

Eclipse perf mods contain modified copyrighted game exes/paks, so they never
go in the git store — they live in your local-payloads folder (NAS/SD). This
script extracts each Eclipse archive and lays it out where the recipes expect:
  <local-payloads>/<recipe-id>/payload/mod/<game-dir mirror>

The per-game mapping (which archive → which recipe → which inner variant
folder to copy from, e.g. "For Licence") is read from
store/eclipse_index.json, generated alongside the recipes so the two never
drift.

Usage:
  python tools/stage-eclipse.py <archive-folder> <local-payloads-folder>

Needs 7z (Windows: 7-Zip; Linux: p7zip) for .rar/.zip extraction. Run it
wherever the archives and the (synced/mounted) local-payloads folder are both
reachable — e.g. your Windows box if local-payloads is a Syncthing folder.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "store" / "eclipse_index.json"


def _sevenzip() -> str:
    for c in ("7z", "7zz", r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if shutil.which(c) or Path(c).is_file():
            return c
    sys.exit("7z not found — install 7-Zip (Windows) or p7zip (Linux).")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    arc_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    sevenzip = _sevenzip()

    archives = [p for p in sorted(arc_dir.iterdir())
                if p.suffix.lower() in (".rar", ".zip", ".7z")]
    if not archives:
        sys.exit(f"No .rar/.zip/.7z archives in {arc_dir}")

    staged = skipped = 0
    for arc in archives:
        # Match this archive to a recipe by its rar_match fragment
        hit = None
        for rid, meta in index.items():
            if meta["rar_match"].lower() in arc.name.lower():
                hit = (rid, meta)
                break
        if hit is None:
            print(f"SKIP (no recipe matches): {arc.name}")
            skipped += 1
            continue
        rid, meta = hit
        dest = out_dir / rid / "payload" / "mod"
        print(f"\n{arc.name}\n  -> {rid}")
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sevenzip, "x", str(arc), f"-o{tmp}", "-y"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                print(f"  ! extract failed: {(r.stderr or '').strip()[:120]}")
                skipped += 1
                continue
            # The archive has a single top folder; stage_from is relative to it
            tops = [p for p in Path(tmp).iterdir() if p.is_dir()]
            top = tops[0] if len(tops) == 1 else Path(tmp)
            src = top / meta["stage_from"]
            if not src.is_dir():
                # some archives extract stage_from directly at top level
                alt = Path(tmp) / meta["stage_from"]
                src = alt if alt.is_dir() else src
            if not src.is_dir():
                print(f"  ! expected folder not found: {meta['stage_from']}")
                skipped += 1
                continue
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.name.lower().startswith("readme") or \
                        item.suffix.lower() == ".txt":
                    continue
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            print(f"  staged -> {dest}")
            staged += 1

    print(f"\nDone: {staged} staged, {skipped} skipped.")
    if staged:
        print(f"Point GFM at it:  python3 gfm.py --local-payloads {out_dir}")


if __name__ == "__main__":
    main()
