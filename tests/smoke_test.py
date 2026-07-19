"""End-to-end smoke test with a fake Steam library — runs anywhere, no Steam
needed. Mirrors the real L.A. Noire V-Patch recipe: a copy_files tree with a
nested plugins/ dir, one file that overwrites a pre-existing original (backup
path) and files that are brand new (add/remove path).

Run:  python tests/smoke_test.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fetch  # noqa: E402  (first, to guard against import cycles)
from core import detect, engine, manifest  # noqa: E402
from core.hashutil import file_hash  # noqa: E402

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

        # Engine siblings can share marker files (Shift 2 vs Automobilista 2
        # both have PakFiles/BOOTFLOW.bff) — a name match must always win.
        decoy = steam / "steamapps" / "common" / "AAA Decoy Engine Sibling"
        decoy.mkdir(parents=True)
        (decoy / "LaNoire.exe").write_bytes(b"NOT THE GAME")  # recipe's marker file
        (steam / "steamapps" / "appmanifest_110800.acf").unlink()  # force marker path
        found3 = detect.find_game_dir(recipe, steam, remembered={})
        check("name match beats decoy with same marker", found3 == game_dir)
        (steam / "steamapps" / "appmanifest_110800.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"110800"\n\t"name"\t\t"L.A. Noire"\n'
            '\t"installdir"\t\t"L.A.Noire"\n}\n', encoding="utf-8")

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

        print("== remote payload with extract_to (zip stand-in for TCU 7z) ==")
        import zipfile
        hosted_zip = tmp / "hosted" / "patch_pkg.zip"
        with zipfile.ZipFile(hosted_zip, "w") as z:
            z.writestr("hook.dll", "FAKE HOOK")
            z.writestr("sub/addon.xml", "<addon/>")
        recipe.remote_payloads = [{
            "path": "payload/downloads/patch_pkg.zip", "url": hosted_zip.as_uri(),
            "sha256": file_hash(hosted_zip), "size": hosted_zip.stat().st_size,
            "extract_to": "payload/patch"}]
        fetch.ensure_remote_payloads(recipe, log=quiet)
        extracted = recipe.dir / "payload" / "patch"
        check("archive downloaded and extracted",
              (extracted / "hook.dll").read_text() == "FAKE HOOK"
              and (extracted / "sub" / "addon.xml").is_file())
        marker = extracted / "hook.dll"
        stamp = marker.stat().st_mtime_ns
        fetch.ensure_remote_payloads(recipe, log=quiet)
        check("no re-extract when archive and dir present",
              marker.stat().st_mtime_ns == stamp)

        print("== remove_files + pak_edit (+ optional step handling) ==")
        import json as _j
        import zipfile as _zf
        pk_dir = tmp / "store" / "games" / "pak-game"
        (pk_dir / "payload" / "wet").mkdir(parents=True)
        (pk_dir / "payload" / "wet" / "hero.material").write_bytes(b"WET MATERIAL")
        pak = game_dir / "pak0.lp"
        with _zf.ZipFile(pak, "w") as z:
            z.writestr("Chars/hero/hero.material", "DRY MATERIAL")
            z.writestr("Chars/hero/hero.gto", "MODEL DATA")
            z.writestr("FMV/LEC", "logo video")
        (game_dir / "intro.bik").write_bytes(b"INTRO")
        (pk_dir / "manifest.json").write_text(_j.dumps({
            "id": "pak-game", "name": "Pak Game",
            "steps": [
                {"type": "remove_files", "targets": ["intro.bik"]},
                {"type": "pak_edit", "archive": "pak0.lp",
                 "insert": [{"from": "payload/wet", "into": "Chars/hero"}]},
                {"type": "pak_edit", "optional": True, "archive": "pak0.lp",
                 "insert": [{"from": "payload/missing", "into": "X"}]},
            ]}), encoding="utf-8")
        pk = [r for r in manifest.load_all(tmp / "store") if r.id == "pak-game"][0]
        pk_ctx = engine.Ctx(pk, game_dir, dry_run=False, log=quiet)
        check("verify before: not applied",
              engine.verify_recipe(pk, pk_ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(pk, pk_ctx)  # optional step must not raise
        check("intro renamed away", not (game_dir / "intro.bik").exists()
              and (game_dir / "intro.bik.gfm-orig").read_bytes() == b"INTRO")
        with _zf.ZipFile(pak) as z:
            check("pak member replaced", z.read("Chars/hero/hero.material") == b"WET MATERIAL")
            check("other members untouched", z.read("Chars/hero/hero.gto") == b"MODEL DATA"
                  and z.read("FMV/LEC") == b"logo video")
        check("compact pak backup exists",
              (game_dir / "pak0.lp.gfm-pakbak.zip").stat().st_size < 1000)
        check("verify after: applied", engine.verify_recipe(pk, pk_ctx) == engine.APPLIED)
        engine.apply_recipe(pk, pk_ctx)  # idempotent — must skip rewrite
        engine.revert_recipe(pk, pk_ctx)
        with _zf.ZipFile(pak) as z:
            check("pak reverted to stock", z.read("Chars/hero/hero.material") == b"DRY MATERIAL")
        check("intro restored", (game_dir / "intro.bik").read_bytes() == b"INTRO")
        check("pak backup consumed", not (game_dir / "pak0.lp.gfm-pakbak.zip").exists())

        print("== systemd_unit (redirected unit dir; no-systemctl path on Windows) ==")
        import json as _json
        import os as _os
        unit_dir = tmp / "fake_systemd_user"
        _os.environ["GFM_SYSTEMD_USER_DIR"] = str(unit_dir)
        svc_dir = tmp / "store" / "games" / "svc-game"
        (svc_dir / "payload").mkdir(parents=True)
        (svc_dir / "payload" / "test.service").write_text(
            "[Unit]\nDescription=test\n", encoding="utf-8")
        (svc_dir / "manifest.json").write_text(_json.dumps({
            "id": "svc-game", "name": "Svc Game",
            "steps": [{"type": "systemd_unit", "unit": "payload/test.service",
                       "scope": "user", "enable": True}],
        }), encoding="utf-8")
        svc_recipe = [r for r in manifest.load_all(tmp / "store")
                      if r.id == "svc-game"][0]
        svc_ctx = engine.Ctx(svc_recipe, game_dir, dry_run=False, log=quiet)
        check("verify before: not applied",
              engine.verify_recipe(svc_recipe, svc_ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(svc_recipe, svc_ctx)
        installed = unit_dir / "test.service"
        check("unit file installed", installed.read_text(encoding="utf-8").startswith("[Unit]"))
        status = engine.verify_recipe(svc_recipe, svc_ctx)
        check("verify after install", status in (engine.APPLIED, engine.PARTIAL))
        engine.revert_recipe(svc_recipe, svc_ctx)
        check("unit file removed on revert", not installed.exists())
        del _os.environ["GFM_SYSTEMD_USER_DIR"]

        print("== launch_options + VDF roundtrip ==")
        from core import steamvdf
        cfgdir = steam / "userdata" / "12345678" / "config"
        cfgdir.mkdir(parents=True)
        (cfgdir / "localconfig.vdf").write_text(
            '"UserLocalConfigStore"\n{\n'
            '\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
            '\t\t\t\t"apps"\n\t\t\t\t{\n'
            '\t\t\t\t\t"110800"\n\t\t\t\t\t{\n\t\t\t\t\t\t"playtime"\t\t"42"\n\t\t\t\t\t}\n'
            '\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n'
            '\t"friends"\n\t{\n\t\t"name"\t\t"Tony \\"Pep\\" P"\n\t}\n}\n',
            encoding="utf-8")
        opts = 'WINEDLLOVERRIDES="dinput8=n,b" %command%'
        lo_dir = tmp / "store" / "games" / "lo-game"
        (lo_dir / "payload").mkdir(parents=True)
        (lo_dir / "manifest.json").write_text(_json.dumps({
            "id": "lo-game", "name": "LO Game", "steam_appid": 110800,
            "steps": [{"type": "launch_options", "value": opts}],
        }), encoding="utf-8")
        lo_recipe = [r for r in manifest.load_all(tmp / "store") if r.id == "lo-game"][0]
        lo_ctx = engine.Ctx(lo_recipe, game_dir, dry_run=False, log=quiet,
                            steam_root=steam)
        check("verify before: not applied",
              engine.verify_recipe(lo_recipe, lo_ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(lo_recipe, lo_ctx)
        check("write queued, not written yet",
              len(lo_ctx.deferred_vdf_writes) == 1
              and engine.verify_recipe(lo_recipe, lo_ctx) == engine.NOT_APPLIED)
        w = lo_ctx.deferred_vdf_writes[0]
        n = steamvdf.set_launch_options(steam, w["appid"], w["value"])
        check("one user file updated", n == 1)
        check("verify after flush: applied",
              engine.verify_recipe(lo_recipe, lo_ctx) == engine.APPLIED)
        tree = steamvdf.vdf_loads((cfgdir / "localconfig.vdf").read_text(encoding="utf-8"))
        apps = tree["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
        check("launch options exact (quotes survive)",
              apps["110800"]["LaunchOptions"] == opts)
        check("existing keys preserved",
              apps["110800"]["playtime"] == "42"
              and tree["UserLocalConfigStore"]["friends"]["name"] == 'Tony "Pep" P')
        check("backup written",
              (cfgdir / "localconfig.vdf.gfm-bak").is_file())
        n2 = steamvdf.set_launch_options(steam, w["appid"], w["value"])
        check("idempotent second write", n2 == 0)

        print("== non-Steam shortcuts (binary vdf): detect + launch options ==")
        from core import shortcutsvdf
        crew_dir = tmp / "games" / "TheCrew"
        crew_dir.mkdir(parents=True)
        (crew_dir / "TheCrew.exe").write_bytes(b"EXE")
        sc_root = {"shortcuts": (0, {
            "0": (0, {
                "appid": (2, 0xDEADBEEF),
                "AppName": (1, "The Crew Unlimited"),
                "Exe": (1, f'"{crew_dir / "TheCrew.exe"}"'),
                "StartDir": (1, f'"{crew_dir}"'),
                "LaunchOptions": (1, ""),
                "tags": (0, {}),
            }),
            "1": (0, {"appname": (1, "Other Game"),
                      "Exe": (1, '"/somewhere/else.exe"')}),
        })}
        scdir = steam / "userdata" / "12345678" / "config"
        raw = shortcutsvdf.dumps(sc_root)
        (scdir / "shortcuts.vdf").write_bytes(raw)
        check("binary vdf roundtrip byte-identical",
              shortcutsvdf.dumps(shortcutsvdf.loads(raw)) == raw)

        crew_store = tmp / "store" / "games" / "crew-game"
        (crew_store / "payload").mkdir(parents=True)
        (crew_store / "manifest.json").write_text(_json.dumps({
            "id": "crew-game", "name": "The Crew",
            "aliases": ["The Crew Unlimited", "TCU"], "steam_appid": 241560,
            "steps": [{"type": "launch_options", "value": opts}],
        }), encoding="utf-8")
        crew = [r for r in manifest.load_all(tmp / "store") if r.id == "crew-game"][0]
        check("game dir found via non-Steam shortcut",
              detect.find_game_dir(crew, steam, {}) == crew_dir)

        crew_ctx = engine.Ctx(crew, crew_dir, dry_run=False, log=quiet,
                              steam_root=steam)
        engine.apply_recipe(crew, crew_ctx)
        cw = crew_ctx.deferred_vdf_writes[0]
        check("shortcut targeted over appid", cw["kind"] == "shortcut")
        n3 = shortcutsvdf.set_launch_options(steam, cw["names"], cw["value"])
        check("shortcut launch options written", n3 == 1)
        check("verify after shortcut flush: applied",
              engine.verify_recipe(crew, crew_ctx) == engine.APPLIED)
        again = shortcutsvdf.loads((scdir / "shortcuts.vdf").read_bytes())
        entry = again["shortcuts"][1]["0"][1]
        check("other shortcut fields intact",
              entry["AppName"][1] == "The Crew Unlimited"
              and entry["appid"][1] == 0xDEADBEEF
              and (scdir / "shortcuts.vdf.gfm-bak").is_file())

        print("== tool recipe (requires_game: false) ==")
        tool_dir = tmp / "store" / "games" / "a-tool"
        (tool_dir / "payload").mkdir(parents=True)
        (tool_dir / "payload" / "Tool.desktop").write_text("[Desktop Entry]\n")
        (tool_dir / "manifest.json").write_text(_json.dumps({
            "id": "a-tool", "name": "A Tool", "requires_game": False,
            "steps": [{"type": "copy_files", "from": "payload/Tool.desktop",
                       "to": "{game_dir}/Desktop", "backup_originals": False,
                       "executable": True}],
        }), encoding="utf-8")
        tool = [r for r in manifest.load_all(tmp / "store") if r.id == "a-tool"][0]
        check("requires_game flag loads", tool.requires_game is False)
        check("game recipes default to requires_game", recipe.requires_game is True)
        fake_home = tmp / "fakehome"
        tool_ctx = engine.Ctx(tool, fake_home, dry_run=False, log=quiet)
        engine.apply_recipe(tool, tool_ctx)
        check("tool staged to desktop",
              (fake_home / "Desktop" / "Tool.desktop").is_file())
        check("tool verify applied",
              engine.verify_recipe(tool, tool_ctx) == engine.APPLIED)

        print("== wine_registry (prefix user.reg, non-Steam appid) ==")
        # prefix belongs to a non-Steam shortcut: appid 0xDEADBEEF from the
        # shortcuts.vdf fixture above -> compatdata/3735928559
        pfx = steam / "steamapps" / "compatdata" / str(0xDEADBEEF) / "pfx"
        pfx.mkdir(parents=True)
        (pfx / "user.reg").write_text(
            "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n"
            "[Software\\\\Existing\\\\Key] 1600000000\n"
            '"Untouched"="yes"\n', encoding="utf-8")
        rg_dir = tmp / "store" / "games" / "rg-game"
        (rg_dir / "payload").mkdir(parents=True)
        (rg_dir / "manifest.json").write_text(_json.dumps({
            "id": "rg-game", "name": "RG Game",
            "aliases": ["The Crew Unlimited"],
            "steps": [{"type": "wine_registry", "key": "Software\\THQ\\Barnyard",
                       "values": {"ControllerEnabled": 1, "Player": "Tony"}}],
        }), encoding="utf-8")
        rg = [r for r in manifest.load_all(tmp / "store") if r.id == "rg-game"][0]
        rg_ctx = engine.Ctx(rg, game_dir, dry_run=False, log=quiet, steam_root=steam)
        check("verify before: not applied",
              engine.verify_recipe(rg, rg_ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(rg, rg_ctx)
        regtext = (pfx / "user.reg").read_text(encoding="utf-8")
        check("dword + string written",
              '"ControllerEnabled"=dword:00000001' in regtext
              and '"Player"="Tony"' in regtext)
        check("existing keys untouched", '"Untouched"="yes"' in regtext)
        check("verify after: applied", engine.verify_recipe(rg, rg_ctx) == engine.APPLIED)
        engine.apply_recipe(rg, rg_ctx)  # idempotent
        check("no duplicate section",
              (pfx / "user.reg").read_text(encoding="utf-8").count("THQ") == 1)
        engine.revert_recipe(rg, rg_ctx)
        regtext = (pfx / "user.reg").read_text(encoding="utf-8")
        check("managed values removed on revert",
              "ControllerEnabled" not in regtext and '"Untouched"="yes"' in regtext)

        print("== step-free (documentation-only) recipe ==")
        doc_dir = tmp / "store" / "games" / "doc-game"
        doc_dir.mkdir(parents=True)
        (doc_dir / "manifest.json").write_text(
            _json.dumps({"id": "doc-game", "name": "Doc Game"}),
            encoding="utf-8")
        doc = [r for r in manifest.load_all(tmp / "store")
               if r.id == "doc-game"][0]
        check("step-free recipe loads (steps optional)", doc.steps == [])
        doc_ctx = engine.Ctx(doc, game_dir, dry_run=False, log=quiet)
        engine.apply_recipe(doc, doc_ctx)  # must not raise
        check("verify step-free = APPLIED",
              engine.verify_recipe(doc, doc_ctx) == engine.APPLIED)

        print("== wine_registry: system hive + {game_dir_win} template ==")
        # New system.reg alongside user.reg — same prefix
        (pfx / "system.reg").write_text(
            "WINE REGISTRY Version 2\n"
            "[Software\\\\Existing\\\\Key] 1600000000\n"
            '"Untouched"="yes"\n', encoding="utf-8")
        vsc_dir = tmp / "store" / "games" / "vsc-game"
        (vsc_dir / "payload").mkdir(parents=True)
        (vsc_dir / "manifest.json").write_text(_json.dumps({
            "id": "vsc-game", "name": "VSC Game",
            "aliases": ["The Crew Unlimited"],
            "steps": [{
                "type": "wine_registry", "hive": "system",
                "key": "Software\\WOW6432Node\\Codemasters\\Race Driver 3",
                "values": {
                    "PATH_APPLICATION": "{game_dir_win}",
                    "PATH_MAIN": "{game_dir_win}\\gamedata",
                    "NAME_APPLICATION": "V8 Supercars 3",
                    "SKU": "aus"}}]}), encoding="utf-8")
        vsc = [r for r in manifest.load_all(tmp / "store")
               if r.id == "vsc-game"][0]
        toca_game = tmp / "run" / "media" / "deck" / "primary" / "V8 Supercars 3"
        toca_game.mkdir(parents=True)
        vsc_ctx = engine.Ctx(vsc, toca_game, dry_run=False,
                             log=quiet, steam_root=steam)
        engine.apply_recipe(vsc, vsc_ctx)
        sysreg = (pfx / "system.reg").read_text(encoding="utf-8")
        expected_win = "Z:" + str(toca_game).replace("/", "\\")
        expected_esc = expected_win.replace("\\", "\\\\")
        check("hklm written to system.reg (not user.reg)",
              '"NAME_APPLICATION"="V8 Supercars 3"' in sysreg
              and '"NAME_APPLICATION"' not in
                  (pfx / "user.reg").read_text(encoding="utf-8"))
        check("{game_dir_win} expands to Z:\\ path with escaped backslashes",
              f'"PATH_APPLICATION"="{expected_esc}"' in sysreg
              and f'"PATH_MAIN"="{expected_esc}\\\\gamedata"' in sysreg)
        check("plain string SKU untouched by templates",
              '"SKU"="aus"' in sysreg)
        check("verify sees the templated values as applied",
              engine.verify_recipe(vsc, vsc_ctx) == engine.APPLIED)
        check("system.reg's untouched key preserved",
              '"Untouched"="yes"' in sysreg)

        print("== prefix reconcile: adopt-existing (shortcut appid rewrite) ==")
        from core import prefixes
        # Fixture: an ORPHAN prefix for "The Crew Unlimited" at the old appid
        # 111222333 (populated with a marker), and the shortcut created above
        # points at 0xDEADBEEF = 3735928559 — currently no prefix there.
        orphan = steam / "steamapps" / "compatdata" / "111222333" / "pfx" / "drive_c" / "Program Files" / "TheCrew"
        orphan.mkdir(parents=True)
        (orphan / "TheCrew.exe").write_bytes(b"EXE")
        # GBM CSV that names the orphan
        gbm_csv = tmp / "non_steam_games.csv"
        gbm_csv.write_text("111222333,The_Crew\n", encoding="utf-8")
        check("gbm csv loads", prefixes.load_gbm_csv(gbm_csv)
              == {"111222333": "The_Crew"})
        # Fake GBM SD backup layout — the STRONGEST identification signal.
        sd = tmp / "sdcard"
        bk = sd / "steamos_restore" / "prefix_backups" / "The_Crew" / "111222333"
        bk.mkdir(parents=True)
        (bk / "pfx").mkdir()  # backup folder just needs to exist
        backup_map = prefixes.load_gbm_backup_map([sd])
        check("gbm backup folder map loads",
              backup_map == {"111222333": "The_Crew"})
        # drive_c signal alone (without csv OR backup map)
        gbm_empty: dict[str, str] = {}
        cands = prefixes.find_candidates(
            steam, crew, gbm_empty, None, exclude_appids={0xDEADBEEF})
        check("drive_c scan identifies the orphan",
              len(cands) == 1 and cands[0][0].name == "111222333"
              and cands[0][1] == "drive_c" and "TheCrew" in cands[0][2])
        # Backup map wins over CSV wins over drive_c
        cands = prefixes.find_candidates(
            steam, crew, gbm_empty, backup_map, exclude_appids={0xDEADBEEF})
        check("backup signal beats drive_c",
              cands[0][1] == "backup" and cands[0][2] == "The_Crew")
        # Pre-existing CompatToolMapping under the CURRENT shortcut appid
        cfg_v = steam / "config" / "config.vdf"
        cfg_v.parent.mkdir(exist_ok=True)
        cfg_v.write_text(
            '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n'
            '\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
            f'\t\t\t\t\t"{0xDEADBEEF}"\n\t\t\t\t\t{{\n'
            '\t\t\t\t\t\t"name"\t\t"proton_9"\n'
            '\t\t\t\t\t\t"config"\t\t""\n'
            '\t\t\t\t\t\t"priority"\t\t"250"\n'
            '\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n',
            encoding="utf-8")
        check("compat mapping present pre-reconcile",
              steamvdf.get_compat_tool(steam, 0xDEADBEEF)["name"] == "proton_9")

        # Rewrite: shortcut appid + config compat mapping
        n = shortcutsvdf.set_appid(steam, crew.all_names, 111222333)
        check("shortcut appid rewritten in one file", n == 1)
        again = shortcutsvdf.loads((scdir / "shortcuts.vdf").read_bytes())
        entry = again["shortcuts"][1]["0"][1]
        check("shortcut now points at orphan appid",
              entry["appid"][1] == 111222333)
        check("shortcut backup written",
              (scdir / "shortcuts.vdf.gfm-bak").is_file())
        moved = steamvdf.remap_compat_tool(steam, 0xDEADBEEF, 111222333)
        check("compat mapping moved to new appid", moved
              and steamvdf.get_compat_tool(steam, 111222333)["name"] == "proton_9"
              and steamvdf.get_compat_tool(steam, 0xDEADBEEF) is None)
        # And detect.find_prefix now finds it via the (updated) shortcut
        check("detect finds the adopted prefix",
              detect.find_prefix(crew, steam)
              == steam / "steamapps" / "compatdata" / "111222333" / "pfx")

        print("== local-payload override (off-git files win) ==")
        # Recipe that swaps an exe from payload/patched/game.exe
        ov_recipe_dir = tmp / "store" / "games" / "ov-game"
        (ov_recipe_dir / "payload" / "patched").mkdir(parents=True)
        (ov_recipe_dir / "payload" / "patched" / "game.exe").write_bytes(b"GIT VERSION")
        ov_game = tmp / "ovgame"
        ov_game.mkdir()
        (ov_game / "game.exe").write_bytes(b"ORIGINAL")
        (ov_recipe_dir / "manifest.json").write_text(_json.dumps({
            "id": "ov-game", "name": "OV Game",
            "steps": [{"type": "swap_exe",
                       "payload": "payload/patched/game.exe",
                       "target": "game.exe"}]}), encoding="utf-8")
        ov = [r for r in manifest.load_all(tmp / "store") if r.id == "ov-game"][0]
        # Without override: uses the git-committed payload
        ctx_git = engine.Ctx(ov, ov_game, dry_run=False, log=quiet)
        check("payload_path returns git file when no override",
              ctx_git.payload_path("payload/patched/game.exe").read_bytes()
              == b"GIT VERSION")
        # With override: a local file wins
        local_dir = tmp / "nas_payloads"
        (local_dir / "ov-game" / "payload" / "patched").mkdir(parents=True)
        (local_dir / "ov-game" / "payload" / "patched" / "game.exe").write_bytes(
            b"LOCAL NAS VERSION")
        ctx_ov = engine.Ctx(ov, ov_game, dry_run=False, log=quiet,
                            local_payloads_dir=local_dir)
        check("local override wins over git payload",
              ctx_ov.payload_path("payload/patched/game.exe").read_bytes()
              == b"LOCAL NAS VERSION")
        engine.apply_recipe(ov, ctx_ov)
        check("apply uses the local override file",
              (ov_game / "game.exe").read_bytes() == b"LOCAL NAS VERSION")
        # A recipe payload that ONLY exists locally (never in git) still resolves
        local_only = tmp / "store" / "games" / "lo-only"
        (local_only).mkdir(parents=True)
        (local_only / "manifest.json").write_text(_json.dumps({
            "id": "lo-only", "name": "Local Only",
            "steps": [{"type": "swap_exe", "payload": "payload/crack.exe",
                       "target": "game.exe"}]}), encoding="utf-8")
        lo = [r for r in manifest.load_all(tmp / "store") if r.id == "lo-only"][0]
        (local_dir / "lo-only" / "payload").mkdir(parents=True)
        (local_dir / "lo-only" / "payload" / "crack.exe").write_bytes(b"CRACK")
        ctx_lo = engine.Ctx(lo, ov_game, dry_run=False, log=quiet,
                            local_payloads_dir=local_dir)
        check("local-only payload resolves (not in git at all)",
              ctx_lo.payload_path("payload/crack.exe").read_bytes() == b"CRACK")
        # override path escape is refused
        try:
            ctx_ov.payload_path("../../../etc/passwd")
            check("override path escape refused", False)
        except (engine.StepError, Exception):
            check("override path escape refused", True)

        print("== {prefix} target template ==")
        pt_recipe = tmp / "store" / "games" / "pt-game"
        (pt_recipe / "payload").mkdir(parents=True)
        (pt_recipe / "payload" / "Engine.ini").write_text("[Core]\nx=1\n")
        (pt_recipe / "manifest.json").write_text(_json.dumps({
            "id": "pt-game", "name": "PT Game", "steam_appid": 110800,
            "steps": [{"type": "copy_files", "from": "payload",
                       "to": "{prefix_localappdata}/MyGame/Saved/Config",
                       "backup_originals": False}]}), encoding="utf-8")
        pt = [r for r in manifest.load_all(tmp / "store") if r.id == "pt-game"][0]
        # steam fixture already has compatdata for 110800? build the prefix
        pt_pfx = steam / "steamapps" / "compatdata" / "110800" / "pfx"
        pt_pfx.mkdir(parents=True, exist_ok=True)
        (pt_pfx / "drive_c").mkdir(exist_ok=True)
        pt_ctx = engine.Ctx(pt, game_dir, dry_run=False, log=quiet, steam_root=steam)
        engine.apply_recipe(pt, pt_ctx)
        landed = (pt_pfx / "drive_c" / "users" / "steamuser" / "AppData"
                  / "Local" / "MyGame" / "Saved" / "Config" / "Engine.ini")
        check("{prefix_localappdata} lands file inside the prefix",
              landed.is_file() and landed.read_text().startswith("[Core]"))

        print("== Steam library scan + map merge ==")
        from core import steamscan
        # Add a fake AMS2 manifest that our La Noire fixture-steam already has
        # (110800). Also a game with no recipe: 1091500 Cyberpunk 2077.
        (steam / "steamapps" / "appmanifest_1091500.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"1091500"\n'
            '\t"name"\t\t"Cyberpunk 2077"\n'
            '\t"installdir"\t\t"Cyberpunk 2077"\n}\n', encoding="utf-8")
        (steam / "steamapps" / "common" / "Cyberpunk 2077").mkdir()
        # Skip-name check: junk manifest for a Proton runtime
        (steam / "steamapps" / "appmanifest_1493710.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"1493710"\n'
            '\t"name"\t\t"Proton Experimental"\n'
            '\t"installdir"\t\t"Proton - Experimental"\n}\n', encoding="utf-8")
        (steam / "steamapps" / "common" / "Proton - Experimental").mkdir()

        installed = steamscan.scan(steam)
        appids_found = {g["appid"] for g in installed}
        check("scan finds La Noire",  "110800" in appids_found)
        check("scan finds Cyberpunk", "1091500" in appids_found)
        check("scan skips Proton",    "1493710" not in appids_found)
        check("library_kind labelled per game",
              all(g.get("library_kind") in ("internal", "sd", "other")
                  for g in installed))
        _steam_recipes = manifest.load_all(tmp / "store")
        with_r, without_r = steamscan.cross_reference(installed, _steam_recipes)
        check("La Noire annotated as has_recipe=True",
              any(g["appid"] == "110800" and g.get("has_recipe")
                  for g in installed))
        check("Cyberpunk annotated as has_recipe=False",
              any(g["appid"] == "1091500" and not g["has_recipe"]
                  for g in installed))
        check("cross_reference counts add up",
              with_r + without_r == len(installed))
        # Merge into map — must not clobber the SD-games section
        from core import sdmap as _sdmap_mod
        existing_map = {"games": {"la-noire": {"path": "/x"}}}
        map_out = tmp / "combined_map.json"
        merged = _sdmap_mod.write_steam_section(installed, dest=map_out,
                                                 existing=existing_map)
        check("steam merge preserves sd games section",
              merged["games"]["la-noire"]["path"] == "/x"
              and "steam_games" in merged
              and "110800" in merged["steam_games"])

        print("== proton_version step (force compat tool) ==")
        # reuse the config.vdf with CompatToolMapping created in the reconcile
        # section; here we force a Proton on la-noire's steam appid 110800.
        pv_dir = tmp / "store" / "games" / "pv-game"
        (pv_dir / "payload").mkdir(parents=True)
        (pv_dir / "manifest.json").write_text(_json.dumps({
            "id": "pv-game", "name": "PV Game", "steam_appid": 110800,
            "steps": [{"type": "proton_version", "tool": "proton_63"}]}),
            encoding="utf-8")
        pv = [r for r in manifest.load_all(tmp / "store") if r.id == "pv-game"][0]
        pv_ctx = engine.Ctx(pv, game_dir, dry_run=False, log=quiet,
                            steam_root=steam)
        check("verify before: not applied",
              engine.verify_recipe(pv, pv_ctx) == engine.NOT_APPLIED)
        engine.apply_recipe(pv, pv_ctx)
        w = pv_ctx.deferred_vdf_writes[0]
        check("compat write queued", w["kind"] == "compat"
              and w["tool"] == "proton_63" and w["appid"] == 110800)
        changed = steamvdf.set_compat_tool(steam, w["appid"], w["tool"],
                                           w["priority"])
        check("compat tool written", changed
              and steamvdf.get_compat_tool(steam, 110800)["name"] == "proton_63")
        check("verify after: applied",
              engine.verify_recipe(pv, pv_ctx) == engine.APPLIED)
        check("idempotent second write",
              not steamvdf.set_compat_tool(steam, 110800, "proton_63"))
        # revert clears it back to default
        pv_ctx.deferred_vdf_writes.clear()
        engine.revert_recipe(pv, pv_ctx)
        rw = pv_ctx.deferred_vdf_writes[0]
        steamvdf.set_compat_tool(steam, rw["appid"], rw["tool"], rw["priority"])
        check("revert removes the mapping",
              steamvdf.get_compat_tool(steam, 110800) is None)

        print("== SD map: authoritative game path lookup ==")
        from core import sdmap as _sdmap
        # Fixture: SD card holding a sd_map.json that names La Noire
        real_pfx = tmp / "sd_scan" / "primary" / "Games" / "L.A. Noire"
        real_pfx.mkdir(parents=True)
        map_file = tmp / "sd_scan" / "primary" / "steamos_restore" / "game_fixes" / "sd_map.json"
        map_file.parent.mkdir(parents=True)
        map_file.write_text(_json.dumps({
            "games_dir": str(real_pfx.parent),
            "games": {"la-noire": {"path": str(real_pfx),
                                    "notes": "hand-verified"}}
        }), encoding="utf-8")
        # Redirect sd_card_roots to our fixture
        import os as _os2
        _os2.environ["GFM_SD_ROOTS"] = str(tmp / "sd_scan" / "primary")
        # Monkey-patch store.sd_card_roots to consult the env var for tests
        from core import store as _store
        original = _store.sd_card_roots
        _store.sd_card_roots = lambda: [Path(p) for p in _os2.environ.get(
            "GFM_SD_ROOTS", "").split(_os2.pathsep) if p and Path(p).is_dir()]
        try:
            found = _sdmap.get_game_path("la-noire")
            check("sd_map.json returns the mapped path", found == real_pfx)
            check("unmapped game returns None",
                  _sdmap.get_game_path("nonexistent-game") is None)
            # Detection now prefers the map over shortcut / library scans
            la_recipe = [r for r in manifest.load_all(tmp / "store")
                         if r.id == "la-noire"][0]
            check("detect.find_game_dir uses the map first",
                  detect.find_game_dir(la_recipe, steam, {}) == real_pfx)
            # And still respects a local remembered override
            override_path = tmp / "override"
            override_path.mkdir()
            check("machine config override still wins over the map",
                  detect.find_game_dir(la_recipe, steam,
                                        {"la-noire": str(override_path)})
                  == override_path)
        finally:
            _store.sd_card_roots = original
            del _os2.environ["GFM_SD_ROOTS"]

        print("== SD Games/ folder scan (auto-populate paths) ==")
        from core import sdscan
        sd_games = tmp / "sd_scan" / "Games"
        (sd_games / "L.A. Noire").mkdir(parents=True)
        (sd_games / "L.A. Noire" / "LaNoire.exe").write_bytes(b"EXE")
        (sd_games / "TheCrew").mkdir()  # aliased folder name
        (sd_games / "TheCrew" / "TheCrew.exe").write_bytes(b"EXE")
        (sd_games / "some other game").mkdir()  # unmatched
        recipes_all = manifest.load_all(tmp / "store")
        result = sdscan.scan(sd_games, recipes_all)
        matched_ids = {r.id for r, _, _ in result["matched"]}
        check("matched La Noire by exact name",
              "la-noire" in matched_ids)
        check("matched The Crew via alias 'TheCrew'",
              "crew-game" in matched_ids)
        check("unmatched folder surfaced, not silently absorbed",
              len(result["unmatched"]) == 1
              and result["unmatched"][0].name == "some other game")
        empty = sdscan.scan(tmp / "sd_scan" / "does_not_exist", recipes_all)
        check("missing games dir handled cleanly",
              empty == {"matched": [], "unmatched": []})

        print("== store mirror (incremental offline copy) ==")
        from core import store as store_mod
        mirror_dest = tmp / "sdcard" / "steamos_restore" / "game_fixes"
        c1, f1 = store_mod.mirror_store(tmp / "store", mirror_dest)
        check("first mirror copies everything", c1 > 0 and f1 == 0)
        check("payload arrived at mirror",
              (mirror_dest / "games" / "la-noire" / "payload" / "vpatch"
               / "dinput8.dll").is_file())
        c2, f2 = store_mod.mirror_store(tmp / "store", mirror_dest)
        check("second mirror copies nothing", c2 == 0 and f2 == c1)
        target = tmp / "store" / "games" / "la-noire" / "manifest.json"
        future = target.stat().st_mtime + 5  # mtime compare is whole-second
        _os.utime(target, (future, future))
        c3, _ = store_mod.mirror_store(tmp / "store", mirror_dest)
        check("touched file re-copied", c3 == 1)
        check("mirrored store is loadable",
              len(manifest.load_all(mirror_dest)) == len(manifest.load_all(tmp / "store")))

        # ---- game-folder saves (save_paths) -------------------------------
        # Saves next to the exe (The Crew's data.bin, Simpsons' Save1) are
        # covered by NO prefix backup, so capture/restore is the only thing
        # standing between a game-folder wipe and a lost save.
        print("== game-folder saves (capture/restore) ==")
        from core import saves as saves_mod
        sv_root = tmp / "saves_fixture"
        sv_recipe_dir = sv_root / "store" / "games" / "svtest"
        sv_recipe_dir.mkdir(parents=True)
        (sv_recipe_dir / "manifest.json").write_text(json.dumps({
            "id": "svtest", "name": "Save Test",
            "detect": {"marker_files": ["g.exe"]},
            "save_paths": ["{game_dir}/nested/data.bin", "{game_dir}/Save1",
                           "{game_dir}/PROF_SAVE_*", "{game_dir}/absent.bin"],
            "steps": [],
        }), encoding="utf-8")
        sv_recipe = manifest.load_recipe(sv_recipe_dir)
        check("save_paths parsed off the manifest", len(sv_recipe.save_paths) == 4)

        sv_game = sv_root / "game"
        (sv_game / "nested").mkdir(parents=True)
        (sv_game / "nested" / "data.bin").write_bytes(b"SAVE-V1")
        (sv_game / "Save1").mkdir()
        (sv_game / "Save1" / "slot.dat").write_bytes(b"SLOT")
        (sv_game / "PROF_SAVE_body").write_bytes(b"BODY")
        (sv_game / "PROF_SAVE_header").write_bytes(b"HEADER")
        sv_snap = sv_root / "snap"

        n_entries, n_files = saves_mod.capture(sv_recipe, sv_game, None, sv_snap,
                                               log=quiet)
        check("capture takes nested file + dir + glob, skips absent",
              n_entries == 3 and n_files == 4)
        check("capture writes a readable index",
              len(saves_mod.read_index(sv_snap)) == 3)

        # simulate a reimage / game-folder wipe
        (sv_game / "nested" / "data.bin").unlink()
        shutil.rmtree(sv_game / "Save1")
        (sv_game / "PROF_SAVE_body").unlink()
        (sv_game / "PROF_SAVE_header").unlink()
        restored = saves_mod.restore(sv_recipe, sv_game, None, sv_snap, log=quiet)
        check("restore puts every save back", restored == 4)
        check("restored nested file intact",
              (sv_game / "nested" / "data.bin").read_bytes() == b"SAVE-V1")
        check("restored dir intact",
              (sv_game / "Save1" / "slot.dat").read_bytes() == b"SLOT")
        check("restored glob matches intact",
              (sv_game / "PROF_SAVE_body").read_bytes() == b"BODY"
              and (sv_game / "PROF_SAVE_header").read_bytes() == b"HEADER")

        # a restore must never be what eats a newer live save
        (sv_game / "nested" / "data.bin").write_bytes(b"NEWER-LIVE")
        saves_mod.restore(sv_recipe, sv_game, None, sv_snap, log=quiet)
        check("live save set aside, not clobbered",
              (sv_game / "nested" / ("data.bin" + saves_mod.SAVE_BAK)).read_bytes()
              == b"NEWER-LIVE")
        check("no snapshot -> no entries, no crash",
              saves_mod.read_index(sv_root / "missing") == [])

        # Re-capturing after a save vanishes from the game folder must NOT
        # drop it from the index: the backup is still on disk, and losing the
        # index entry makes it unreachable to restore forever. This is the
        # reimage case exactly — deploy a fresh game folder, 🔍 Scan captures
        # every game, and a save you DID back up silently goes missing.
        shutil.rmtree(sv_game / "Save1")
        fresh_n, _ = saves_mod.capture(sv_recipe, sv_game, None, sv_snap,
                                       log=quiet)
        merged = saves_mod.read_index(sv_snap)
        check("re-capture keeps the backup of a now-missing save",
              any(e["template"] == "{game_dir}/Save1" for e in merged))
        check("re-capture reports only what it freshly captured", fresh_n == 2)
        (sv_game / "nested" / "data.bin").unlink()
        saves_mod.restore(sv_recipe, sv_game, None, sv_snap, log=quiet)
        check("the carried-forward backup still restores",
              (sv_game / "Save1" / "slot.dat").read_bytes() == b"SLOT")
        idx_before = (sv_snap / saves_mod.INDEX_NAME).read_text()
        saves_mod.capture(sv_recipe, tmp / "sv_nothing_here", None, sv_snap,
                          log=quiet)
        check("a capture that finds nothing leaves the index untouched",
              (sv_snap / saves_mod.INDEX_NAME).read_text() == idx_before)

        # steam_root is None when Steam isn't found. 🔍 Scan captures across
        # every recipe, so an unguarded `None / "userdata"` took the whole
        # scan down with a TypeError.
        print("== no-Steam guards ==")
        from core import steamart as art_mod
        art_src = tmp / "art_fixture"
        art_src.mkdir()
        (art_src / "123.png").write_bytes(b"PNG")
        try:
            cap_n = art_mod.capture(None, 123, tmp / "art_out")
            res_n = art_mod.restore(None, 123, art_src)
            crashed = False
        except Exception:
            cap_n = res_n = -1
            crashed = True
        check("steamart capture/restore survive steam_root=None",
              not crashed and cap_n == 0 and res_n == 0)

        # ---- prefix import (restore backed-up prefixes to compatdata) -----
        print("== prefix import (backup -> compatdata) ==")
        from core import prefiximport as pi
        pi_sd = tmp / "pi_sd"
        deck_bk = (pi_sd / "steamos_restore" / "prefix_backups"
                   / "The_Crew" / "3585568980")
        baz_bk = (pi_sd / "bazzite_restore" / "prefix backups"
                  / "Barnyard" / "3702111588")
        for b in (deck_bk, baz_bk):
            (b / "pfx" / "drive_c").mkdir(parents=True)
            (b / "pfx" / "drive_c" / "save.dat").write_bytes(b"PREFIX-SAVE")
        (pi_sd / "steamos_restore" / "prefix_backups" / "Husk" / "999").mkdir(
            parents=True)  # no pfx/ inside

        roots = pi.backup_roots([pi_sd])
        check("finds BOTH backup layouts (underscore + 'prefix backups')",
              len(roots) == 2)
        found = pi.list_backups(roots)
        check("lists every <name>/<appid> backup",
              {b.appid for b in found} == {"3585568980", "3702111588", "999"})
        check("husk backup (no pfx/) flagged",
              not next(b for b in found if b.appid == "999").has_pfx)
        check("real backup has pfx/",
              next(b for b in found if b.appid == "3585568980").has_pfx)
        check("target defaults to primary library compatdata",
              pi.target_dir(steam, "3585568980")
              == steam / "steamapps" / "compatdata" / "3585568980")
        check("no backups -> empty list, no crash",
              pi.list_backups([tmp / "pi_missing"]) == [])

        crew_bk = next(b for b in found if b.appid == "3585568980")
        check("not live before import", not pi.is_live(steam, "3585568980"))
        dst, n_files = pi.restore(crew_bk, steam, log=quiet)
        check("prefix restored into compatdata",
              (dst / "pfx" / "drive_c" / "save.dat").read_bytes() == b"PREFIX-SAVE"
              and n_files == 1)
        check("live after import", pi.is_live(steam, "3585568980"))

        # an import must never be what destroys a current prefix
        (dst / "pfx" / "LIVE").write_bytes(b"LIVE-PREFIX")
        pi.restore(crew_bk, steam, log=quiet)
        check("live prefix set aside, not destroyed",
              (dst.with_name(dst.name + pi.PREFIX_BAK) / "pfx" / "LIVE")
              .read_bytes() == b"LIVE-PREFIX")

        # A wine prefix holds dosdevices/z: -> / . Following that symlink would
        # copy the whole root filesystem into compatdata. Creating a symlink
        # needs elevation on Windows, so guard the FLAG rather than the OS
        # behaviour — this catches anyone quietly dropping symlinks=True.
        calls = []
        real_copytree = shutil.copytree

        def _spy(src, dest, *a, **kw):
            # copytree recurses into itself positionally; calls[0] is the
            # outer call restore() made, which is the one under test.
            calls.append(kw)
            return real_copytree(src, dest, *a, **kw)

        pi.shutil.copytree = _spy
        try:
            shutil.rmtree(dst, ignore_errors=True)
            pi.restore(crew_bk, steam, log=quiet)
        finally:
            pi.shutil.copytree = real_copytree
        check("restore copies with symlinks=True (z:-> / never followed)",
              bool(calls) and calls[0].get("symlinks") is True)

        # ---- deploy staged game from NAS -> SD ---------------------------
        print("== deploy game (NAS _games/ -> SD Games/) ==")
        from core import deploy as dep
        dp_root = tmp / "dep"
        dp_nas = dp_root / "nas"
        dp_sd = dp_root / "sd" / "Games"
        dp_sd.mkdir(parents=True)
        dp_game = dep.staged_root(dp_nas) / "Battlefield 3"
        (dp_game / "Data").mkdir(parents=True)
        (dp_game / "temp-dodi").mkdir()          # empty dir — must be carried
        (dp_game / "bf3.exe").write_bytes(b"E" * (5 << 20))  # > chunk size
        (dp_game / "Data" / "big.sb").write_bytes(b"D" * 2048)
        (dp_game / "bf3.par").write_bytes(b"")   # 0-byte file — must be carried

        staged = dep.list_staged(dp_nas)
        check("lists staged games by name",
              len(staged) == 1 and staged[0].name == "Battlefield 3")
        # Sizes come from a cached manifest so the menu can show them WITHOUT
        # walking every tree (that walk is the ~43s SMB cost we removed).
        check("no manifest -> size stays lazy (None)", staged[0].size is None)
        dep.save_size(dp_nas, dep.StagedGame("Battlefield 3", dp_game, 3, 4096))
        (dp_nas / dep.GAMES_DIR / "Fake").mkdir()  # a game not in the manifest
        staged2 = {g.name: g for g in dep.list_staged(dp_nas)}
        check("manifest size shown without a tree walk",
              staged2["Battlefield 3"].size == 4096
              and staged2["Battlefield 3"].files == 3)
        check("game absent from manifest -> size None (not a crash)",
              staged2["Fake"].size is None)
        check(".gfm-sizes.json is not itself listed as a game",
              not any(g.name.startswith(".") for g in staged2.values()))
        # Drawing the menu must NOT walk every game: over SMB that's a
        # round-trip per file, and with ~32k staged files it took 43s to draw
        # a list of names (and plan() then walked them all again, both sides).
        # Names are cheap; size/count are measured for the picked game only.
        check("list_staged is lazy — no measuring (keeps the menu instant)",
              staged[0].files is None and staged[0].size is None)
        check("measure() fills size + count on demand",
              dep.measure(staged[0]).files == 3 and staged[0].size is not None)
        check("measure() is idempotent",
              dep.measure(staged[0]).files == 3)
        check("no _games dir -> empty list, no crash",
              dep.list_staged(dp_root / "absent") == [])
        # A dropped automount looks like an EMPTY folder, not an error — so the
        # deploy screen must tell "NAS down" from "nothing staged" (it read as
        # "nothing staged", which is what made the menu look empty).
        check("mount_reachable: populated share -> up", dep.mount_reachable(dp_nas))
        check("mount_reachable: missing path -> down",
              not dep.mount_reachable(dp_root / "gone"))
        _empty = dp_root / "empty_mountpoint"
        _empty.mkdir()
        check("mount_reachable: empty mountpoint -> down (dropped automount)",
              not dep.mount_reachable(_empty))
        # The menu marks what's already on the card, but must do it with ONE
        # stat per game — not by re-walking trees (that's what made it slow).
        check("is_deployed: false before the copy",
              not dep.is_deployed(staged[0], dp_sd))

        ticks = []
        stats = dep.deploy(staged[0], dp_sd,
                           progress=lambda d, t, r: ticks.append(d))
        dp_dst = dp_sd / "Battlefield 3"
        check("deploy copies every file",
              stats["copied"] == 3
              and (dp_dst / "bf3.exe").stat().st_size == (5 << 20))
        check("empty directory carried across (temp-dodi)",
              (dp_dst / "temp-dodi").is_dir())
        check("0-byte file carried across (bf3.par)",
              (dp_dst / "bf3.par").is_file()
              and (dp_dst / "bf3.par").stat().st_size == 0)
        check("no .gfm-part temp files left behind",
              not list(dp_dst.rglob("*.gfm-part")))
        check("progress reported during copy", len(ticks) >= 1)

        check("is_deployed: true once it's on the card",
              dep.is_deployed(staged[0], dp_sd))
        # Deploy auto-creates shortcuts: the SETUP steps (make it appear in
        # Steam + run) fire on deploy; everything else is left for Apply Fixes.
        import inspect as _insp
        import gfm as _gfm
        check("deploy setup-steps = runner/proton/shortcut only",
              set(_gfm.App.SETUP_STEP_TYPES)
              == {"install_runner", "proton_version", "steam_shortcut"})
        _dsrc = _insp.getsource(_gfm.App.cmd_deploy_game)
        check("deploy multi-selects games", "multi=True" in _dsrc)
        check("deploy auto-runs shortcut setup + one Steam bounce",
              "_setup_shortcut" in _dsrc and "flush_vdf_writes" in _dsrc)
        check("deploy latches shortcut_seen (arms reclaim without a prior run)",
              "shortcut_seen" in _dsrc)
        # The weekly timer runs unattended — a prompt on that path would hang
        # the job forever waiting on stdin that never comes.
        for _fn in ("cmd_reclaim", "cmd_backup_prefixes"):
            _s = _insp.getsource(getattr(_gfm.App, _fn))
            # the unattended guard is named `auto` in one, `unattended` in the
            # other — take whichever appears first
            _guards = [_s.index(g) for g in ("if auto:", "if unattended:")
                       if g in _s]
            check(f"{_fn} has an unattended guard", bool(_guards))
            _ia = min(_guards)
            _prompts = [c for c in ("self.ui.input(", "self.ui.choose(",
                                    "self.ui.confirm(", "_pick_reclaim(")
                        if c in _s]
            check(f"{_fn}: every prompt is after the auto guard",
                  all(_s.index(c) > _ia for c in _prompts))
        # The unattended refresh must write the SAME map sections the menu
        # Scan does — games + steam_games + prefix_backups — or the weekly run
        # silently leaves part of the map stale.
        _rm = _insp.getsource(_gfm.App._refresh_map)
        check("weekly map refresh covers SD games, Steam and prefix backups",
              "sdscan.scan" in _rm and "steamscan.scan" in _rm
              and "_scan_prefix_backups" in _rm)
        check("prefix-backup inventory never prompts (runs unattended)",
              not any(p in _insp.getsource(_gfm.App._scan_prefix_backups)
                      for p in ("self.ui.input(", "self.ui.choose(",
                                "self.ui.confirm(")))
        check("capture/map refresh never prompt (used by the weekly timer)",
              not any(p in _insp.getsource(getattr(_gfm.App, f))
                      for f in ("_capture_all", "_refresh_map")
                      for p in ("self.ui.input(", "self.ui.choose(",
                                "self.ui.confirm(")))

        check("deploy makes a generic shortcut for recipe-less games",
              hasattr(_gfm.App, "_make_generic_shortcut")
              and "_make_generic_shortcut" in _insp.getsource(
                  _gfm.App._setup_shortcut))

        again = dep.deploy(staged[0], dp_sd)
        check("re-deploy is a no-op (resume skips identical files)",
              again["copied"] == 0 and again["skipped"] == 3)

        (dp_dst / "Data" / "big.sb").unlink()
        todo, need, ok_n = dep.plan(staged[0], dp_sd)
        check("plan re-copies only what's missing",
              len(todo) == 1 and ok_n == 2 and need == 2048)
        check("free space reports something", dep.free_space(dp_sd) > 0)

        # ---- reclaim SD space (the one place the tool DELETES) -------------
        # Every rule here is a safety rule; assert each guard holds.
        print("== reclaim (uninstall removed deployed games) ==")
        from core import reclaim as rc
        from core import shortcutsvdf as sv
        rc_root = tmp / "rc"
        rc_steam = rc_root / "steam"
        (rc_steam / "userdata" / "1" / "config").mkdir(parents=True)
        rc_sd = rc_root / "sd" / "Games"
        rc_sd.mkdir(parents=True)
        rc_nas = rc_root / "nas"
        (dep.staged_root(rc_nas)).mkdir(parents=True)

        def rc_recipe(rid, name, sp=None):
            return manifest.Recipe(
                id=rid, name=name, aliases=[], steam_appid=None, detect={},
                steps=[{"type": "steam_shortcut", "exe": "g.exe"}], notes="",
                post_apply_message="", remote_payloads=[], requires_game=True,
                save_paths=sp or [], dir=rc_root)

        def rc_game(name, gb, on_nas=True):
            d = rc_sd / name
            d.mkdir(parents=True)
            with open(d / "data.bin", "wb") as f:
                f.seek(int(gb * (1 << 30)) - 1)
                f.write(b"\0")
            if on_nas:
                (dep.staged_root(rc_nas) / name).mkdir(parents=True)

        def rc_add_sc(name):
            sv.ensure_shortcut(rc_steam, name, str(rc_sd / name / "g.exe"),
                               str(rc_sd / name), "", 999000, [])

        def rc_clear():
            for f in sv._shortcut_files(rc_steam):
                f.write_bytes(sv.dumps({"shortcuts": (sv.TYPE_MAP, {})}))

        def rc_scan(recipes, deployed):
            return rc.scan(recipes, rc_steam, deployed, [rc_sd], rc_nas)

        big = rc_recipe("big", "Big"); rc_game("Big", 40); rc_add_sc("Big")
        r = rc_scan([big], {"Big": {}})
        check("reclaim: shortcut present -> latch shortcut_seen, no candidate",
              not r.candidates and r.deployed["Big"]["shortcut_seen"] is True)
        rc_clear()
        r = rc_scan([big], r.deployed)
        check("reclaim: seen-then-removed + big -> candidate",
              len(r.candidates) == 1 and r.candidates[0].name == "Big")
        check("reclaim: never-applied (deploy->apply gap) is safe",
              not rc_scan([big], {"Big": {}}).candidates)
        sm = rc_recipe("sm", "Small"); rc_game("Small", 5)
        check("reclaim: under the size floor -> kept",
              not rc_scan([sm], {"Small": {"shortcut_seen": True}}).candidates)
        orp = rc_recipe("orp", "Orphan"); rc_game("Orphan", 40, on_nas=False)
        check("reclaim: not staged on NAS -> refuse (not reversible)",
              not rc_scan([orp], {"Orphan": {"shortcut_seen": True}}).candidates)
        cg = rc_recipe("cg", "CrewBig", sp=["{game_dir}/data.bin"])
        rc_game("CrewBig", 40)
        check("reclaim: save_paths not captured -> refuse",
              not rc_scan([cg], {"CrewBig": {"shortcut_seen": True}}).candidates)
        (rc_nas / "cg" / "saves" / "0").mkdir(parents=True)
        (rc_nas / "cg" / "saves" / "0" / "data.bin").write_bytes(b"S")
        (rc_nas / "cg" / "saves" / "index.json").write_text(
            '{"recipe":"cg","entries":[{"slot":0,"template":"{game_dir}/'
            'data.bin","names":["data.bin"]}]}')
        check("reclaim: save_paths captured -> candidate",
              len(rc_scan([cg], {"CrewBig": {"shortcut_seen": True}})
                  .candidates) == 1)
        check("reclaim: no Steam root -> blocked, nothing reclaimed",
              rc.scan([big], None, {"Big": {"shortcut_seen": True}}, [rc_sd],
                      rc_nas).blocked)
        # A deployed game with NO recipe (a generic shortcut made on deploy) is
        # still tracked — by its folder name, which is the shortcut's AppName.
        rc_game("NoRecipe", 40)
        sv.ensure_shortcut(rc_steam, "NoRecipe",
                           str(rc_sd / "NoRecipe" / "g.exe"),
                           str(rc_sd / "NoRecipe"), "", 2000000002, [])
        r = rc.scan([], rc_steam, {"NoRecipe": {"shortcut_seen": True}},
                    [rc_sd], rc_nas)
        check("reclaim: recipe-less game with a shortcut -> kept (tracked)",
              not r.candidates and "still in Steam" in r.considered[0][1])
        rc_clear()
        r = rc.scan([], rc_steam, {"NoRecipe": {"shortcut_seen": True}},
                    [rc_sd], rc_nas)
        check("reclaim: recipe-less game, shortcut deleted -> removable",
              len(r.candidates) == 1 and r.candidates[0].name == "NoRecipe")
        rc_clear()
        r = rc_scan([big], {"Big": {"shortcut_seen": True}})
        freed = rc.uninstall(r.candidates[0], r.deployed)
        check("reclaim: uninstall frees the SD folder but KEEPS the NAS copy",
              freed > 0 and not (rc_sd / "Big").exists()
              and (dep.staged_root(rc_nas) / "Big").is_dir()
              and "Big" not in r.deployed)

        # ---- per-game settings capture/restore (Gamescope block) ----------
        # localconfig.vdf keeps the Deck's per-game framerate/tearing/frame-
        # limit under "Gamescope", keyed by appid. Restore must be SURGICAL:
        # merge only those back into a fresh localconfig, touch nothing else.
        print("== per-game settings (Gamescope restore) ==")
        from core import steamperf as sp
        snap_tree = {"UserLocalConfigStore": {
            "friends": {"1": {"name": "keep me"}},
            "Gamescope": {
                "AppTargetFrameRate": {"111": "60", "222": "144"},
                "AllowTearing": {"111": "0"},
                "DisableFrameLimit": {"222": "1"}}}}
        saved = sp.extract(snap_tree)
        check("extract pulls the per-appid Gamescope maps",
              saved["AppTargetFrameRate"] == {"111": "60", "222": "144"}
              and saved["AllowTearing"] == {"111": "0"})
        # a fresh post-reimage localconfig with OTHER data but no Gamescope
        fresh = {"UserLocalConfigStore": {"friends": {"9": {"name": "fresh"}}}}
        n = sp.restore_into(fresh, saved)
        check("restore merges every saved value", n == 4)
        check("restore reproduces the settings exactly",
              sp.extract(fresh) == saved)
        check("restore is surgical — untouched keys survive",
              steamvdf._child_ci(sp._store(fresh), "friends")
              == {"9": {"name": "fresh"}})
        n2 = sp.restore_into(fresh, saved, only_appids={"111"})
        check("only_appids restores just the named game",
              sum(1 for m in sp.extract(fresh).values() for a in m if a == "222")
              >= 1 and n2 == 2)  # 111 in AppTargetFrameRate + AllowTearing
        check("empty snapshot -> nothing extracted",
              sp.extract({"UserLocalConfigStore": {}}) == {})

        # ---- Decky settings (per-game TDP/GPU/power) — device-keyed ---------
        # TDP lives in ~/homebrew/settings/<Plugin>/, NOT localconfig, and is
        # device-specific, so backups are keyed by hostname and MUST NOT
        # cross-restore (a Deck's 15W is nonsense on a Legion Go).
        print("== Decky settings (device-keyed TDP backup) ==")
        from core import deckysettings as ds
        dk = tmp / "decky"
        dhome = dk / "deck_home"
        (dhome / "homebrew" / "settings" / "SimpleDeckyTDP").mkdir(parents=True)
        (dhome / "homebrew" / "settings" / "SimpleDeckyTDP" / "s.json"
         ).write_text('{"tdp":15}')
        dstate = dk / "_state"
        check("capture backs up the settings tree",
              ds.capture(dhome, dstate, host="steamdeck", log=quiet) == 1)
        lhome = dk / "legion_home"
        (lhome / "homebrew" / "settings" / "X").mkdir(parents=True)
        (lhome / "homebrew" / "settings" / "X" / "s.json").write_text('{"tdp":30}')
        ds.capture(lhome, dstate, host="legion-go-2", log=quiet)
        check("both device backups listed",
              set(ds.hosts_available(dstate)) == {"steamdeck", "legion-go-2"})
        shutil.rmtree(dhome / "homebrew")
        ds.restore(dstate, dhome, host="steamdeck", log=quiet)
        _got = (dhome / "homebrew" / "settings" / "SimpleDeckyTDP" / "s.json"
                ).read_text()
        check("restore is device-keyed (deck gets 15W, never legion's 30W)",
              '"tdp":15' in _got and '"tdp":30' not in _got)
        check("restore for an unknown host is a safe no-op",
              ds.restore(dstate, dhome, host="mystery-pc", log=quiet) == 0)

        # ---- prefix BACKUP (retires the old Linux Prefix Manager) ----------
        # Writes the exact layout prefiximport reads, so backup+restore pair up.
        print("== prefix backup (compatdata -> SD) ==")
        from core import prefixbackup as pbk
        check("safe_name matches the old tool's sanitiser",
              pbk.safe_name("Tom Clancy's H.A.W.X") == "Tom_Clancy_s_H.A.W.X"
              and pbk.safe_name("Project CARS 3") == "Project_CARS_3")
        _pb = tmp / "pbk"
        _mk = lambda n, cloud, steam=True, aid="1": pbk.PrefixInfo(
            appid=aid, name=n, path=_pb, is_steam=steam, has_cloud=cloud)
        _all = [_mk("The Crew", False, False, "10"), _mk("Driver SF", False, True, "11"),
                _mk("Halo MCC", True, True, "12"), _mk("Project CARS 3", True, True, "13")]
        check("cloud games hidden from the default list",
              {p.name for p in pbk.candidates(_all, set())}
              == {"The Crew", "Driver SF"})
        check("show_cloud reveals them for picking",
              len(pbk.candidates(_all, set(), show_cloud=True)) == 4)
        check("an opted-in cloud game graduates to the main list",
              {p.name for p in pbk.candidates(_all, {"13"})}
              == {"The Crew", "Driver SF", "Project CARS 3"})

        _src = tmp / "compat" / "4242"
        (_src / "pfx" / "drive_c" / "users" / "steamuser" / "Documents").mkdir(parents=True)
        (_src / "pfx" / "drive_c" / "users" / "steamuser" / "Documents" / "save.dat"
         ).write_bytes(b"SAVE" * 64)
        (_src / "pfx" / "drive_c" / "users" / "steamuser" / "Temp").mkdir()
        (_src / "pfx" / "drive_c" / "users" / "steamuser" / "Temp" / "j.tmp"
         ).write_bytes(b"J" * 4096)
        _info = pbk.PrefixInfo(appid="4242", name="Test Game", path=_src,
                               is_steam=False, has_cloud=False)
        _dest = tmp / "pbk_sd" / "steamos_restore" / "prefix_backups"
        _st = pbk.backup(_info, _dest, log=quiet)
        _out = _dest / "Test_Game" / "4242"
        check("backup copies the prefix contents",
              (_out / "pfx" / "drive_c" / "users" / "steamuser" / "Documents"
               / "save.dat").is_file())
        check("Temp junk is skipped",
              not (_out / "pfx" / "drive_c" / "users" / "steamuser" / "Temp").exists())
        _st2 = pbk.backup(_info, _dest, log=quiet)
        check("re-backup is incremental (nothing re-copied)",
              _st2["copied"] == 0 and _st2["skipped"] > 0)
        # The whole point: what backup writes, import can read.
        from core import prefiximport as _pi
        _found = _pi.list_backups([_dest])
        check("prefiximport reads back what prefixbackup wrote",
              len(_found) == 1 and _found[0].appid == "4242"
              and _found[0].has_pfx)

        # Inventory: old-tool backups dragged in are IDENTIFIED, not just found.
        _inv_root = tmp / "inv" / "steamos_restore" / "prefix_backups"
        for _sn, _aid in (("The_Crew", "3585568980"), ("Barnyard", "3702111588"),
                          ("Some_Old_Game", "999888777")):
            (_inv_root / _sn / _aid / "pfx" / "drive_c").mkdir(parents=True)
            (_inv_root / _sn / _aid / "pfx" / "drive_c" / "f.dat"
             ).write_bytes(b"x" * 512)
        (_inv_root / "Empty_One" / "111").mkdir(parents=True)   # no pfx/
        _recs = [manifest.Recipe(id="the-crew", name="The Crew", aliases=[],
                                 steam_appid=None, detect={}, steps=[], notes="",
                                 post_apply_message="", remote_payloads=[],
                                 requires_game=True, save_paths=[], dir=tmp),
                 manifest.Recipe(id="barnyard", name="Barnyard", aliases=[],
                                 steam_appid=None, detect={}, steps=[], notes="",
                                 post_apply_message="", remote_payloads=[],
                                 requires_game=True, save_paths=[], dir=tmp)]
        _reg = {"3585568980": {"appid": 3585568980, "name": "The Crew",
                               "recipe_id": "the-crew"}}
        _inv = {e["safe_name"]: e for e in
                pbk.inventory(_recs, _reg, roots=[_inv_root])}
        check("inventory matches a backup by pinned gospel appid",
              _inv["The_Crew"]["matched_by"] == "registry"
              and _inv["The_Crew"]["recipe_id"] == "the-crew")
        check("inventory matches a backup by recipe name",
              _inv["Barnyard"]["matched_by"] == "recipe")
        check("unrecognised backup is flagged, not dropped",
              _inv["Some_Old_Game"]["matched_by"] == "unknown")
        check("backup with no pfx/ is flagged empty",
              _inv["Empty_One"]["has_pfx"] is False)
        _mp = tmp / "inv_map" / "sd_map.json"
        from core import sdmap as _sdm
        _sdm.write_prefix_backups_section(list(_inv.values()), _mp,
                                          {"games": {"keep": "me"}})
        _read = json.loads(_mp.read_text(encoding="utf-8"))
        check("inventory written to the map without clobbering other sections",
              _read["games"] == {"keep": "me"}
              and len(_read["prefix_backups"]) == 4)

        # ---- CLI wiring ---------------------------------------------------
        # A command in the parser's choices but missing a handler used to fall
        # through to the interactive menu — the command would silently "do
        # nothing" instead of erroring. Both now come from one dict; assert
        # every entry resolves to a real App method.
        print("== CLI wiring ==")
        import inspect as _insp
        import gfm as gfm_mod
        missing = [name for name in gfm_mod.COMMANDS if not callable(
            gfm_mod.COMMANDS[name])]
        check("every CLI command has a callable handler", not missing)
        # callable() alone is too weak — a wrong-arity lambda passes it and
        # then blows up at runtime. main() calls handler(app, args).
        wrong = [n for n, fn in gfm_mod.COMMANDS.items()
                 if len(_insp.signature(fn).parameters) != 2]
        check("every CLI handler takes exactly (app, args)", not wrong)
        # ...and that each lambda actually names a method App has.
        _src = _insp.getsource(gfm_mod).split("COMMANDS = {")[1].split("\n}")[0]
        unknown = sorted({m for m in re.findall(r"app\.(\w+)\(", _src)
                          if not hasattr(gfm_mod.App, m)})
        check("every CLI handler targets a real App method", not unknown)
        # "Back up with last selection" — skips the picker so a manual re-run
        # doesn't mean re-ticking everything; --auto is that plus no prompts.
        _bp = _insp.getsource(_gfm.App.cmd_backup_prefixes)
        check("--auto implies use_saved (timer reuses the remembered set)",
              "use_saved = use_saved or unattended" in _bp)
        check("empty remembered set reports instead of silently doing nothing",
              "Nothing selected yet" in _bp)
        check("🔁 wrapper delegates with use_saved=True",
              "use_saved=True" in _insp.getsource(
                  _gfm.App.cmd_backup_prefixes_saved))

        for expected in ("scan", "save-restore", "deploy", "import-prefixes",
                         "restore-saves", "restore-settings", "scan-sd",
                         "scan-steam", "reclaim", "setup-reclaim-timer",
                         "backup-prefixes", "backup-prefixes-now"):
            check(f"CLI exposes '{expected}'", expected in gfm_mod.COMMANDS)
        methods = ("cmd_scan_all", "cmd_save_restore", "cmd_deploy_game",
                   "cmd_import_prefixes", "cmd_restore_saves", "cmd_capture",
                   "menu_advanced", "menu_settings", "_pick_many")
        check("bundle + submenu methods exist on App",
              all(hasattr(gfm_mod.App, m) for m in methods))
        # Apply/Revert must use the MULTI picker: _pick_one silently allowed
        # only one game while the prompt claimed "A = select, then Done".
        import inspect
        for fn in ("cmd_apply", "cmd_revert"):
            src = inspect.getsource(getattr(gfm_mod.App, fn))
            check(f"{fn} uses the multi-select picker",
                  "_pick_many" in src and "_pick_one" not in src)

        # --- step-type drift ------------------------------------------
        # KNOWN_STEP_TYPES gates recipes at LOAD time; the engine registry
        # is what actually runs them. A name in one but not the other means
        # either a recipe dies mid-apply (declared, unimplemented — this is
        # what "run_script" did) or a valid step is rejected at startup.
        from core import engine as _eng, manifest as _man
        declared, implemented = _man.KNOWN_STEP_TYPES, set(_eng._REGISTRY)
        check("no step type declared without an implementation",
              not (declared - implemented))
        check("no step implemented without being declared",
              not (implemented - declared))

        # --- pitcrew_compile ------------------------------------------
        from core.steps import pitcrew_compile as _pc
        mdata = tmp / "Test.mdata"
        mdata.write_text(
            '<metadata><files>'
            '<file priority="998" loc="Test_entities.xml" />'
            '<file priority="500" loc="Test_data" />'
            '</files></metadata>', encoding="utf-8")
        mod_id, entries = _pc._mod_entries(mdata)
        check("pitcrew mod id comes from the .mdata filename", mod_id == "Test")
        check("pitcrew reads file entries with priorities",
              entries == [("998", "Test_entities.xml"), ("500", "Test_data")])
        xml = _pc._build_manifest(mod_id, entries, 5)
        check("pitcrew manifest declares packageversion 5 for The Crew",
              'packageversion="5"' in xml)
        check("pitcrew manifest rewrites loc into mods/",
              'loc="mods/Test_entities.xml"' in xml
              and 'loc="mods/Test_data"' in xml)
        check("pitcrew manifest preserves per-file priority",
              'priority="500"' in xml and 'priority="998"' in xml)
        # Case-insensitive nested lookup: the game ships "Data_Win32" but
        # casing varies by repack and ext4 is case-sensitive.
        (tmp / "the crew" / "data_win32").mkdir(parents=True, exist_ok=True)
        found = _pc._find_data_dir(tmp, "the crew/Data_Win32")
        # Compare identity, not spelling: on a case-INSENSITIVE filesystem
        # the fast path returns the casing we asked for, on ext4 it returns
        # the casing on disk. Both must land on the same directory.
        check("pitcrew finds a nested data dir despite case",
              found is not None
              and found.samefile(tmp / "the crew" / "data_win32"))
        check("pitcrew returns None-path error for a missing data dir",
              _pc._resolve_ci(tmp, "nope/Data_Win32") is None)

        print(f"\nAll {PASS} checks passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
