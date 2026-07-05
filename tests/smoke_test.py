"""End-to-end smoke test with a fake Steam library — runs anywhere, no Steam
needed. Exercises: manifest load, appid detection via ACF, apply (with backup),
idempotent re-apply, verify, tamper->partial? (single-step: not_applied), revert.

Run:  python tests/smoke_test.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import detect, engine, manifest  # noqa: E402

PASS = 0


def check(desc: str, cond: bool):
    global PASS
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {desc}")
    if cond:
        PASS += 1
    else:
        sys.exit(f"Smoke test failed at: {desc}")


def build_fixture(root: Path):
    # Fake Steam library with L.A. Noire "installed"
    steam = root / "steam"
    game_dir = steam / "steamapps" / "common" / "L.A.Noire"
    game_dir.mkdir(parents=True)
    (game_dir / "LaNoire.exe").write_bytes(b"ORIGINAL GAME EXE v1.0")
    (steam / "steamapps" / "appmanifest_110800.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"110800"\n\t"name"\t\t"L.A. Noire"\n'
        '\t"installdir"\t\t"L.A.Noire"\n}\n', encoding="utf-8")

    # Fake store with a patched exe payload
    recipe_dir = root / "store" / "games" / "la-noire"
    (recipe_dir / "payload").mkdir(parents=True)
    (recipe_dir / "payload" / "LaNoire.exe").write_bytes(b"PATCHED EXE - widescreen fix")
    (recipe_dir / "manifest.json").write_text(json.dumps({
        "id": "la-noire", "name": "L.A. Noire",
        "aliases": ["LA Noire"], "steam_appid": 110800,
        "detect": {"install_dir_names": ["L.A.Noire"], "marker_files": ["LaNoire.exe"]},
        "steps": [{"type": "swap_exe", "payload": "payload/LaNoire.exe",
                   "target": "LaNoire.exe"}],
    }), encoding="utf-8")
    return steam, root / "store", game_dir


def main():
    tmp = Path(tempfile.mkdtemp(prefix="gfm_smoke_"))
    try:
        steam, store_root, game_dir = build_fixture(tmp)
        exe = game_dir / "LaNoire.exe"
        backup = game_dir / ("LaNoire.exe" + engine.BACKUP_SUFFIX)

        print("== load & detect ==")
        recipes = manifest.load_all(store_root)
        check("one recipe loads", len(recipes) == 1 and recipes[0].id == "la-noire")
        recipe = recipes[0]
        found = detect.find_game_dir(recipe, steam, remembered={})
        check("game dir found via appid/ACF", found == game_dir)
        found2 = detect.find_game_dir(recipe, steam, remembered={"la-noire": str(game_dir)})
        check("remembered path wins", found2 == game_dir)

        quiet = lambda _m: None  # noqa: E731
        ctx = engine.Ctx(recipe, game_dir, dry_run=False, log=quiet)

        print("== dry run ==")
        dry = engine.Ctx(recipe, game_dir, dry_run=True, log=quiet)
        engine.apply_recipe(recipe, dry)
        check("dry-run touches nothing", exe.read_bytes() == b"ORIGINAL GAME EXE v1.0"
              and not backup.exists())

        print("== apply ==")
        check("verify before: not applied",
              engine.verify_recipe(recipe, ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(recipe, ctx)
        check("exe is patched", exe.read_bytes().startswith(b"PATCHED"))
        check("original backed up", backup.read_bytes() == b"ORIGINAL GAME EXE v1.0")
        check("verify after: applied", engine.verify_recipe(recipe, ctx) == engine.APPLIED)

        print("== idempotency ==")
        engine.apply_recipe(recipe, ctx)  # run again
        check("re-apply keeps true original in backup",
              backup.read_bytes() == b"ORIGINAL GAME EXE v1.0")

        print("== game update clobbers mod (re-apply path) ==")
        exe.write_bytes(b"ORIGINAL GAME EXE v1.1 (steam updated)")
        check("verify detects drift", engine.verify_recipe(recipe, ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(recipe, ctx)
        check("re-patched after update", exe.read_bytes().startswith(b"PATCHED"))
        check("backup still original v1.0 (first write wins)",
              backup.read_bytes() == b"ORIGINAL GAME EXE v1.0")

        print("== revert ==")
        engine.revert_recipe(recipe, ctx)
        check("original restored", exe.read_bytes() == b"ORIGINAL GAME EXE v1.0")
        check("backup consumed", not backup.exists())
        check("verify after revert: not applied",
              engine.verify_recipe(recipe, ctx) == engine.NOT_APPLIED)

        print(f"\nAll {PASS} checks passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
