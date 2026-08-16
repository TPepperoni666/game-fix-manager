#!/usr/bin/env python
"""Build a standalone Windows gfm.exe — no Python needed on the target PC.

    python build-windows.py

Produces dist/gfm.exe: a single file bundling the code + the recipe store, with
zero third-party deps (the tool is pure stdlib). Copy it to the HTPC, run it, or
add it to Steam as a non-Steam shortcut so it shows up in Big Picture / FSE.

Note: a frozen exe can't `git pull` to update — rebuild and recopy when you want
the latest recipes/fixes. For a machine you actively develop on, a git checkout
(`python gfm.py`) is easier to keep current.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _lean_store(dest: Path) -> None:
    """Copy just the recipe DEFINITIONS (manifest.json + the registry) into
    dest — NOT the payload/artwork/saves folders. The committed payloads (Watch
    Dogs mod, Force Unleashed exe swaps, …) are hundreds of MB and belong on the
    NAS local-payloads, which payload_path reads first anyway. Keeps the exe
    ~15MB instead of ~800MB.

    _mirror_payloads then puts them ON the NAS — leaving them out here is only
    safe if they are actually there to fall back to."""
    src = ROOT / "store"
    (dest / "games").mkdir(parents=True, exist_ok=True)
    for reg in src.glob("*.json"):
        shutil.copy2(reg, dest / reg.name)
    for manifest in src.glob("games/*/manifest.json"):
        gdir = dest / "games" / manifest.parent.name
        gdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, gdir / "manifest.json")


def _mirror_payloads(root: Path) -> None:
    """Copy every committed payload to the NAS the lean exe reads from.

    _lean_store deliberately leaves payloads OUT of the exe — they're hundreds
    of MB and payload_path() prefers the NAS copy anyway. But nothing ever PUT
    them there. A Windows box therefore found 16 recipes failing with "payload
    missing": the lean store was all it had, and there was no
    _recipes/<id>/payload/ on the NAS to fall back to. Staging the exe without
    the files it depends on isn't staging it at all.

    Size + whole-second mtime skip, so a rebuild doesn't re-push ~700 MB over
    SMB every time."""
    src_root = ROOT / "store" / "games"
    copied = skipped = 0
    total = 0
    for game in sorted(src_root.iterdir()):
        src = game / "payload"
        if not src.is_dir():
            continue
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            dst = root / "_recipes" / game.name / "payload" / rel
            try:
                s = f.stat()
                if dst.is_file():
                    d = dst.stat()
                    if d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime):
                        skipped += 1
                        continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                copied += 1
                total += s.st_size
            except OSError as e:
                print(f"  ! {game.name}/{rel}: {e}")
    if copied:
        print(f"Mirrored {copied} payload file(s) "
              f"({total / (1 << 20):.0f} MB) -> {root / '_recipes'}")
    if skipped:
        print(f"  ({skipped} payload file(s) already current)")


def _publish_to_nas(exe: Path) -> None:
    """Drop the freshly built exe on the NAS so any Windows box can pick it up.

    A frozen exe can't `git pull` to update itself, so every change means
    copying a new one over — and the target machine is often asleep when you
    build. Staging it on the shared NAS decouples the two: build now, install
    whenever that machine is next on. Best effort; a missing/offline share just
    skips it."""
    for root in (Path("X:/"), Path(r"\\192.168.1.33\Game Fixes")):
        try:
            if not root.is_dir():
                continue
            dest = root / "_state" / "windows"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exe, dest / "gfm.exe")
            print(f"Staged on the NAS -> {dest / 'gfm.exe'}")
            # The exe is useless on its own — see _mirror_payloads.
            _mirror_payloads(root)
            print("  On a Windows box:  copy \"" + str(dest / "gfm.exe")
                  + "\" C:\\gfm.exe")
            print("  Then run 🔌 Connect NAS Payloads there — the lean exe "
                  "reads payloads from the NAS, so without it every recipe "
                  "that installs files fails.")
            return
        except OSError:
            continue
    print("(NAS not reachable — exe not staged; copy it across manually.)")


def main() -> int:
    if sys.platform != "win32":
        print("This build targets Windows — run it on the Windows box.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        lean = Path(tmp) / "store"
        _lean_store(lean)
        args = [
            sys.executable, "-m", "PyInstaller",
            "--onefile", "--console", "--name", "gfm",
            "--add-data", f"{lean}{';'}store",   # definitions only
            "--clean", "--noconfirm",
            str(ROOT / "gfm.py"),
        ]
        print("Building (recipe definitions only; payloads come from the NAS)…")
        r = subprocess.run(args)
    if r.returncode == 0:
        exe = ROOT / "dist" / "gfm.exe"
        mb = exe.stat().st_size / (1 << 20) if exe.is_file() else 0
        print(f"\nDone -> {exe}  ({mb:.0f} MB)")
        _publish_to_nas(exe)
        print("Copy it to the HTPC and run it, or add it to Steam as a "
              "non-Steam game. Recipe payloads (widescreen mods etc.) load from "
              "the NAS _recipes/<id>/payload/, so connect the NAS there too.")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
