"""remove_files step: take game files out of play, reversibly.

Manifest form:
  { "type": "remove_files",
    "targets": ["Game/Disc/FMV/Win32/LEC", "Game/Disc/FMV/Win32/LEC_DE"] }

Nothing is deleted — each target is renamed to <name>.gfm-orig (the same
backup convention every other step uses), so revert simply renames back.
Classic use: skipping unskippable intro videos.
"""
from __future__ import annotations

from ..engine import (APPLIED, BACKUP_SUFFIX, NOT_APPLIED, PARTIAL, Ctx,
                      register_step)


@register_step("remove_files")
class RemoveFiles:
    def __init__(self, step: dict):
        self.targets = step["targets"]

    def apply(self, ctx: Ctx) -> None:
        for t in self.targets:
            p = ctx.game_dir / t
            backup = p.with_name(p.name + BACKUP_SUFFIX)
            if not p.exists():
                ctx.log(f"      = {p.name} already out of the way")
                continue
            ctx.log(f"      - {t} -> {p.name}{BACKUP_SUFFIX}")
            if not ctx.dry_run:
                if backup.exists():
                    backup.unlink()  # stale backup from an interrupted run
                p.rename(backup)

    def verify(self, ctx: Ctx) -> str:
        gone = sum(1 for t in self.targets if not (ctx.game_dir / t).exists())
        if gone == len(self.targets):
            return APPLIED
        return NOT_APPLIED if gone == 0 else PARTIAL

    def revert(self, ctx: Ctx) -> None:
        for t in self.targets:
            p = ctx.game_dir / t
            backup = p.with_name(p.name + BACKUP_SUFFIX)
            if backup.exists() and not p.exists():
                ctx.log(f"      ~ restoring {t}")
                if not ctx.dry_run:
                    backup.rename(p)
