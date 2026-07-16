#!/usr/bin/env python3
"""Game Fix Manager — re-apply game mods/fixes after a SteamOS reimage.

Usage:
  gfm.py                          interactive menu
  gfm.py list                     recipes + detection + applied status
  gfm.py apply [id ...]           apply fixes (all detected games if no ids)
  gfm.py revert <id>              undo a game's fixes
  gfm.py --dry-run apply ...      show what would happen, touch nothing

Options: --store PATH, --steam-root PATH, --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from core import (deploy, detect, engine, fetch, manifest, prefiximport,
                  prefixes, saves, sdmap, sdscan, shortcutsvdf, steamart,
                  steamscan, steamvdf, store)
from ui import get_ui

STATUS_ICON = {engine.APPLIED: "✅", engine.NOT_APPLIED: "☐ ",
               engine.PARTIAL: "🟡", "not_found": "❓"}
STATUS_TEXT = {engine.APPLIED: "applied", engine.NOT_APPLIED: "not applied",
               engine.PARTIAL: "PARTIAL — some steps missing", "not_found": "game not found"}


class App:
    def __init__(self, args):
        self.args = args
        self.ui = get_ui()
        self._open_log()          # before anything that might log
        self.cfg = store.load_config()
        self.store_root = store.resolve_store(args.store, self.cfg)
        self.steam_root = detect.find_steam_root(args.steam_root)
        self.local_payloads = store.resolve_local_payloads(
            getattr(args, "local_payloads", None), self.cfg)
        self.pending_vdf_writes: list = []
        self.recipes = manifest.load_all(self.store_root) if self.store_root else []
        self.log(f"start: cmd={getattr(args, 'command', None)} "
                 f"store={self.store_root} steam={self.steam_root} "
                 f"local_payloads={self.local_payloads} "
                 f"recipes={len(self.recipes)}")

    # --- logging (writes next to the map so it Syncthing-mirrors) ---

    def _open_log(self):
        self._logf = None
        try:
            self._logf = open(sdmap.log_path(), "a", encoding="utf-8",
                              errors="replace")
        except Exception:
            return
        self.log("=" * 60)
        # Tee every UI message into the log too
        _orig = self.ui.msg

        def teed(text, style="info"):
            self.log(f"[{style}] {text}")
            _orig(text, style)
        self.ui.msg = teed

    def log(self, text: str):
        if getattr(self, "_logf", None):
            try:
                self._logf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
                self._logf.flush()
            except Exception:
                pass

    # --- shared helpers ---

    def game_dir_for(self, recipe, interactive: bool) -> Path | None:
        if not recipe.requires_game:
            return Path.home()  # tool recipe: {game_dir} means the home dir
        remembered = self.cfg.get("game_paths", {})
        found = detect.find_game_dir(recipe, self.steam_root, remembered)
        if found or not interactive:
            return found
        self.ui.msg(f"Could not auto-locate '{recipe.name}'.", "warn")
        raw = self.ui.input(f"Path to {recipe.name} install dir (blank to skip)")
        if not raw or not Path(raw).is_dir():
            return None
        self.cfg.setdefault("game_paths", {})[recipe.id] = raw
        store.save_config(self.cfg)
        return Path(raw)

    def status_line(self, recipe, game_dir) -> str:
        if game_dir is None:
            status = "not_found"
        else:
            ctx = engine.Ctx(recipe, game_dir, dry_run=True, log=lambda _: None,
                             steam_root=self.steam_root,
                             local_payloads_dir=self.local_payloads)
            try:
                status = engine.verify_recipe(recipe, ctx)
            except engine.StepError as e:
                if recipe.remote_payloads:
                    return f"☐  {recipe.name} — not applied (payload downloads on apply)"
                return f"❓ {recipe.name} — payload problem: {e}"
        return f"{STATUS_ICON[status]} {recipe.name} — {STATUS_TEXT[status]}"

    def run_engine(self, recipe, game_dir, action) -> bool:
        ctx = engine.Ctx(recipe, game_dir, dry_run=self.args.dry_run,
                         log=lambda m: self.ui.msg(m, "dim"),
                         steam_root=self.steam_root,
                         local_payloads_dir=self.local_payloads)
        label = "DRY RUN " if self.args.dry_run else ""
        self.ui.msg(f"{label}{action.__name__.split('_')[0]}: {recipe.name} -> {game_dir}")
        try:
            action(recipe, ctx)
        except engine.StepError as e:
            self.ui.msg(f"{recipe.name}: {e}", "error")
            return False
        self.pending_vdf_writes.extend(ctx.deferred_vdf_writes)
        self.ui.msg(f"{recipe.name} done.", "success")
        return True

    def flush_vdf_writes(self):
        """Apply queued launch-option writes with one Steam bounce for all."""
        writes = self.pending_vdf_writes
        if not writes:
            return
        self.pending_vdf_writes = []
        names = ", ".join(sorted({w["game"] for w in writes}))
        self.ui.msg(f"Steam config changes queued for: {names}", "warn")
        was_running = steamvdf.steam_running()
        if was_running:
            if not self.ui.confirm(
                    "Steam must close briefly to write these changes "
                    "(controller drops out until it's back). Do it now?"):
                self.ui.msg("Skipped — re-run apply later to write them.", "warn")
                return
            steamvdf.close_steam(lambda m: self.ui.msg(m, "warn"))
        total = 0
        for w in writes:
            kind = w.get("kind")
            if kind == "compat":
                changed = steamvdf.set_compat_tool(
                    self.steam_root, w["appid"], w["tool"], w["priority"])
                self.ui.msg(f'  {w["game"]}: Proton = '
                            f'{w["tool"] or "(default)"}'
                            f'{"" if changed else " (already set)"}', "dim")
                total += int(changed)
            elif kind == "add_shortcut":
                n = shortcutsvdf.ensure_shortcut(
                    self.steam_root, w["appname"], w["exe"], w["start_dir"],
                    w.get("launch_options", ""), w.get("appid"),
                    w.get("aliases"))
                self.ui.msg(f'  {w["game"]}: Steam shortcut written '
                            f'({n} file(s))', "dim")
                total += n
            elif kind == "restore_art":
                n = steamart.restore(self.steam_root, w["appid"], w["src"])
                self.ui.msg(f'  {w["game"]}: shortcut art restored '
                            f'({n} file(s))', "dim")
                total += n
            elif kind == "shortcut":
                n = shortcutsvdf.set_launch_options(self.steam_root,
                                                    w["names"], w["value"])
                self.ui.msg(f'  {w["game"]}: LaunchOptions = '
                            f'{w["value"] or "(cleared)"} ({n} file(s))', "dim")
                total += n
            else:
                n = steamvdf.set_launch_options(self.steam_root,
                                                w["appid"], w["value"])
                self.ui.msg(f'  {w["game"]}: LaunchOptions = '
                            f'{w["value"] or "(cleared)"} ({n} file(s))', "dim")
                total += n
        if was_running:
            steamvdf.start_steam(lambda m: self.ui.msg(m, "warn"))
        self.ui.msg(f"Steam config written ({total} change(s)).", "success")

    # --- commands ---

    def _gospel_appid(self, recipe):
        """The pinned non-Steam appid for a recipe, from prefix_registry.json."""
        reg = self.store_root / "prefix_registry.json"
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for e in data.get("entries", []):
            if e.get("recipe_id") == recipe.id and e.get("appid") is not None:
                return int(e["appid"])
        return None

    def _capture_one(self, recipe, interactive: bool = True) -> bool:
        """Art + game-folder saves for one recipe. Returns True if anything
        was captured. interactive=False in bulk runs — otherwise a sweep over
        32 recipes would stop and ask for a path on every undetected game."""
        got = False
        appid = self._gospel_appid(recipe)
        if appid is None:
            if interactive:
                self.ui.msg(f"No gospel appid for {recipe.name} — skipping art "
                            "(nothing to key it by).", "warn")
        else:
            dest = self.local_payloads / recipe.id / "artwork"
            n = steamart.capture(self.steam_root, appid, dest)
            if n:
                self.ui.msg(f"  🎨 {recipe.name}: {n} art file(s)", "success")
                got = True
            elif interactive:
                self.ui.msg(f"No custom art found for {recipe.name} (appid "
                            f"{appid}) — set it in Steam first, then capture.",
                            "warn")
        return self._capture_saves(recipe, interactive=interactive) or got

    def _snapshot_localconfig(self) -> int:
        """Snapshot localconfig.vdf — the source of per-game display/perf
        settings (TDP, scaling, VRR, framerate). Kept whole for now so the
        surgical per-appid restore can be built against a real file."""
        state = self.local_payloads / "_state"
        snapped = 0
        for cfg in steamvdf._localconfigs(self.steam_root):
            uid = cfg.parent.parent.name
            try:
                state.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cfg, state / f"localconfig-{uid}.vdf")
                snapped += 1
            except OSError as e:
                self.ui.msg(f"  localconfig snapshot failed: {e}", "warn")
        return snapped

    def _capture_all(self) -> None:
        """Capture art + saves for every DETECTED game, then the settings
        snapshot. No prompting — undetected games are skipped silently."""
        if self.local_payloads is None:
            self.ui.msg("No local-payloads dir (NAS/SD) set to capture into.",
                        "warn")
            return
        hits = 0
        for recipe in self.recipes:
            if recipe.requires_game and \
                    self.game_dir_for(recipe, interactive=False) is None:
                continue
            if self._capture_one(recipe, interactive=False):
                hits += 1
        self.ui.msg(f"Captured art/saves for {hits} game(s).",
                    "success" if hits else "dim")
        snapped = self._snapshot_localconfig()
        if snapped:
            self.ui.msg(f"Snapshotted localconfig.vdf for {snapped} user(s) "
                        "(perf/display source).", "dim")

    def cmd_capture(self):
        """Snapshot ONE game's custom Steam shortcut art (keyed by its gospel
        appid) AND its game-folder saves into local-payloads, so a recreated
        shortcut gets its art back and a wiped/replaced game folder gets its
        save back. The 🔍 Scan bundle does this for every game at once."""
        recipe = self._pick_one("🎨 CAPTURE ART + SAVES + SETTINGS",
                                 "Capture for:")
        if recipe is None:
            return
        if self.local_payloads is None:
            self.ui.msg("No local-payloads dir (NAS/SD) set to capture into.",
                        "warn")
            return
        self._capture_one(recipe, interactive=True)
        snapped = self._snapshot_localconfig()
        if snapped:
            self.ui.msg(f"Snapshotted localconfig.vdf for {snapped} user(s) "
                        f"-> {state} (perf/display source).", "dim")

    @staticmethod
    def _gb(n: float) -> str:
        return f"{n / (1 << 30):.2f} GB"

    @staticmethod
    def _eta(seconds: float) -> str:
        if seconds < 0 or seconds > 86400:
            return "--:--"
        return f"{int(seconds) // 60:d}:{int(seconds) % 60:02d}"

    def cmd_deploy_game(self):
        """Pull a game staged on the NAS (_games/<name>/) onto the SD card.
        The one thing a reimage restore couldn't put back: the game itself."""
        self.ui.header("⬇️  DEPLOY GAME FROM NAS")
        if self.local_payloads is None:
            self.ui.msg("No local-payloads dir (NAS/SD) configured — run "
                        "🔌 Connect NAS Payloads first.", "warn")
            return
        root = deploy.staged_root(self.local_payloads)
        self.ui.msg(f"📂 Staged games: {root}", "dim")
        games = deploy.list_staged(self.local_payloads)
        if not games:
            self.ui.msg(f"Nothing staged yet. Copy a game folder to {root}/ "
                        "(e.g. '_games/Battlefield 3/') and re-run.", "warn")
            return
        games_dirs = sdscan.find_games_dirs()
        if not games_dirs:
            raw = self.ui.input("No SD Games/ folder found — enter one "
                                "(blank to cancel)")
            if not raw:
                return
            dest_root = Path(raw)
            if not dest_root.is_dir():
                self.ui.msg(f"Not a directory: {dest_root}", "error")
                return
        elif len(games_dirs) == 1:
            dest_root = games_dirs[0]
        else:
            picked = self.ui.choose("Deploy to which card?",
                                    [str(g) for g in games_dirs])
            if not picked:
                return
            dest_root = Path(picked[0])
        self.ui.msg(f"🎯 Destination : {dest_root}", "dim")
        self.ui.msg("", "dim")

        by_label, options = {}, []
        for g in games:
            todo, size, skipped = deploy.plan(g, dest_root)
            if not todo:
                state = "✅ already on the card"
            elif skipped:
                state = f"↻ resume — {self._gb(size)} of {self._gb(g.size)} left"
            else:
                state = f"{self._gb(g.size)}, {g.files} files"
            label = f"{g.name}  ({state})"
            options.append(label)
            by_label[label] = (g, size)
        back = "⬅️  Cancel"
        picked = self.ui.choose("Deploy which game?", options + [back])
        if not picked or picked[0] == back:
            return
        game, need = by_label[picked[0]]
        if need == 0:
            self.ui.msg(f"{game.name} is already fully on the card — nothing "
                        "to copy.", "success")
            return

        free = deploy.free_space(dest_root)
        self.ui.msg(f"Need {self._gb(need)}, free {self._gb(free)}", "dim")
        if free and need > free:
            self.ui.msg(f"Not enough room on {dest_root}: needs "
                        f"{self._gb(need)}, only {self._gb(free)} free. "
                        "Freeing space now beats failing at 95%.", "error")
            return
        ok = self.ui.choose(f"Copy {game.name} ({self._gb(need)}) to "
                            f"{dest_root}?", ["✅ Yes, copy", "⬅️  Cancel"])
        if not ok or not ok[0].startswith("✅"):
            return

        started = time.monotonic()

        def _show(done: int, total: int, rel: str) -> None:
            pct = (done * 100 // total) if total else 100
            elapsed = max(time.monotonic() - started, 0.001)
            rate = done / elapsed
            eta = (total - done) / rate if rate > 0 else -1
            name = (rel[:38] + "…") if len(rel) > 39 else rel
            self.ui.progress(
                f"  {pct:3d}%  {self._gb(done)}/{self._gb(total)}  "
                f"{rate / (1 << 20):5.1f} MB/s  ETA {self._eta(eta)}  {name}")

        try:
            stats = deploy.deploy(game, dest_root, progress=_show,
                                  log=lambda m: self.ui.msg(m, "warn"))
        except OSError as e:
            self.ui.progress_done()
            self.ui.msg(f"Copy failed: {e}", "error")
            self.ui.msg("Re-run to resume — finished files are kept.", "warn")
            return
        except KeyboardInterrupt:
            self.ui.progress_done()
            self.ui.msg("Cancelled. Re-run to resume where it stopped.", "warn")
            return
        self.ui.progress_done()
        secs = stats["seconds"]
        rate = stats["bytes"] / max(secs, 0.001) / (1 << 20)
        self.ui.msg(f"{game.name} deployed to {stats['dest']}", "success")
        self.ui.msg(f"  {stats['copied']} file(s), {self._gb(stats['bytes'])} "
                    f"in {int(secs) // 60}m {int(secs) % 60}s ({rate:.1f} MB/s)"
                    + (f", {stats['skipped']} already there"
                       if stats["skipped"] else ""), "dim")
        self.ui.msg("Now run 📁 Scan SD so the map picks it up, then 🔧 Apply "
                    "to add its shortcut + fixes.", "dim")

    def _registry_by_appid(self) -> dict:
        """prefix_registry.json entries keyed by appid string."""
        reg = self.store_root / "prefix_registry.json"
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(e["appid"]): e for e in data.get("entries", [])
                if e.get("appid") is not None}

    def _import_one(self, backup, reg) -> None:
        name = (reg.get(backup.appid, {}).get("name") or backup.safe_name)
        if not backup.has_pfx:
            self.ui.msg(f"{name}: backup has no pfx/ inside — skipping "
                        f"({backup.path})", "warn")
            return
        self.ui.msg(f"Importing {name} (appid {backup.appid})…", "dim")
        try:
            dst, files = prefiximport.restore(
                backup, self.steam_root, log=lambda m: self.ui.msg(m, "dim"))
        except OSError as e:
            self.ui.msg(f"{name}: import failed — {e}", "error")
            return
        self.ui.msg(f"{name}: prefix restored to {dst} ({files} files)",
                    "success")

    def cmd_import_prefixes(self):
        """Restore prefixes backed up by the old Linux Prefix Manager into
        compatdata — the other half of the gospel-appid design."""
        self.ui.header("📥 IMPORT PREFIX BACKUPS")
        if self.steam_root is None:
            self.ui.msg("Steam root not found — nowhere to import to.", "warn")
            return
        roots = prefiximport.backup_roots()
        if not roots:
            self.ui.msg("No prefix-backup folder found on any SD card. Looked "
                        "for <SD>/steamos_restore/prefix_backups/ and "
                        "<SD>/bazzite_restore/prefix backups/.", "warn")
            return
        for r in roots:
            self.ui.msg(f"📂 {r}", "dim")
        backups = prefiximport.list_backups(roots)
        if not backups:
            self.ui.msg("Backup folder exists but holds no <name>/<appid>/ "
                        "prefixes.", "warn")
            return
        reg = self._registry_by_appid()
        self.ui.msg("", "dim")
        self.ui.msg("Install each game and let Steam make its shortcut BEFORE "
                    "importing — Steam wipes compatdata around first launch, "
                    "so import last.", "warn")
        self.ui.msg("", "dim")
        by_label, options = {}, []
        for b in backups:
            entry = reg.get(b.appid, {})
            name = entry.get("name") or b.safe_name
            live = prefiximport.is_live(self.steam_root, b.appid)
            mark = "⚠️ " if live else "  "
            note = " [live prefix will be set aside]" if live else ""
            if not b.has_pfx:
                note = " [no pfx/ — empty backup]"
            label = f"{mark}{name}  (appid {b.appid}){note}"
            options.append(label)
            by_label[label] = b
        all_opt = f"⏬ Import ALL {len(backups)} prefixes"
        back = "⬅️  Cancel"
        picked = self.ui.choose("Import which prefix?",
                                options + [all_opt, back])
        if not picked or picked[0] == back:
            return
        chosen = backups if picked[0] == all_opt else [by_label[picked[0]]]
        live_n = sum(1 for b in chosen
                     if prefiximport.is_live(self.steam_root, b.appid))
        if live_n:
            self.ui.msg(f"{live_n} of these already have a live prefix. Each "
                        f"is kept as <appid>{prefiximport.PREFIX_BAK}, but if "
                        "the live one is NEWER than the backup you'd be going "
                        "backwards.", "warn")
        ok = self.ui.choose(f"Import {len(chosen)} prefix(es)?",
                            ["✅ Yes, import", "⬅️  Cancel"])
        if not ok or not ok[0].startswith("✅"):
            return
        for b in chosen:
            self._import_one(b, reg)
        self.ui.msg("Done. If a game still starts fresh, its shortcut appid "
                    "doesn't match the prefix — run Apply (which forces the "
                    "gospel appid) then re-check.", "dim")

    def _capture_saves(self, recipe, interactive: bool = True) -> bool:
        """Snapshot game-folder saves (data.bin & friends) to local-payloads.
        These live next to the exe, so NO prefix backup covers them."""
        if not recipe.save_paths:
            return False
        game_dir = self.game_dir_for(recipe, interactive=interactive)
        if game_dir is None:
            if interactive:
                self.ui.msg(f"{recipe.name} not located — skipping save "
                            "capture.", "warn")
            return False
        dest = self.local_payloads / recipe.id / "saves"
        entries, files = saves.capture(
            recipe, game_dir, self.steam_root, dest,
            log=(lambda m: self.ui.msg(m, "dim")) if interactive
            else (lambda _m: None))
        if files:
            self.ui.msg(f"  💾 {recipe.name}: {files} save file(s)/folder(s) "
                        f"across {entries} path(s)", "success")
            return True
        if interactive:
            self.ui.msg(f"No saves found yet for {recipe.name} — play it once, "
                        "then capture.", "warn")
        return False

    def _saves_snapshot_for(self, recipe):
        """(src_dir, entries) for a recipe's captured saves, or (None, [])."""
        if not recipe.save_paths or self.local_payloads is None:
            return None, []
        src = self.local_payloads / recipe.id / "saves"
        return src, saves.read_index(src)

    def _restore_saves_one(self, recipe, interactive: bool = True) -> int:
        src, entries = self._saves_snapshot_for(recipe)
        if not entries:
            return 0
        game_dir = self.game_dir_for(recipe, interactive=interactive)
        if game_dir is None:
            if interactive:
                self.ui.msg(f"{recipe.name} not located.", "warn")
            return 0
        n = saves.restore(recipe, game_dir, self.steam_root, src,
                          log=lambda m: self.ui.msg(m, "dim"))
        if n:
            self.ui.msg(f"  💾 {recipe.name}: {n} save file(s)/folder(s) "
                        "restored", "success")
        return n

    def _restore_saves_all(self) -> None:
        """Restore game-folder saves for every game that HAS a snapshot."""
        pending = []
        for recipe in self.recipes:
            _src, entries = self._saves_snapshot_for(recipe)
            if entries:
                pending.append(recipe)
        if not pending:
            self.ui.msg("No captured game-folder saves to restore — run 🔍 Scan "
                        "first to capture them.", "warn")
            return
        self.ui.msg(f"{len(pending)} game(s) have captured saves: "
                    + ", ".join(r.name for r in pending), "dim")
        self.ui.msg(f"Anything already live is kept as *{saves.SAVE_BAK} — but "
                    "if a live save is NEWER than its snapshot, restoring "
                    "buries it.", "warn")
        ok = self.ui.choose(f"Restore saves for {len(pending)} game(s)?",
                            ["✅ Yes, restore", "⬅️  Skip"])
        if not ok or not ok[0].startswith("✅"):
            return
        total = sum(self._restore_saves_one(r, interactive=False)
                    for r in pending)
        self.ui.msg(f"Restored {total} save file(s)/folder(s) across "
                    f"{len(pending)} game(s).", "success" if total else "warn")

    def cmd_restore_saves(self):
        """Put ONE game's captured game-folder saves back. The ♻️ Save Restore
        bundle does every game at once."""
        recipe = self._pick_one("♻️  RESTORE GAME SAVES", "Restore saves for:")
        if recipe is None:
            return
        if not recipe.save_paths:
            self.ui.msg(f"{recipe.name} declares no save_paths — its saves "
                        "live in the prefix, so a prefix restore covers them.",
                        "warn")
            return
        if self.local_payloads is None:
            self.ui.msg("No local-payloads dir (NAS/SD) to restore from.",
                        "warn")
            return
        src, entries = self._saves_snapshot_for(recipe)
        if not entries:
            self.ui.msg(f"No captured saves for {recipe.name} at {src} — "
                        "capture them first (🎨).", "warn")
            return
        self.ui.msg(f"This writes {len(entries)} captured save path(s) over the "
                    "live game files.", "warn")
        self.ui.msg(f"Anything already there is kept as *{saves.SAVE_BAK} — but "
                    "if your live save is NEWER than the snapshot, restoring "
                    "will bury it.", "warn")
        ok = self.ui.choose("Restore saves?", ["✅ Yes, restore", "⬅️  Cancel"])
        if not ok or not ok[0].startswith("✅"):
            return
        n = self._restore_saves_one(recipe, interactive=True)
        self.ui.msg(f"Restored {n} save file(s)/folder(s) for {recipe.name}.",
                    "success" if n else "warn")

    def cmd_scan_all(self):
        """Take stock of everything and snapshot it: SD games -> Steam games
        -> adopt orphan prefixes -> capture art/saves/settings.

        Order matters: scan SD and Steam FIRST so detection knows where every
        game lives, otherwise reconcile has nothing to match against and
        capture skips games it can't locate."""
        self.ui.header("🔍 SCAN")
        self.ui.msg("SD games → Steam games → reconcile prefixes → capture "
                    "art/saves/settings", "dim")
        self.ui.msg("", "dim")
        self.ui.msg("── 1/4  Scanning SD for games " + "─" * 12, "info")
        self.cmd_scan_sd()
        self.ui.msg("── 2/4  Scanning Steam libraries " + "─" * 9, "info")
        self.cmd_scan_steam()
        self.ui.msg("── 3/4  Reconciling prefixes " + "─" * 13, "info")
        self.cmd_reconcile()
        self.ui.msg("── 4/4  Capturing art + saves + settings " + "─" * 1,
                    "info")
        self._capture_all()
        self.ui.msg("", "dim")
        self.ui.msg("Scan complete.", "success")

    def cmd_save_restore(self):
        """Put saves back after a reimage: prefix backups -> game-folder saves.

        Reconcile is deliberately NOT part of this — it repoints a shortcut at
        an OLD prefix already on disk, which would leave Steam ignoring the
        prefix we just imported at the gospel appid. It lives in 🔍 Scan."""
        self.ui.header("♻️  SAVE RESTORE")
        self.ui.msg("Prefix backups → game-folder saves", "dim")
        self.ui.msg("Install your games first — Steam wipes compatdata around "
                    "first launch, so restoring last is what sticks.", "warn")
        self.ui.msg("", "dim")
        self.ui.msg("── 1/2  Importing prefix backups " + "─" * 9, "info")
        self.cmd_import_prefixes()
        self.ui.msg("── 2/2  Restoring game-folder saves " + "─" * 6, "info")
        self._restore_saves_all()
        self.ui.msg("", "dim")
        self.ui.msg("Save restore complete.", "success")

    def menu_settings(self):
        """Setup + maintenance: the tool's own plumbing, not game work."""
        while True:
            self.ui.header("⚙️  SETTINGS")
            self.ui.msg(f"📦 Store : {self.store_root or 'NOT FOUND'}", "dim")
            self.ui.msg(f"📡 NAS   : {self.local_payloads or 'not connected'}",
                        "dim")
            self.ui.msg("", "dim")
            choice = self.ui.choose("Setup & maintenance:", [
                "🔌 Connect NAS Payloads (SMB automount)",
                "🧰 Stage GE-Proton Runner to NAS",
                "💾 Mirror Store (offline copy of recipes on SD/NAS)",
                "⬆️  Update (git pull latest recipes + code)",
                "🖥️  Install Shortcut (put GFM on Desktop + Game Mode)",
                "⬅️  Back"])
            choice = choice[0] if choice else "⬅️  Back"
            if choice.startswith("🔌"):
                self.cmd_setup_nas()
            elif choice.startswith("🧰"):
                self.cmd_stage_runner()
            elif choice.startswith("💾"):
                self.cmd_mirror(None)
            elif choice.startswith("⬆"):
                self.cmd_update()
            elif choice.startswith("🖥"):
                self.cmd_install()
            else:
                return
            self.ui.input("Press Enter to continue")

    def menu_advanced(self):
        """The individual steps the two bundles wrap, for surgical use."""
        while True:
            self.ui.header("🛠  ADVANCED")
            self.ui.msg("Individual steps — 🔍 Scan and ♻️  Save Restore run "
                        "these for you.", "dim")
            self.ui.msg("", "dim")
            choice = self.ui.choose("Run which step?", [
                "📁 Scan SD for Games (paths + exes + readmes)",
                "📚 Scan Steam Libraries (inventory + buildids)",
                "🔗 Reconcile Prefixes (adopt existing compatdata)",
                "🎨 Capture one game (art + saves)",
                "📥 Import Prefix Backups (backup -> compatdata)",
                "♻️  Restore Game Saves (one game)",
                "🩺 Test The Crew Server",
                "⬅️  Back"])
            choice = choice[0] if choice else "⬅️  Back"
            if choice.startswith("📁"):
                self.cmd_scan_sd()
            elif choice.startswith("📚"):
                self.cmd_scan_steam()
            elif choice.startswith("🔗"):
                self.cmd_reconcile()
            elif choice.startswith("🎨"):
                self.cmd_capture()
            elif choice.startswith("📥"):
                self.cmd_import_prefixes()
            elif choice.startswith("♻"):
                self.cmd_restore_saves()
            elif choice.startswith("🩺"):
                self.cmd_test_crew()
            else:
                return
            self.ui.input("Press Enter to continue")

    def cmd_stage_runner(self):
        """Copy a compatibilitytools.d runner (e.g. GE-Proton) to the NAS
        _runners/ so install_runner can side-load it after a reimage."""
        if self.steam_root is None:
            self.ui.msg("Steam root not found.", "warn")
            return
        if self.local_payloads is None:
            self.ui.msg("No local-payloads dir (NAS/SD) set to stage into.", "warn")
            return
        ctd = self.steam_root / "compatibilitytools.d"
        runners = sorted(d.name for d in ctd.iterdir() if d.is_dir()) \
            if ctd.is_dir() else []
        if not runners:
            self.ui.msg("No custom runners in compatibilitytools.d — install one "
                        "via ProtonUp-Qt first.", "warn")
            return
        back = "⬅️  Cancel"
        picked = self.ui.choose("Stage which runner to the NAS?", runners + [back])
        if not picked or picked[0] == back:
            return
        name = picked[0]
        import tarfile
        dest = self.local_payloads / "_runners" / f"{name}.tar.gz"
        if dest.is_file():
            if not self.ui.confirm(f"{name} already staged — overwrite?"):
                return
        self.ui.msg(f"Packing {name} -> {dest} (reads the read-only runner dir, "
                    "writes ONE file over the NAS, keeps exec bits)...", "dim")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".gz.tmp")
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(ctd / name, arcname=name)
            tmp.replace(dest)
        except OSError as e:
            self.ui.msg(f"Failed to stage {name}: {e}", "error")
            return
        self.ui.msg(f"Staged {name} -> {dest}. install_runner will side-load it "
                    "after a reimage.", "success")

    def cmd_test_crew(self):
        """Health-check the TCU (The Crew) offline-server network setup: the
        systemd unit, the unprivileged-port-443 sysctl, the ubiservices ->
        127.0.0.1 DNAT rule, DNS, and whether the server is currently live
        (it only binds 127.0.0.1:443 while the game is running)."""
        import socket
        self.ui.msg("The Crew / TCU — server health check", "warn")

        def run(cmd):
            try:
                return subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=8)
            except (OSError, subprocess.SubprocessError):
                return None

        checks = []
        r = run(["systemctl", "is-active", "tcu-network.service"])
        state = r.stdout.strip() if r else "n/a"
        checks.append(("tcu-network.service active", state == "active", state))

        r = run(["sysctl", "-n", "net.ipv4.ip_unprivileged_port_start"])
        try:
            port_ok = r is not None and int(r.stdout.strip()) <= 443
            pval = r.stdout.strip()
        except (ValueError, AttributeError):
            port_ok, pval = False, "n/a"
        checks.append(("port 443 bindable without root", port_ok, pval))

        # The rule is added by the service's ExecStart, so an active service
        # means it's in place. iptables -C needs root to read the table, so we
        # only UPGRADE to "verified" when passwordless sudo actually confirms
        # it — a failed sudo must not be reported as "rule missing".
        r = run(["sudo", "-n", "iptables", "-t", "nat", "-C", "OUTPUT", "-d",
                 "public-ubiservices.ubi.com", "-j", "DNAT",
                 "--to-destination", "127.0.0.1"])
        if r is not None and r.returncode == 0:
            checks.append(("ubiservices -> 127.0.0.1 DNAT rule", True,
                           "verified present"))
        elif state == "active":
            checks.append(("ubiservices -> 127.0.0.1 DNAT rule", True,
                           "added by the active service (sudo needed to verify directly)"))
        else:
            checks.append(("ubiservices -> 127.0.0.1 DNAT rule", False,
                           "service not active — rule not applied"))

        try:
            ip = socket.gethostbyname("public-ubiservices.ubi.com")
            dns_ok = True
        except OSError:
            ip, dns_ok = "(resolve failed)", False
        checks.append(("public-ubiservices.ubi.com resolves", dns_ok, ip))

        live = False
        try:
            s = socket.socket()
            s.settimeout(2)
            live = s.connect_ex(("127.0.0.1", 443)) == 0
            s.close()
        except OSError:
            pass
        checks.append(("TCU server live on 127.0.0.1:443 (game must be running)",
                       live, "listening" if live else "not listening"))

        for name, ok, detail in checks:
            self.ui.msg(f"  {'PASS' if ok else 'FAIL'}  {name}  [{detail}]",
                        "success" if ok else "warn")
        plumbing_ok = checks[0][1] and checks[1][1]
        if plumbing_ok:
            self.ui.msg("Network plumbing is good — launch the game and the TCU "
                        "server should come up on 127.0.0.1:443.", "success")
        else:
            self.ui.msg("Plumbing incomplete — re-apply the-crew (set a password "
                        "with `passwd` first if the systemd step needs sudo).",
                        "warn")

    def cmd_list(self):
        if not self.recipes:
            self.ui.msg(f"No recipes found (store: {self.store_root})", "warn")
            return
        self.ui.header("🔧 GAME FIXES")
        self.ui.msg(f"Store : {self.store_root}", "dim")
        self.ui.msg(f"Steam : {self.steam_root or 'not found'}", "dim")
        for recipe in self.recipes:
            game_dir = self.game_dir_for(recipe, interactive=False)
            self.ui.msg(self.status_line(recipe, game_dir))

    def _recipes_by_ids(self, ids: list[str]):
        by_id = {r.id: r for r in self.recipes}
        missing = [i for i in ids if i not in by_id]
        if missing:
            self.ui.msg(f"Unknown recipe id(s): {', '.join(missing)}", "error")
            sys.exit(1)
        return [by_id[i] for i in ids]

    def _pick_one(self, title: str, prompt: str):
        """Single-select from the recipe list (+ a Back entry). Returns one
        recipe, or None to finish. Clears the screen first (via header) so it
        never stacks under previous apply output. The list is LIGHT — just
        name + a not-found marker — so it stays fast; it deliberately skips
        the applied-status hash check (which would re-read NAS payloads on
        every redraw). Full applied status lives in the Status command."""
        self.ui.header(title)
        options, by_label = [], {}
        for recipe in self.recipes:
            game_dir = self.game_dir_for(recipe, interactive=False)
            mark = "❓ " if (recipe.requires_game and game_dir is None) else "  "
            label = f"{mark}{recipe.name}"
            options.append(label)
            by_label[label] = recipe
        back = "⬅️  Done / back to menu"
        options.append(back)
        picked = self.ui.choose(prompt, options)   # single-select
        if not picked or picked[0] == back:
            return None
        return by_label.get(picked[0])

    def _pick_many(self, title: str, prompt: str) -> list:
        """Multi-select recipe picker (arrow-key toggle on the Deck).

        Apply/Revert act on SEVERAL games in one pass — which is also why
        every queued VDF write lands behind a single Steam bounce at the end
        rather than one bounce per game."""
        self.ui.header(title)
        options, by_label = [], {}
        for recipe in self.recipes:
            game_dir = self.game_dir_for(recipe, interactive=False)
            mark = "❓ " if (recipe.requires_game and game_dir is None) else "  "
            label = f"{mark}{recipe.name}"
            options.append(label)
            by_label[label] = recipe
        picked = self.ui.choose(prompt, options, multi=True)
        return [by_label[p] for p in picked if p in by_label]

    def _apply_one(self, recipe) -> None:
        self.log(f"apply {recipe.id}")
        game_dir = self.game_dir_for(recipe, interactive=True)
        if game_dir is None:
            self.ui.msg(f"Skipping {recipe.name} — not located.", "warn")
            return
        if recipe.remote_payloads:
            if self.args.dry_run:
                for i in recipe.remote_payloads:
                    self.ui.msg(f'DRY RUN would fetch {i["url"]} '
                                f'({i.get("size", 0) // (1 << 20)} MB)', "dim")
                self.ui.msg(f"{recipe.name}: real apply needed to fetch.", "warn")
                return
            try:
                fetch.ensure_remote_payloads(
                    recipe, log=lambda m: self.ui.msg(m, "dim"))
            except fetch.FetchError as e:
                self.ui.msg(f"{recipe.name}: {e}", "error")
                return
        ok = self.run_engine(recipe, game_dir, engine.apply_recipe)
        if ok and recipe.post_apply_message:
            self.ui.msg("── Manual step needed " + "─" * 20, "warn")
            for line in recipe.post_apply_message.splitlines():
                self.ui.msg(line, "warn")

    def cmd_apply(self, ids: list[str]):
        if ids:
            for recipe in self._recipes_by_ids(ids):
                self._apply_one(recipe)
            self.flush_vdf_writes()
            return
        # Multi-select: tick several games, apply them all, ONE Steam bounce.
        chosen = self._pick_many(
            "🔧 APPLY FIXES",
            "Pick games — ←→ toggle, Enter confirm, Esc cancel")
        if not chosen:
            return
        for i, recipe in enumerate(chosen, 1):
            self.ui.header(f"Applying {i}/{len(chosen)}: {recipe.name}")
            self._apply_one(recipe)
            if i < len(chosen):
                self.ui.input("Press Enter for the next game")
        self.ui.input("Press Enter to continue")
        self.flush_vdf_writes()

    def cmd_revert(self, ids: list[str]):
        if ids:
            for recipe in self._recipes_by_ids(ids):
                gd = self.game_dir_for(recipe, interactive=True)
                if gd and self.ui.confirm(f"Revert {recipe.name}?", danger=True):
                    self.run_engine(recipe, gd, engine.revert_recipe)
            self.flush_vdf_writes()
            return
        chosen = self._pick_many(
            "↩️  REVERT A GAME",
            "Pick games to revert — ←→ toggle, Enter confirm, Esc cancel")
        if not chosen:
            return
        names = ", ".join(r.name for r in chosen)
        if not self.ui.confirm(f"Revert {len(chosen)} game(s)? {names}",
                               danger=True):
            return
        for i, recipe in enumerate(chosen, 1):
            self.ui.header(f"Reverting {i}/{len(chosen)}: {recipe.name}")
            gd = self.game_dir_for(recipe, interactive=True)
            if gd:
                self.run_engine(recipe, gd, engine.revert_recipe)
            if i < len(chosen):
                self.ui.input("Press Enter for the next game")
        self.ui.input("Press Enter to continue")
        self.flush_vdf_writes()

    def cmd_update(self):
        """Pull the latest recipes and code from GitHub. If code changed,
        offer to restart the app in place; recipe-only changes reload
        without a restart."""
        app_dir = Path(__file__).resolve().parent
        self.ui.header("⬆️  UPDATE")
        if not (app_dir / ".git").is_dir():
            self.ui.msg(f"Not a git checkout: {app_dir}", "warn")
            self.ui.msg("This copy likely came from the SD mirror or a manual "
                        "download — nothing to pull.", "dim")
            self.ui.msg("To install a fresh checkout, run the one-liner from "
                        "the README.", "dim")
            return

        def git(*args) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(app_dir), *args],
                capture_output=True, text=True)

        old_head = git("rev-parse", "HEAD").stdout.strip()
        self.ui.msg(f"Local commit: {old_head[:7]}", "dim")
        self.ui.msg("Checking GitHub...", "dim")

        pull = git("pull", "--ff-only")
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout).strip()
            self.ui.msg("Update failed:", "error")
            for line in err.splitlines()[:8]:
                self.ui.msg(f"  {line}", "dim")
            return

        new_head = git("rev-parse", "HEAD").stdout.strip()
        if old_head == new_head:
            self.ui.msg("Already up to date.", "success")
            return

        # What actually changed?
        log = git("log", "--oneline", "--no-decorate",
                  f"{old_head}..{new_head}").stdout.strip()
        changed = git("diff", "--name-only",
                      f"{old_head}..{new_head}").stdout.splitlines()
        code_changed = any(f.endswith(".py") for f in changed)

        self.ui.msg(f"Updated {old_head[:7]} → {new_head[:7]}.", "success")
        self.ui.msg("New commits:", "info")
        for line in log.splitlines()[:8]:
            self.ui.msg(f"  {line}", "dim")

        if code_changed:
            if self.ui.confirm("Code was updated — restart the app now?"):
                os.execv(sys.executable,
                         [sys.executable, str(app_dir / "gfm.py")])
            else:
                self.ui.msg("Restart later to pick up the code changes.",
                            "warn")
        else:
            # Recipe-only: reload without restart so the new games appear.
            self.recipes = manifest.load_all(self.store_root)
            self.ui.msg(f"Recipes reloaded: {len(self.recipes)} in the store.",
                        "success")

    def cmd_setup_nas(self):
        """Install a systemd automount for the NAS payload share and point
        GFM at it — all from inside the tool. Deck/Linux only."""
        import subprocess
        import tempfile
        if os.name == "nt":
            self.ui.msg("NAS mount setup is Deck/Linux only.", "warn")
            return
        self.ui.header("🔌 CONNECT NAS PAYLOADS")
        self.ui.msg("Sets up an on-demand SMB automount that survives reboots "
                    "and won't hang boot when the NAS is offline.", "dim")
        self.ui.msg("", "dim")

        host = self.ui.input("NAS host / IP", "192.168.1.33")
        share = self.ui.input("SMB share name", "Game Fixes")
        mount_point = self.ui.input("Mount point",
                                    str(Path.home() / "mnt" / "game-fixes"))
        if not host or not share or not mount_point:
            self.ui.msg("Cancelled.", "warn")
            return
        user = self.ui.input("SMB username (blank = guest)")
        password = self.ui.input("SMB password", password=True) if user else ""

        if not __import__("shutil").which("mount.cifs") and \
                not Path("/sbin/mount.cifs").exists():
            self.ui.msg("Note: mount.cifs not found — if the mount later fails "
                        "with 'wrong fs type', install cifs-utils "
                        "(sudo steamos-readonly disable first on SteamOS).",
                        "warn")

        if not self.ui.confirm("Install the automount now? (needs sudo)",
                               danger=True):
            return

        def sudo_write(path: str, content: str, mode: str | None = None):
            tmp = Path(tempfile.gettempdir()) / ("gfm-" + Path(path).name)
            tmp.write_text(content, encoding="utf-8")
            subprocess.run(["sudo", "cp", str(tmp), path], check=True)
            if mode:
                subprocess.run(["sudo", "chmod", mode, path], check=True)
            tmp.unlink(missing_ok=True)

        cred_file = "/etc/gfm-smb.cred"
        if user:
            sudo_write(cred_file, f"username={user}\npassword={password}\n",
                       mode="600")
            cred_opt = f"credentials={cred_file}"
        else:
            cred_opt = "guest"

        Path(mount_point).mkdir(parents=True, exist_ok=True)
        uid, gid = os.getuid(), os.getgid()

        def esc(suffix):
            return subprocess.run(
                ["systemd-escape", "-p", f"--suffix={suffix}", mount_point],
                capture_output=True, text=True, check=True).stdout.strip()
        mount_unit, auto_unit = esc("mount"), esc("automount")

        sudo_write(f"/etc/systemd/system/{mount_unit}",
                   "[Unit]\nDescription=Game Fixes SMB share (GFM local "
                   "payloads)\nAfter=network-online.target\n"
                   "Wants=network-online.target\n\n[Mount]\n"
                   f"What=//{host}/{share}\nWhere={mount_point}\nType=cifs\n"
                   f"Options={cred_opt},uid={uid},gid={gid},ro,iocharset=utf8,"
                   "vers=3.0,_netdev,nofail\nTimeoutSec=20\n")
        sudo_write(f"/etc/systemd/system/{auto_unit}",
                   "[Unit]\nDescription=Automount for Game Fixes SMB share\n\n"
                   f"[Automount]\nWhere={mount_point}\nTimeoutIdleSec=300\n\n"
                   "[Install]\nWantedBy=multi-user.target\n")

        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "--now", auto_unit],
                       check=True)

        # Wire GFM to use it — persist to config
        self.cfg["local_payloads_dir"] = mount_point
        self.local_payloads = Path(mount_point)
        store.save_config(self.cfg)

        self.ui.msg("", "dim")
        self.ui.msg(f"Done. Automount installed and GFM pointed at "
                    f"{mount_point}.", "success")

        # Verify: accessing the mount point triggers the automount. Report
        # what's actually visible so you know it worked, not just "it said so".
        self.ui.msg("Testing the mount (first access triggers it)...", "dim")
        mp = Path(mount_point)
        try:
            entries = [p.name for p in mp.iterdir()]
        except Exception as e:
            entries = None
            self.log(f"mount test error: {e}")
        if entries is None:
            self.ui.msg("Mount not reachable yet. Check the NAS is on, the "
                        "share/creds are right, and that cifs-utils is "
                        "installed. See the log for details.", "error")
        elif not entries:
            self.ui.msg("Mounted, but the share looks EMPTY. Have you staged "
                        "the payloads onto it yet? (tools/stage-eclipse.py "
                        "writes them there from your PC.)", "warn")
        else:
            eclipse = sum(1 for e in entries if e.startswith("eclipse-"))
            self.ui.msg(f"Mount OK — {len(entries)} folder(s) visible "
                        f"({eclipse} eclipse payloads). You're set.", "success")
        self.ui.msg("It re-mounts on access and after every reboot. Re-run "
                    "this after a reimage.", "dim")

    def cmd_scan_sd(self):
        """Scan the SD card's Games/ folder and pair each subfolder to a
        recipe. On confirmation, writes the mappings into the machine
        config's game_paths — so future Applies never prompt for a path."""
        self.ui.header("📁 SCAN SD FOR GAMES")
        games_dirs = sdscan.find_games_dirs()
        if not games_dirs:
            self.ui.msg("No 'Games' folder found on any mounted SD card.",
                        "warn")
            raw = self.ui.input("Enter a folder to scan (blank to cancel)")
            if not raw:
                return
            games_dir = Path(raw)
            if not games_dir.is_dir():
                self.ui.msg(f"Not a directory: {games_dir}", "error")
                return
        elif len(games_dirs) == 1:
            games_dir = games_dirs[0]
            self.ui.msg(f"Scanning {games_dir}", "dim")
        else:
            picked = self.ui.choose(
                "Multiple SD cards have Games/ folders — pick one:",
                [str(g) for g in games_dirs])
            if not picked:
                return
            games_dir = Path(picked[0])

        result = sdscan.scan(games_dir, self.recipes)
        matched, unmatched = result["matched"], result["unmatched"]

        if not matched and not unmatched:
            self.ui.msg("The Games folder is empty.", "warn")
            return

        self.ui.msg("", "dim")
        if matched:
            self.ui.msg(f"Matched {len(matched)}:", "success")
            for recipe, folder, signal in matched:
                self.ui.msg(f"  ✔ {recipe.name}  ←  {folder.name}  ({signal})",
                            "dim")
        if unmatched:
            self.ui.msg(f"Unmatched {len(unmatched)}:", "warn")
            for folder in unmatched:
                self.ui.msg(f"  ? {folder.name}", "dim")
            self.ui.msg("(Add a matching alias or install_dir_name to a "
                        "recipe, then re-scan.)", "dim")
        self.ui.msg("", "dim")

        if not matched and not unmatched:
            self.ui.msg("Nothing to save.", "warn")
            return

        # Preview the diff against what's already on disk
        dest = sdmap.default_write_path()
        if dest is None:
            self.ui.msg("No SD card mounted — can't save the map. Insert "
                        "the card and re-scan.", "error")
            return
        existing = sdmap.load_first()
        preview = sdmap.write(matched, unmatched, games_dir,
                              dest=dest.with_suffix(".preview.json"),
                              existing=existing, steam_root=self.steam_root)
        # Immediately delete the preview file — we only wanted the payload
        dest.with_suffix(".preview.json").unlink(missing_ok=True)
        d = sdmap.diff(existing, preview)
        if d["added"]:
            self.ui.msg(f"New: {', '.join(d['added'])}", "info")
        if d["changed"]:
            self.ui.msg(f"Path changed: {', '.join(d['changed'])}", "warn")
        if d["removed"]:
            self.ui.msg(f"Removed: {', '.join(d['removed'])}", "warn")
        if not (d["added"] or d["changed"] or d["removed"]) and not unmatched:
            self.ui.msg("Map already up to date — nothing changed.", "success")
            return

        self.ui.msg(f"Will write: {dest}", "dim")
        if not self.ui.confirm("Save the SD map to that path?"):
            return
        sdmap.write(matched, unmatched, games_dir, dest=dest,
                    existing=existing, steam_root=self.steam_root)
        self.ui.msg(
            f"Saved. {len(matched)} game(s) mapped, {len(unmatched)} "
            "folder(s) unmatched.", "success")
        self.ui.msg("The tool now looks here first — no path prompts for "
                    "mapped games.", "dim")

    def cmd_scan_steam(self):
        """Inventory every installed Steam game across every library
        (internal SSD, SD card, external) into sd_map.json's steam_games
        section. Cross-references recipes so it's obvious which owned games
        already have fixes and which are candidates."""
        self.ui.header("📚 SCAN STEAM LIBRARIES")
        if self.steam_root is None:
            self.ui.msg("No Steam root found — nothing to scan.", "warn")
            return
        self.ui.msg(f"Steam root: {self.steam_root}", "dim")

        games = steamscan.scan(self.steam_root)
        if not games:
            self.ui.msg("No installed games detected across any library.",
                        "warn")
            return
        with_r, without_r = steamscan.cross_reference(games, self.recipes)

        libs_seen = sorted({g["library"] for g in games})
        self.ui.msg(f"{len(games)} game(s) installed across "
                    f"{len(libs_seen)} library location(s):", "info")
        for lib in libs_seen:
            n = sum(1 for g in games if g["library"] == lib)
            self.ui.msg(f"  {lib}  ({n} game(s))", "dim")
        self.ui.msg(f"{with_r} already have a recipe, "
                    f"{without_r} do not.", "info")

        if without_r and without_r <= 20:
            self.ui.msg("Games without a recipe:", "dim")
            for g in games:
                if not g["has_recipe"]:
                    self.ui.msg(f'  · {g["name"]}  (appid {g["appid"]})', "dim")

        dest = sdmap.default_write_path()
        if dest is None:
            self.ui.msg("No SD card mounted — can't save the map. Insert "
                        "the card and re-scan.", "error")
            return
        if not self.ui.confirm(f"Write the inventory to {dest}?"):
            return
        existing = sdmap.load_first()
        sdmap.write_steam_section(games, dest=dest, existing=existing)
        self.ui.msg(
            "Saved. Claude can now see your full installed Steam library "
            "and suggest recipes for owned games.", "success")

    def cmd_reconcile(self):
        """Wire non-Steam shortcuts to pre-existing compatdata prefixes.
        For each recipe whose shortcut points at an empty prefix while a
        populated candidate sits elsewhere in compatdata, rewrite the
        shortcut's appid (and its CompatToolMapping) to the candidate."""
        if self.steam_root is None:
            self.ui.msg("No Steam root — nothing to reconcile.", "warn")
            return
        self.ui.header("🔗 RECONCILE PREFIXES")
        gbm_map = prefixes.load_gbm_csv()
        backup_map = prefixes.load_gbm_backup_map()
        self.ui.msg(
            f"GBM sources: {len(backup_map)} SD backups, "
            f"{len(gbm_map)} CSV entries", "dim")

        # shortcut appids we're processing (never adopt one of THESE as target)
        all_shortcut_ids: set[int] = set()
        for r in self.recipes:
            try:
                all_shortcut_ids.update(
                    shortcutsvdf.find_appids(self.steam_root, r.all_names))
            except shortcutsvdf.ShortcutsError:
                pass

        # plan: (recipe, current_appid, target_appid, signal, friendly_name)
        plan: list[tuple] = []
        for recipe in self.recipes:
            try:
                sc_ids = shortcutsvdf.find_appids(self.steam_root, recipe.all_names)
            except shortcutsvdf.ShortcutsError:
                continue
            if not sc_ids:
                continue
            current = sc_ids[0]
            if prefixes.shortcut_has_live_prefix(self.steam_root, current):
                continue

            excl = (all_shortcut_ids - {current})  # don't steal from siblings
            cands = prefixes.find_candidates(
                self.steam_root, recipe, gbm_map, backup_map, excl)
            if not cands:
                self.ui.msg(f"  {recipe.name}: no candidate prefix found", "dim")
                continue
            if len(cands) == 1:
                target, signal, name = cands[0]
                self.ui.msg(
                    f'  {recipe.name}: compatdata/{target.name}  '
                    f'"{name}" ({signal})', "info")
            else:
                self.ui.msg(f"  {recipe.name}: multiple candidates, please pick",
                            "warn")
                labels = [f'compatdata/{p.name}  "{n}" ({s})'
                          for p, s, n in cands]
                picked = self.ui.choose(
                    f"Which prefix is {recipe.name}?", labels)
                if not picked:
                    continue
                idx = labels.index(picked[0])
                target, signal, name = cands[idx]
            plan.append((recipe, current, int(target.name), signal, name))

        if not plan:
            self.ui.msg("Nothing to reconcile — every shortcut already has a "
                        "live prefix (or no candidate exists).", "success")
            return

        self.ui.msg("", "dim")
        self.ui.msg("Plan:", "info")
        for recipe, cur, tgt, sig, name in plan:
            self.ui.msg(
                f'  {recipe.name}: shortcut appid {cur} → {tgt}  '
                f'"{name}" via {sig}', "dim")
        if not self.ui.confirm(
                "Rewrite shortcut appids to match the existing prefixes? "
                "Steam will close briefly.", danger=True):
            return

        was_running = steamvdf.steam_running()
        if was_running:
            steamvdf.close_steam(lambda m: self.ui.msg(m, "warn"))

        for recipe, cur, tgt, _sig, _name in plan:
            n = shortcutsvdf.set_appid(self.steam_root, recipe.all_names, tgt)
            moved = steamvdf.remap_compat_tool(self.steam_root, cur, tgt)
            self.ui.msg(
                f"  {recipe.name}: shortcut updated ({n} file(s)); "
                f"compat mapping {'remapped' if moved else 'no entry'}",
                "success")

        if was_running:
            steamvdf.start_steam(lambda m: self.ui.msg(m, "warn"))
        self.ui.msg("Done. Launch each game from Steam to confirm your "
                    "existing setup carried over.", "success")

    def cmd_mirror(self, dest_arg: str | None):
        """Make the SD card (or any path) a complete offline copy of the
        store: pre-fetch every remote payload, then incremental copy."""
        self.ui.header("💾 MIRROR STORE")
        self.ui.msg("Fetching any missing payloads so the mirror is complete...", "dim")
        for recipe in self.recipes:
            if recipe.remote_payloads:
                try:
                    fetch.ensure_remote_payloads(
                        recipe, log=lambda m: self.ui.msg(m, "dim"))
                except fetch.FetchError as e:
                    self.ui.msg(f"{recipe.name}: {e} — mirror will lack "
                                "this payload", "warn")

        dest = Path(dest_arg) if dest_arg else None
        if dest is None:
            cards = store.sd_card_roots()
            if cards:
                pick = self.ui.choose("Mirror to which card/drive?",
                                      [str(c) for c in cards])
                if not pick:
                    return
                dest = Path(pick[0]) / "steamos_restore" / "game_fixes"
            else:
                raw = self.ui.input("No SD card found — enter a destination path")
                if not raw:
                    return
                dest = Path(raw)
        if dest.resolve() == Path(self.store_root).resolve():
            self.ui.msg("The store already lives at that destination.", "warn")
            return
        self.ui.msg(f"Mirroring store -> {dest}", "dim")
        copied, fresh = store.mirror_store(self.store_root, dest,
                                           log=lambda m: self.ui.msg(m, "dim"))
        self.ui.msg(f"Mirror complete: {copied} file(s) copied, "
                    f"{fresh} already up to date.", "success")
        self.ui.msg("A reimage can now restore every game from this copy — "
                    "no internet needed.", "dim")

    def cmd_install(self):
        """Desktop + Game Mode launcher, GBM-style (controller-navigable)."""
        if os.name == "nt":
            self.ui.msg("Shortcut install is for the Deck — nothing to do on Windows.", "warn")
            return
        app_dir = Path(__file__).resolve().parent
        launcher = app_dir / "gfm-launch.sh"
        launcher.write_text(
            "#!/bin/bash\n"
            "# Game Fix Manager launcher — used by the desktop shortcut and the\n"
            "# Game Mode (non-Steam) entry. Konsole gives gum a real terminal.\n"
            f'cd "{app_dir}"\n'
            "git pull --ff-only 2>/dev/null   # freshen recipes when online\n"
            "exec python3 gfm.py\n", encoding="utf-8")
        launcher.chmod(0o755)

        desktop = Path.home() / "Desktop" / "Game Fix Manager.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Name=Game Fix Manager\n"
            "Comment=Re-apply game mods and fixes\n"
            f"Exec=konsole --hide-menubar --hide-tabbar -e {launcher}\n"
            "Icon=applications-games\n"
            "Type=Application\n"
            "Categories=Utility;\n", encoding="utf-8")
        desktop.chmod(0o755)
        subprocess.run(["gio", "set", str(desktop), "metadata::trusted", "true"],
                       capture_output=True)

        self.ui.msg("Desktop shortcut installed.", "success")
        self.ui.msg("", "dim")
        self.ui.msg("Controller support:", "info")
        self.ui.msg("• Desktop Mode: works now — Steam's desktop layout already maps", "dim")
        self.ui.msg("  D-pad to arrows, A to Enter, B to Esc.", "dim")
        self.ui.msg("• Game Mode: right-click the desktop shortcut > Add to Steam,", "dim")
        self.ui.msg("  then in its controller settings pick the official", "dim")
        self.ui.msg("  'Keyboard (WASD) and Mouse' template (or map D-pad to arrow", "dim")
        self.ui.msg("  keys, A to Enter, B to Esc).", "dim")

    def menu(self):
        while True:
            self.ui.header("🔧 GAME FIX MANAGER")
            self.ui.msg(f"📦 Store : {self.store_root or 'NOT FOUND'}", "dim")
            self.ui.msg(f"🎮 Steam : {self.steam_root or 'not found'}", "dim")
            self.ui.msg(f"🗂️  Games : {len(self.recipes)} recipe(s)", "dim")
            self.ui.msg("", "dim")
            choice = self.ui.choose("What would you like to do?", [
                "🔧 Apply Fixes",
                "📋 Status",
                "↩️  Revert a Game",
                "⬇️  Deploy Game from NAS (copy game to SD)",
                "🔍 Scan (SD + Steam + prefixes + capture saves/art)",
                "♻️  Save Restore (prefix backups + game saves)",
                "⚙️  Settings (NAS, runners, mirror, update, install)",
                "🛠  Advanced (individual steps)",
                "❌ Exit"])
            choice = choice[0] if choice else "❌ Exit"
            if choice.startswith("🔧"):
                self.cmd_apply([])
                self.ui.input("Press Enter to continue")
            elif choice.startswith("📋"):
                self.cmd_list()
                self.ui.input("Press Enter to continue")
            elif choice.startswith("↩"):
                self.cmd_revert([])
            elif choice.startswith("⬇"):
                self.cmd_deploy_game()
                self.ui.input("Press Enter to continue")
            elif choice.startswith("🔍"):
                self.cmd_scan_all()
                self.ui.input("Press Enter to continue")
            elif choice.startswith("♻"):
                self.cmd_save_restore()
                self.ui.input("Press Enter to continue")
            elif choice.startswith("⚙"):
                self.menu_settings()
            elif choice.startswith("🛠"):
                self.menu_advanced()
            else:
                return


# CLI command -> what it runs. Single source of truth: main() builds BOTH the
# parser's choices and the dispatch from this, so the two can't drift. They
# used to be two lists, and a command present in choices but missing from
# dispatch fell through to `dispatch.get(cmd, app.menu)` — silently opening the
# interactive menu instead of running the command.
COMMANDS = {
    "list": lambda app, a: app.cmd_list(),
    "apply": lambda app, a: app.cmd_apply(a.ids),
    "revert": lambda app, a: app.cmd_revert(a.ids),
    "install": lambda app, a: app.cmd_install(),
    "mirror": lambda app, a: app.cmd_mirror(a.dest),
    "update": lambda app, a: app.cmd_update(),
    "deploy": lambda app, a: app.cmd_deploy_game(),
    # bundles (the two headline menu entries)
    "scan": lambda app, a: app.cmd_scan_all(),
    "save-restore": lambda app, a: app.cmd_save_restore(),
    # the individual steps those bundles wrap
    "scan-sd": lambda app, a: app.cmd_scan_sd(),
    "scan-steam": lambda app, a: app.cmd_scan_steam(),
    "reconcile": lambda app, a: app.cmd_reconcile(),
    "capture": lambda app, a: app.cmd_capture(),
    "import-prefixes": lambda app, a: app.cmd_import_prefixes(),
    "restore-saves": lambda app, a: app.cmd_restore_saves(),
    # setup / diagnostics
    "setup-nas": lambda app, a: app.cmd_setup_nas(),
    "stage-runner": lambda app, a: app.cmd_stage_runner(),
    "test-crew": lambda app, a: app.cmd_test_crew(),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=list(COMMANDS),
                        help="omit for interactive menu")
    parser.add_argument("ids", nargs="*", help="recipe ids (e.g. la-noire)")
    parser.add_argument("--store", help="path to the fix store")
    parser.add_argument("--steam-root", help="override Steam root detection")
    parser.add_argument("--dest", help="mirror destination (default: SD card)")
    parser.add_argument("--local-payloads",
                        help="folder of local-only override payloads "
                             "(NAS mount, SD, …); also persisted to config")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    app = App(args)
    # Persist an explicitly-passed local-payloads dir so it sticks
    if getattr(args, "local_payloads", None) and app.local_payloads:
        app.cfg["local_payloads_dir"] = str(app.local_payloads)
        store.save_config(app.cfg)
    if app.store_root is None:
        print("No fix store found. Clone the repo, insert the SD card, or pass --store.",
              file=sys.stderr)
        sys.exit(1)

    handler = COMMANDS.get(args.command)
    try:
        app.menu() if handler is None else handler(app, args)
    except KeyboardInterrupt:
        app.log("interrupted by user")
    except Exception:
        app.log("UNCAUGHT EXCEPTION:\n" + traceback.format_exc())
        app.ui.msg("Something went wrong — details written to the log "
                   f"({sdmap.log_path()}).", "error")
        app.ui.msg(traceback.format_exc().splitlines()[-1], "dim")
        raise


if __name__ == "__main__":
    main()
