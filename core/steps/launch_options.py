"""launch_options step: set a game's Steam launch options.

Manifest form:
  { "type": "launch_options", "value": "{home}/gfm-wrappers/twfc.sh %command%" }

"{home}" is expanded at apply time (Steam does not expand ~ in launch
options, so recipes must not rely on it).

Steam only persists localconfig.vdf edits made while it is CLOSED, so this
step does not write directly: it queues the write on ctx.deferred_vdf_writes
and the caller (gfm.py) batches every queued write behind a single
close-Steam -> write -> restart-Steam dance at the end of the apply run.

Steam games only (needs a steam_appid). Non-Steam shortcuts need binary
shortcuts.vdf editing — future work.
"""
from __future__ import annotations

from pathlib import Path

from .. import steamvdf
from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


def _expand(value: str) -> str:
    return value.replace("{home}", str(Path.home()))


@register_step("launch_options")
class LaunchOptions:
    def __init__(self, step: dict):
        self.value = step["value"]

    def _appid(self, ctx: Ctx) -> int:
        appid = ctx.recipe.steam_appid
        if not appid:
            raise StepError("launch_options requires a steam_appid "
                            "(non-Steam shortcuts not supported yet)")
        return appid

    def apply(self, ctx: Ctx) -> None:
        appid = self._appid(ctx)
        value = _expand(self.value)
        if ctx.steam_root is None:
            raise StepError("Steam root not found — cannot set launch options")
        current = steamvdf.get_launch_options(ctx.steam_root, appid)
        if current and all(v == value for v in current.values()):
            ctx.log(f"      = launch options already set")
            return
        ctx.log(f'      ⏲ queued launch options: {value}')
        if not ctx.dry_run:
            ctx.deferred_vdf_writes.append(
                {"appid": appid, "value": value, "game": ctx.recipe.name})

    def verify(self, ctx: Ctx) -> str:
        if ctx.steam_root is None:
            return NOT_APPLIED
        current = steamvdf.get_launch_options(ctx.steam_root, self._appid(ctx))
        value = _expand(self.value)
        if current and all(v == value for v in current.values()):
            return APPLIED
        return NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        appid = self._appid(ctx)
        if ctx.steam_root is None:
            return
        current = steamvdf.get_launch_options(ctx.steam_root, appid)
        if current and any(v == _expand(self.value) for v in current.values()):
            ctx.log("      ⏲ queued launch options reset")
            if not ctx.dry_run:
                ctx.deferred_vdf_writes.append(
                    {"appid": appid, "value": "", "game": ctx.recipe.name})
