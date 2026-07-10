"""steam_shortcut step: create (or update) a non-Steam Steam shortcut for the
game, so the user doesn't have to add it by hand.

Manifest form:
  { "type": "steam_shortcut",
    "exe": "Deadpool.exe",          # optional; defaults to the first marker_file
    "start_dir": "{game_dir}",       # optional; defaults to the game dir
    "launch_options": "%command%",   # optional
    "proton": "proton_9",            # optional: also force this compat tool
    "appid": 3880897897 }            # optional; else looked up in prefix_registry.json

Everything is keyed off the game's install dir (detected) + the gospel appid.
The gospel appid comes from store/prefix_registry.json (by recipe id) unless
given explicitly — forcing it means Steam uses that same id for the game's
compatdata prefix, so a later prefix restore lines up (and re-adding the game
never spawns a random new id).

Because we KNOW the appid, this step writes the shortcut, its LaunchOptions and
(optionally) its Proton mapping all against that id — no "add it to Steam
first" chicken-and-egg. Writes are queued and flushed behind the one Steam
close, exactly like launch_options / proton_version.
"""
from __future__ import annotations

import json

from .. import shortcutsvdf
from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


@register_step("steam_shortcut")
class SteamShortcut:
    def __init__(self, step: dict):
        self.exe = step.get("exe")
        self.start_dir = step.get("start_dir", "{game_dir}")
        self.launch_options = step.get("launch_options", "")
        self.proton = step.get("proton")
        self.appid = step.get("appid")
        # Restore captured custom shortcut art (from `gfm capture`) if any
        # was saved for this game. Default on — a no-op when none exists.
        self.restore_art = step.get("restore_art", True)

    def _appid(self, ctx: Ctx) -> int | None:
        if self.appid is not None:
            return int(self.appid)
        reg = ctx.recipe.dir.parent.parent / "prefix_registry.json"
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for e in data.get("entries", []):
            if e.get("recipe_id") == ctx.recipe.id and e.get("appid") is not None:
                return int(e["appid"])
        return None

    def _exe(self, ctx: Ctx) -> str:
        rel = self.exe or next(iter(ctx.recipe.detect.get("marker_files", [])), None)
        if not rel:
            raise StepError("steam_shortcut: no 'exe' given and no marker_files "
                            "to derive one from")
        p = ctx.resolve_target(rel) if "{" in rel else (ctx.game_dir / rel)
        return str(p)

    def apply(self, ctx: Ctx) -> None:
        if ctx.steam_root is None:
            raise StepError("Steam root not found — cannot create a shortcut")
        exe = self._exe(ctx)
        start = str(ctx.resolve_target(self.start_dir))
        appid = self._appid(ctx)
        if appid is None:
            ctx.log("      ! no gospel appid for this game — Steam will assign a "
                    "random one, so a restored prefix won't line up")
        ctx.deferred_vdf_writes.append({
            "kind": "add_shortcut", "game": ctx.recipe.name,
            "appname": ctx.recipe.name, "aliases": list(ctx.recipe.aliases),
            "exe": exe, "start_dir": start,
            "launch_options": self.launch_options, "appid": appid,
        })
        if self.proton and appid is not None:
            ctx.deferred_vdf_writes.append({
                "kind": "compat", "game": ctx.recipe.name,
                "appid": appid, "tool": self.proton, "priority": "250",
            })
        if self.restore_art and appid is not None and ctx.local_payloads_dir is not None:
            art_src = ctx.local_payloads_dir / ctx.recipe.id / "artwork"
            try:
                has_art = art_src.is_dir() and any(art_src.iterdir())
            except OSError:
                has_art = False
            if has_art:
                ctx.deferred_vdf_writes.append({
                    "kind": "restore_art", "game": ctx.recipe.name,
                    "appid": appid, "src": str(art_src),
                })
        ctx.log(f"      + Steam shortcut queued: {ctx.recipe.name} -> {exe}"
                + (f"  (appid {appid})" if appid is not None else ""))

    def verify(self, ctx: Ctx) -> str:
        if ctx.steam_root is None:
            return NOT_APPLIED
        try:
            ids = shortcutsvdf.find_appids(ctx.steam_root, ctx.recipe.all_names)
        except shortcutsvdf.ShortcutsError:
            ids = []
        want = self._appid(ctx)
        if want is not None:
            return APPLIED if want in ids else NOT_APPLIED
        return APPLIED if ids else NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        # Removing a shortcut the user may have since customised is intrusive;
        # leave it in place and let them delete it from Steam if they want.
        ctx.log("      (steam_shortcut) leaving the Steam shortcut in place — "
                "delete it from Steam manually if you want it gone")
