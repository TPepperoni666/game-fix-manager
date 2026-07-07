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
        with_r, without_r = steamscan.cross_reference(installed, recipes_all)
        check("La Noire annotated as has_recipe=True",
              any(g["appid"] == "110800" and g.get("has_recipe")
                  for g in installed))
        check("Cyberpunk annotated as has_recipe=False",
              any(g["appid"] == "1091500" and not g["has_recipe"]
                  for g in installed))
        check("cross_reference counts add up",
              with_r + without_r == len(installed))
        # Merge into map — must not clobber the SD-games section
        existing_map = {"games": {"la-noire": {"path": "/x"}}}
        map_out = tmp / "combined_map.json"
        merged = sdmap.write_steam_section(installed, dest=map_out,
                                            existing=existing_map)
        check("steam merge preserves sd games section",
              merged["games"]["la-noire"]["path"] == "/x"
              and "steam_games" in merged
              and "110800" in merged["steam_games"])

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

        print(f"\nAll {PASS} checks passed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
