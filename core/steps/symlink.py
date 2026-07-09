"""symlink step: point one path at another so both share a single real
directory — used to keep a save in sync across two exes that look for it in
different folders.

Manifest form:
  { "type": "symlink",
    "link": "{prefix}/drive_c/users/steamuser/Documents/Ubisoft/Driver San Francisco/SKIDROW",
    "target": "TPepperoni666",
    "optional": true }

Motivating case: a cracked exe writes its save under a crack-group folder
(SKIDROW), while the legit Uplay build writes under the account-name folder
(the Ubisoft username). Symlinking one to the other means BOTH paths resolve
to a single real save folder, so progress is shared no matter which exe you
launch — no copy daemon, no last-write-wins conflicts. Linking at the
DIRECTORY level (not the individual file) survives the game's atomic
save-writes (it replaces autosave.dat, never the folder).

"link" is expanded with the usual target templates ({prefix}, {game_dir}, ~).
"target" is taken relative to the link's parent directory when it isn't
absolute, so the link stays portable (no machine-specific path baked into the
prefix). The target must already exist — mark the step "optional" so it's a
no-op until the game has run once and created the real save folder, then
re-apply.

On apply, if a real (non-symlink) directory already sits where the link goes,
its contents are merged into the target first (existing target files win),
then it's replaced by the link — so an existing crack save is never lost.
Revert removes only a symlink we created; it never touches a real folder.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


@register_step("symlink")
class Symlink:
    def __init__(self, step: dict):
        self.link = step["link"]
        self.target = step["target"]

    def _paths(self, ctx: Ctx) -> tuple[Path, Path]:
        """(link path, absolute target path). resolve_target may raise
        StepError if a {prefix} template can't resolve yet."""
        link = ctx.resolve_target(self.link)
        tgt_abs = (Path(self.target) if os.path.isabs(self.target)
                   else link.parent / self.target)
        return link, tgt_abs

    def apply(self, ctx: Ctx) -> None:
        link, tgt_abs = self._paths(ctx)
        if not tgt_abs.exists():
            raise StepError(
                f"symlink target does not exist yet: {tgt_abs} — run the game "
                "once so the save folder is created, then re-apply this fix")
        if link.is_symlink():
            try:
                if link.resolve() == tgt_abs.resolve():
                    ctx.log(f"      = {link.name} -> {self.target} (already linked)")
                    return
            except OSError:
                pass
            ctx.log(f"      ~ replacing stale symlink {link.name}")
            if not ctx.dry_run:
                link.unlink()
        elif link.exists():
            # A real folder/file is here — fold it into the target so no save
            # is lost, then remove it to make room for the link.
            ctx.log(f"      ~ merging existing {link.name}/ into {tgt_abs.name}/ then linking")
            if not ctx.dry_run:
                if link.is_dir():
                    for item in link.iterdir():
                        dest = tgt_abs / item.name
                        if not dest.exists():
                            shutil.move(str(item), str(dest))
                    shutil.rmtree(link)
                else:
                    link.unlink()
        ctx.log(f"      + {link} -> {self.target}")
        if not ctx.dry_run:
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.target, link, target_is_directory=True)

    def verify(self, ctx: Ctx) -> str:
        try:
            link, tgt_abs = self._paths(ctx)
        except StepError:
            return NOT_APPLIED
        if link.is_symlink():
            try:
                return APPLIED if link.resolve() == tgt_abs.resolve() else NOT_APPLIED
            except OSError:
                return NOT_APPLIED
        return NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        link, _tgt = self._paths(ctx)
        if link.is_symlink():
            ctx.log(f"      - unlink {link}")
            if not ctx.dry_run:
                link.unlink()
