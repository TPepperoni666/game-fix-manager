"""proton_version step: force a specific Proton / compatibility tool for the
game, the same as picking one in Steam > Properties > Compatibility.

Manifest form:
  { "type": "proton_version", "tool": "proton_63" }          # Proton 6.3
  { "type": "proton_version", "tool": "GE-Proton10-15" }     # a GE build

Common official tool names (the internal id, not the display name):
  proton_experimental, proton_9, proton_8, proton_7, proton_63 (6.3),
  proton_513 (5.13), proton_5, proton_411 (4.11), proton_42 (4.2),
  proton_316 (3.16), proton_37 (3.7), proton_hotfix.
GE-Proton / custom tools use their folder name in compatibilitytools.d
(e.g. GE-Proton10-15).

Writes Steam's CompatToolMapping in config.vdf — queued and flushed behind
the one Steam-close batch, like launch_options. Works for Steam appids and
non-Steam shortcuts alike (both keyed by appid).

NOTE: the chosen Proton must be INSTALLED. Steam ships recent versions; old
ones (3.x/4.x/5.x) install from the Library's Proton tools, GE builds via
ProtonUp-Qt. If it isn't installed, Steam silently falls back — so recipes
using an old/GE Proton should say so in post_apply_message.
"""
from __future__ import annotations

from .. import shortcutsvdf, steamvdf
from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


@register_step("proton_version")
class ProtonVersion:
    def __init__(self, step: dict):
        self.tool = step["tool"]
        self.priority = str(step.get("priority", "250"))

    def _appid(self, ctx: Ctx) -> int | None:
        """Non-Steam shortcut appid wins over the recipe's steam_appid."""
        try:
            ids = shortcutsvdf.find_appids(ctx.steam_root, ctx.recipe.all_names)
        except shortcutsvdf.ShortcutsError:
            ids = []
        if ids:
            return ids[0]
        return ctx.recipe.steam_appid

    def apply(self, ctx: Ctx) -> None:
        if ctx.steam_root is None:
            raise StepError("Steam root not found — cannot set Proton version")
        appid = self._appid(ctx)
        if not appid:
            raise StepError("no appid yet — add the game to Steam first, "
                            "then re-apply")
        current = steamvdf.get_compat_tool(ctx.steam_root, appid)
        if current and current.get("name") == self.tool:
            ctx.log(f"      = Proton already forced to {self.tool}")
            return
        ctx.log(f"      ⏲ queued Proton = {self.tool}")
        if not ctx.dry_run:
            ctx.deferred_vdf_writes.append(
                {"kind": "compat", "appid": appid, "tool": self.tool,
                 "priority": self.priority, "game": ctx.recipe.name})

    def verify(self, ctx: Ctx) -> str:
        if ctx.steam_root is None:
            return NOT_APPLIED
        appid = self._appid(ctx)
        if not appid:
            return NOT_APPLIED
        current = steamvdf.get_compat_tool(ctx.steam_root, appid)
        return APPLIED if (current and current.get("name") == self.tool) \
            else NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        if ctx.steam_root is None:
            return
        appid = self._appid(ctx)
        if not appid:
            return
        current = steamvdf.get_compat_tool(ctx.steam_root, appid)
        if current and current.get("name") == self.tool:
            ctx.log("      ⏲ queued Proton reset (back to Steam default)")
            if not ctx.dry_run:
                ctx.deferred_vdf_writes.append(
                    {"kind": "compat", "appid": appid, "tool": "",
                     "priority": self.priority, "game": ctx.recipe.name})
