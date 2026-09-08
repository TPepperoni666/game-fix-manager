"""make_executable step: set the +x bit on native Linux binaries.

Manifest form:
  { "type": "make_executable",
    "paths": ["{game_dir}/WheelWizard_Linux",
              "{game_dir}/WiiCompiled-Setup-x86_64.AppImage"] }

Why this has to be a step rather than something copy_files does silently:
the executable bit does not survive the journey. These files arrive from a
Windows workstation, cross an SMB share on the NAS, and land on the Deck —
and neither NTFS nor a default SMB mount carries a POSIX mode, so a native
binary that was executable at the source is 0644 by the time it is deployed.
It then fails to launch with 'Permission denied', which reads like a broken
download rather than a lost metadata bit.

Applies only on Linux; on Windows it is a no-op that verifies as applied,
because there is no bit to set and a recipe must not report itself broken on
a platform where the question is meaningless.

A glob is allowed ("{game_dir}/bin/*.AppImage"), matched against the parent.
Entries that match nothing are skipped and logged — the same contract as
save_paths, so listing a path that only exists on one platform costs nothing.

Only the bits that are already readable are made executable (r-bit -> x-bit),
which is what chmod +x does: a file readable by everyone becomes executable by
everyone, one readable only by its owner stays that way.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step

_GLOB_CHARS = "*?["


@register_step("make_executable")
class MakeExecutable:
    def __init__(self, step: dict):
        paths = step.get("paths") or ([step["path"]] if "path" in step else [])
        if not paths:
            raise StepError("make_executable needs 'paths' (or 'path')")
        self.paths = paths

    def _matches(self, ctx: Ctx) -> list[Path]:
        out: list[Path] = []
        for template in self.paths:
            try:
                resolved = ctx.resolve_target(template)
            except StepError:
                continue
            try:
                if any(c in resolved.name for c in _GLOB_CHARS):
                    out.extend(sorted(resolved.parent.glob(resolved.name)))
                elif resolved.exists():
                    out.append(resolved)
            except OSError:
                continue
        return out

    @staticmethod
    def _wanted(mode: int) -> int:
        """chmod +x: mirror each read bit into the matching execute bit."""
        return mode | ((mode & 0o444) >> 2)

    def apply(self, ctx: Ctx) -> None:
        if os.name == "nt":
            ctx.log("      = not applicable on Windows")
            return
        hits = self._matches(ctx)
        if not hits:
            ctx.log("      ? nothing matched — is the game deployed yet?")
            return
        for p in hits:
            try:
                mode = stat.S_IMODE(p.stat().st_mode)
            except OSError as e:
                ctx.log(f"      ! {p}: {e}")
                continue
            want = self._wanted(mode)
            if want == mode:
                ctx.log(f"      = {p.name} already executable")
                continue
            ctx.log(f"      + {p.name}  {mode:04o} -> {want:04o}")
            if not ctx.dry_run:
                try:
                    p.chmod(want)
                except OSError as e:
                    ctx.log(f"      ! {p}: {e}")

    def verify(self, ctx: Ctx) -> str:
        if os.name == "nt":
            return APPLIED
        hits = self._matches(ctx)
        if not hits:
            return NOT_APPLIED
        for p in hits:
            try:
                if not stat.S_IMODE(p.stat().st_mode) & 0o111:
                    return NOT_APPLIED
            except OSError:
                return NOT_APPLIED
        return APPLIED

    def revert(self, ctx: Ctx) -> None:
        # Deliberately a no-op. Reverting would strip the bit and leave a game
        # that cannot start; nothing about "undo this fix" implies breaking the
        # launcher, and the bit is not ours to take away — the file may well
        # have arrived executable on a filesystem that kept it.
        ctx.log("      = leaving the executable bit in place")
