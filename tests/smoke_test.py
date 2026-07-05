"""End-to-end smoke test with a fake Steam library — runs anywhere, no Steam
needed. Mirrors the real L.A. Noire V-Patch recipe: a copy_files tree with a
nested plugins/ dir, one file that overwrites a pre-existing original (backup
path) and files that are brand new (add/remove path).

Run:  python tests/smoke_test.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import detect, engine, fetch, manifest  # noqa: E402
from core.steps.copy_files import file_hash  # noqa: E402

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
    # Fake Steam library with L.A. Noire "installed".
    # The game ships its own dinput8.dll so the overwrite/backup path is tested.
    steam = root / "steam"
    game_dir = steam / "steamapps" / "common" / "L.A.Noire"
    game_dir.mkdir(parents=True)
    (game_dir / "LaNoire.exe").write_bytes(b"GAME EXE")
    (game_dir / "dinput8.dll").write_bytes(b"STOCK DINPUT8")
    (steam / "steamapps" / "appmanifest_110800.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"110800"\n\t"name"\t\t"L.A. Noire"\n'
        '\t"installdir"\t\t"L.A.Noire"\n}\n', encoding="utf-8")

    # Fake store mirroring the real V-Patch payload layout
    recipe_dir = root / "store" / "games" / "la-noire"
    vpatch = recipe_dir / "payload" / "vpatch"
    (vpatch / "plugins").mkdir(parents=True)
    (vpatch / "dinput8.dll").write_bytes(b"ASI LOADER DINPUT8")
    (vpatch / "plugins" / "lanvp.asi").write_bytes(b"VPATCH ASI")
    (vpatch / "plugins" / "lanvp.ini").write_bytes(b"fps_unlock=1")
    (recipe_dir / "manifest.json").write_text(json.dumps({
        "id": "la-noire", "name": "L.A. Noire",
        "aliases": ["LA Noire"], "steam_appid": 110800,
        "detect": {"install_dir_names": ["L.A.Noire"], "marker_files": ["LaNoire.exe"]},
        "steps": [{"type": "copy_files", "from": "payload/vpatch",
                   "to": "{game_dir}", "backup_originals": True}],
        "post_apply_message": "set WINEDLLOVERRIDES",
    }), encoding="utf-8")
    return steam, root / "store", game_dir


def main():
    tmp = Path(tempfile.mkdtemp(prefix="gfm_smoke_"))
    try:
        steam, store_root, game_dir = build_fixture(tmp)
        dll = game_dir / "dinput8.dll"
        dll_backup = game_dir / ("dinput8.dll" + engine.BACKUP_SUFFIX)
        asi = game_dir / "plugins" / "lanvp.asi"
        ini = game_dir / "plugins" / "lanvp.ini"

        print("== load & detect ==")
        recipes = manifest.load_all(store_root)
        check("one recipe loads", len(recipes) == 1 and recipes[0].id == "la-noire")
        recipe = recipes[0]
        check("post_apply_message loads", recipe.post_apply_message.startswith("set "))
        found = detect.find_game_dir(recipe, steam, remembered={})
        check("game dir found via appid/ACF", found == game_dir)
        found2 = detect.find_game_dir(recipe, steam, remembered={"la-noire": str(game_dir)})
        check("remembered path wins", found2 == game_dir)

        quiet = lambda _m: None  # noqa: E731
        ctx = engine.Ctx(recipe, game_dir, dry_run=False, log=quiet)

        print("== dry run ==")
        dry = engine.Ctx(recipe, game_dir, dry_run=True, log=quiet)
        engine.apply_recipe(recipe, dry)
        check("dry-run touches nothing",
              dll.read_bytes() == b"STOCK DINPUT8" and not asi.exists()
              and not dll_backup.exists())

        print("== apply ==")
        check("verify before: not applied",
              engine.verify_recipe(recipe, ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(recipe, ctx)
        check("dll overwritten", dll.read_bytes() == b"ASI LOADER DINPUT8")
        check("stock dll backed up", dll_backup.read_bytes() == b"STOCK DINPUT8")
        check("nested plugins dir created", asi.read_bytes() == b"VPATCH ASI"
              and ini.read_bytes() == b"fps_unlock=1")
        check("verify after: applied", engine.verify_recipe(recipe, ctx) == engine.APPLIED)

        print("== idempotency ==")
        engine.apply_recipe(recipe, ctx)  # run again
        check("re-apply keeps true original in backup",
              dll_backup.read_bytes() == b"STOCK DINPUT8")

        print("== game update clobbers mod (re-apply path) ==")
        dll.write_bytes(b"STOCK DINPUT8 v1.1 (steam updated)")
        check("verify detects drift", engine.verify_recipe(recipe, ctx) == engine.PARTIAL)
        engine.apply_recipe(recipe, ctx)
        check("re-patched after update", dll.read_bytes() == b"ASI LOADER DINPUT8")
        check("backup still stock v1.0 (first write wins)",
              dll_backup.read_bytes() == b"STOCK DINPUT8")

        print("== revert ==")
        engine.revert_recipe(recipe, ctx)
        check("stock dll restored", dll.read_bytes() == b"STOCK DINPUT8")
        check("backup consumed", not dll_backup.exists())
        check("added files removed", not asi.exists() and not ini.exists())
        check("verify after revert: not applied",
              engine.verify_recipe(recipe, ctx) == engine.NOT_APPLIED)

        print("== remote payload fetch (file:// stand-in for release asset) ==")
        big = tmp / "hosted" / "patch.dat"
        big.parent.mkdir()
        big.write_bytes(b"BIG MOD DATA " * 1000)
        recipe.remote_payloads = [{
            "path": "payload/vpatch/patch.dat", "url": big.as_uri(),
            "sha256": file_hash(big), "size": big.stat().st_size}]
        fetched = recipe.dir / "payload" / "vpatch" / "patch.dat"
        fetch.ensure_remote_payloads(recipe, log=quiet)
        check("missing payload downloaded + verified",
              fetched.read_bytes() == big.read_bytes())
        stamp = fetched.stat().st_mtime_ns
        fetch.ensure_remote_payloads(recipe, log=quiet)
        check("present payload not re-downloaded", fetched.stat().st_mtime_ns == stamp)
        recipe.remote_payloads[0]["sha256"] = "0" * 64
        fetched.unlink()
        try:
            fetch.ensure_remote_payloads(recipe, log=quiet)
            check("hash mismatch rejected", False)
        except fetch.FetchError:
            check("hash mismatch rejected", not fetched.exists())

        print(f"\nAll {PASS} checks passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
