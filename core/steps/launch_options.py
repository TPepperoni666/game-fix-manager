"""launch_options step: set a game's Steam launch options.

Manifest form:
  { "type": "launch_options", "value": "{home}/gfm-wrappers/twfc.sh %command%" }

"{home}" is expanded at apply time (Steam does not expand ~ in launch
options, so recipes must not rely on it).

Steam only persists localconfig.vdf edits made while it is CLOSED, so this
step does not write directly: it queues the write on ctx.deferred_vdf_writes
and the caller (gfm.py) batches every queued write behind a single
close-Steam -> write -> restart-Steam dance at the end of the apply run.

Targets both install kinds: a non-Steam shortcut whose AppName matches the
recipe wins (binary shortcuts.vdf); otherwise the recipe's steam_appid is
written to text localconfig.vdf.
"""
from __future__ import annotations

from pathlib import Path

from .. import shortcutsvdf, steamvdf
from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


def _expand(value: str) -> str:
    return value.replace("{home}", str(Path.home()))


@register_step("launch_options")
class LaunchOptions:
    def __init__(self, step: dict):
        self.value = step["value"]

    def _current(self, ctx: Ctx) -> tuple[str, dict[str, str]]:
        """(target kind, current values). Shortcut match wins over appid."""
        if ctx.steam_root is None:
            raise StepError("Steam root not found — cannot set launch options")
        try:
            hits = shortcutsvdf.get_launch_options(ctx.steam_root,
                                                   ctx.recipe.all_names)
        except shortcutsvdf.ShortcutsError:
            hits = {}
        if hits:
            return "shortcut", hits
        if ctx.recipe.steam_appid:
            return "steam", steamvdf.get_launch_options(
                ctx.steam_root, ctx.recipe.steam_appid)
        raise StepError("no matching non-Steam shortcut and no steam_appid — "
                        "add the game to Steam first, then re-apply")

    def _queue(self, ctx: Ctx, kind: str, value: str) -> None:
        write = {"kind": kind, "value": value, "game": ctx.recipe.name}
        if kind == "shortcut":
            write["names"] = ctx.recipe.all_names
        else:
            write["appid"] = ctx.recipe.steam_appid
        ctx.deferred_vdf_writes.append(write)

    def apply(self, ctx: Ctx) -> None:
        value = _expand(self.value)
        kind, current = self._current(ctx)
        if current and all(v == value for v in current.values()):
            ctx.log("      = launch options already set")
            return
        ctx.log(f'      ⏲ queued launch options ({kind}): {value}')
        if not ctx.dry_run:
            self._queue(ctx, kind, value)

    def verify(self, ctx: Ctx) -> str:
        try:
            _, current = self._current(ctx)
        except StepError:
            return NOT_APPLIED
        value = _expand(self.value)
        if current and all(v == value for v in current.values()):
            return APPLIED
        return NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        try:
            kind, current = self._current(ctx)
        except StepError:
            return
        if current and any(v == _expand(self.value) for v in current.values()):
            ctx.log("      ⏲ queued launch options reset")
            if not ctx.dry_run:
                self._queue(ctx, kind, "")
