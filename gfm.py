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
import os
import subprocess
import sys
from pathlib import Path

from core import (detect, engine, fetch, manifest, prefixes, shortcutsvdf,
                  steamvdf, store)
from ui import get_ui

STATUS_ICON = {engine.APPLIED: "✅", engine.NOT_APPLIED: "☐ ",
               engine.PARTIAL: "🟡", "not_found": "❓"}
STATUS_TEXT = {engine.APPLIED: "applied", engine.NOT_APPLIED: "not applied",
               engine.PARTIAL: "PARTIAL — some steps missing", "not_found": "game not found"}


class App:
    def __init__(self, args):
        self.args = args
        self.ui = get_ui()
        self.cfg = store.load_config()
        self.store_root = store.resolve_store(args.store, self.cfg)
        self.steam_root = detect.find_steam_root(args.steam_root)
        self.pending_vdf_writes: list = []
        self.recipes = manifest.load_all(self.store_root) if self.store_root else []

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
                             steam_root=self.steam_root)
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
                         steam_root=self.steam_root)
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
        self.ui.msg(f"Launch options queued for: {names}", "warn")
        was_running = steamvdf.steam_running()
        if was_running:
            if not self.ui.confirm(
                    "Steam must close briefly to write launch options "
                    "(controller drops out until it's back). Do it now?"):
                self.ui.msg("Skipped — re-run apply later to set launch options.", "warn")
                return
            steamvdf.close_steam(lambda m: self.ui.msg(m, "warn"))
        total = 0
        for w in writes:
            if w.get("kind") == "shortcut":
                n = shortcutsvdf.set_launch_options(self.steam_root,
                                                    w["names"], w["value"])
            else:
                n = steamvdf.set_launch_options(self.steam_root,
                                                w["appid"], w["value"])
            self.ui.msg(f'  {w["game"]}: LaunchOptions = {w["value"] or "(cleared)"} '
                        f'({n} user file(s))', "dim")
            total += n
        if was_running:
            steamvdf.start_steam(lambda m: self.ui.msg(m, "warn"))
        self.ui.msg(f"Launch options written ({total} file update(s)).", "success")

    # --- commands ---

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

    def cmd_apply(self, ids: list[str]):
        targets = self._pick(ids, "Select games to fix")
        for recipe in targets:
            game_dir = self.game_dir_for(recipe, interactive=True)
            if game_dir is None:
                self.ui.msg(f"Skipping {recipe.name} — not located.", "warn")
                continue
            if recipe.remote_payloads:
                if self.args.dry_run:
                    missing = [i for i in recipe.remote_payloads
                               if not (recipe.dir / i["path"]).is_file()
                               or (i.get("extract_to")
                                   and not (recipe.dir / i["extract_to"]).is_dir())]
                    if missing:
                        for i in missing:
                            self.ui.msg(f'DRY RUN would download {i["url"]} '
                                        f'({i.get("size", 0) // (1 << 20)} MB)', "dim")
                        self.ui.msg(f"{recipe.name}: rest of the plan needs the "
                                    "payload — run a real apply to fetch it.", "warn")
                        continue
                else:
                    try:
                        fetch.ensure_remote_payloads(
                            recipe, log=lambda m: self.ui.msg(m, "dim"))
                    except fetch.FetchError as e:
                        self.ui.msg(f"{recipe.name}: {e}", "error")
                        continue
            ok = self.run_engine(recipe, game_dir, engine.apply_recipe)
            if ok and recipe.post_apply_message:
                self.ui.msg("── Manual step needed " + "─" * 20, "warn")
                for line in recipe.post_apply_message.splitlines():
                    self.ui.msg(line, "warn")
        self.flush_vdf_writes()

    def cmd_revert(self, ids: list[str]):
        targets = self._pick(ids, "Select games to revert")
        for recipe in targets:
            game_dir = self.game_dir_for(recipe, interactive=True)
            if game_dir is None:
                continue
            if self.ui.confirm(f"Revert fixes for {recipe.name}?", danger=True):
                self.run_engine(recipe, game_dir, engine.revert_recipe)
        self.flush_vdf_writes()

    def _pick(self, ids: list[str], prompt: str):
        if ids:
            by_id = {r.id: r for r in self.recipes}
            missing = [i for i in ids if i not in by_id]
            if missing:
                self.ui.msg(f"Unknown recipe id(s): {', '.join(missing)}", "error")
                sys.exit(1)
            return [by_id[i] for i in ids]
        options, by_label = [], {}
        for recipe in self.recipes:
            game_dir = self.game_dir_for(recipe, interactive=False)
            label = self.status_line(recipe, game_dir)
            options.append(label)
            by_label[label] = recipe
        self.ui.msg("TAB to select  •  ENTER to confirm  •  ESC to cancel", "dim")
        picked = self.ui.choose(prompt, options, multi=True)
        return [by_label[p] for p in picked]

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
        self.ui.msg(f"GBM CSV: {len(gbm_map)} known appid→name mappings", "dim")

        # shortcut appids we're processing (never adopt one of THESE as target)
        all_shortcut_ids: set[int] = set()
        for r in self.recipes:
            try:
                all_shortcut_ids.update(
                    shortcutsvdf.find_appids(self.steam_root, r.all_names))
            except shortcutsvdf.ShortcutsError:
                pass

        plan: list[tuple] = []  # (recipe, current_appid, target_appid, signal)
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
            cands = prefixes.find_candidates(self.steam_root, recipe, gbm_map, excl)
            if not cands:
                self.ui.msg(f"  {recipe.name}: no candidate prefix found", "dim")
                continue
            if len(cands) == 1:
                target, signal = cands[0]
                self.ui.msg(f"  {recipe.name}: candidate compatdata/{target.name} "
                            f"(matched by {signal})", "info")
            else:
                self.ui.msg(f"  {recipe.name}: multiple candidates, please pick",
                            "warn")
                picked = self.ui.choose(
                    f"Which prefix is {recipe.name}?",
                    [f"compatdata/{p.name}  [matched by {s}]" for p, s in cands])
                if not picked:
                    continue
                pick_line = picked[0]
                target_id = pick_line.split("/", 1)[1].split(" ", 1)[0]
                target = next(p for p, _ in cands if p.name == target_id)
                signal = next(s for p, s in cands if p.name == target_id)
            plan.append((recipe, current, int(target.name), signal))

        if not plan:
            self.ui.msg("Nothing to reconcile — every shortcut already has a "
                        "live prefix (or no candidate exists).", "success")
            return

        self.ui.msg("", "dim")
        self.ui.msg("Plan:", "info")
        for recipe, cur, tgt, sig in plan:
            self.ui.msg(f"  {recipe.name}: shortcut appid {cur} → {tgt}  "
                        f"({sig})", "dim")
        if not self.ui.confirm(
                "Rewrite shortcut appids to match the existing prefixes? "
                "Steam will close briefly.", danger=True):
            return

        was_running = steamvdf.steam_running()
        if was_running:
            steamvdf.close_steam(lambda m: self.ui.msg(m, "warn"))

        for recipe, cur, tgt, _sig in plan:
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
                "🔗 Reconcile Prefixes (adopt existing compatdata)",
                "💾 Mirror Store (offline copy on SD/NAS)",
                "🖥️  Install Shortcut (Desktop + Game Mode)",
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
            elif choice.startswith("🔗"):
                self.cmd_reconcile()
                self.ui.input("Press Enter to continue")
            elif choice.startswith("💾"):
                self.cmd_mirror(None)
                self.ui.input("Press Enter to continue")
            elif choice.startswith("🖥"):
                self.cmd_install()
                self.ui.input("Press Enter to continue")
            else:
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?",
                        choices=["list", "apply", "revert", "install", "mirror",
                                 "reconcile"],
                        help="omit for interactive menu")
    parser.add_argument("ids", nargs="*", help="recipe ids (e.g. la-noire)")
    parser.add_argument("--store", help="path to the fix store")
    parser.add_argument("--steam-root", help="override Steam root detection")
    parser.add_argument("--dest", help="mirror destination (default: SD card)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    app = App(args)
    if app.store_root is None:
        print("No fix store found. Clone the repo, insert the SD card, or pass --store.",
              file=sys.stderr)
        sys.exit(1)

    if args.command == "list":
        app.cmd_list()
    elif args.command == "apply":
        app.cmd_apply(args.ids)
    elif args.command == "revert":
        app.cmd_revert(args.ids)
    elif args.command == "install":
        app.cmd_install()
    elif args.command == "mirror":
        app.cmd_mirror(args.dest)
    elif args.command == "reconcile":
        app.cmd_reconcile()
    else:
        app.menu()


if __name__ == "__main__":
    main()
