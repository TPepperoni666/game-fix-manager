"""swap_exe step: replace one game file with a patched copy from the payload.

Manifest form:
  { "type": "swap_exe", "payload": "payload/LaNoire.exe", "target": "LaNoire.exe" }

"target" is relative to the game dir. The original is always kept as
<name>.gfm-orig — this step exists so exe-swap recipes read as intent,
even though it shares its machinery with copy_files.
"""
from __future__ import annotations

from ..engine import APPLIED, NOT_APPLIED, Ctx, register_step
from .copy_files import _same_file, copy_one, revert_one


@register_step("swap_exe")
class SwapExe:
    def __init__(self, step: dict):
        self.payload = step["payload"]
        self.target = step["target"]

    def apply(self, ctx: Ctx) -> None:
        src = ctx.payload_path(self.payload)
        dst = ctx.game_dir / self.target
        if not dst.is_file() and not ctx.dry_run:
            ctx.log(f"      ! warning: {dst} does not exist yet (game not installed?)")
        copy_one(src, dst, backup=True, ctx=ctx)

    def verify(self, ctx: Ctx) -> str:
        src = ctx.payload_path(self.payload)
        dst = ctx.game_dir / self.target
        return APPLIED if _same_file(src, dst) else NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        revert_one(ctx.payload_path(self.payload), ctx.game_dir / self.target, ctx)
