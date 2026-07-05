"""copy_files step: copy a payload file/tree into the game (or any) directory.

Manifest form:
  { "type": "copy_files", "from": "payload/mod", "to": "{game_dir}",
    "backup_originals": true }

"from" may be a file or a directory (copied recursively, structure preserved).
Overwritten originals are kept alongside as <name>.gfm-orig — first write wins,
so re-applying an updated payload never clobbers the true original.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..engine import (APPLIED, BACKUP_SUFFIX, NOT_APPLIED, PARTIAL, Ctx,
                      register_step)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    if not (a.is_file() and b.is_file()):
        return False
    if a.stat().st_size != b.stat().st_size:
        return False
    return file_hash(a) == file_hash(b)


def copy_one(src: Path, dst: Path, backup: bool, ctx: Ctx) -> None:
    if _same_file(src, dst):
        ctx.log(f"      = {dst.name} (already in place)")
        return
    if backup and dst.is_file() and not dst.with_name(dst.name + BACKUP_SUFFIX).exists():
        ctx.log(f"      ~ backing up original -> {dst.name}{BACKUP_SUFFIX}")
        if not ctx.dry_run:
            shutil.copy2(dst, dst.with_name(dst.name + BACKUP_SUFFIX))
    ctx.log(f"      + {dst}")
    if not ctx.dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def revert_one(src: Path, dst: Path, ctx: Ctx) -> None:
    backup = dst.with_name(dst.name + BACKUP_SUFFIX)
    if backup.is_file():
        ctx.log(f"      ~ restoring original {dst.name}")
        if not ctx.dry_run:
            shutil.copy2(backup, dst)
            backup.unlink()
    elif _same_file(src, dst):
        # File we introduced (nothing was there before) — remove it.
        ctx.log(f"      - {dst}")
        if not ctx.dry_run:
            dst.unlink()


def iter_pairs(src_root: Path, dst_root: Path):
    """Yield (payload_file, target_file) pairs for a file or tree."""
    if src_root.is_file():
        yield src_root, dst_root / src_root.name if dst_root.is_dir() else dst_root
        return
    for src in sorted(src_root.rglob("*")):
        if src.is_file():
            yield src, dst_root / src.relative_to(src_root)


@register_step("copy_files")
class CopyFiles:
    def __init__(self, step: dict):
        self.src = step["from"]
        self.dst = step["to"]
        self.backup = step.get("backup_originals", True)

    def _pairs(self, ctx: Ctx):
        return list(iter_pairs(ctx.payload_path(self.src), ctx.resolve_target(self.dst)))

    def apply(self, ctx: Ctx) -> None:
        for src, dst in self._pairs(ctx):
            copy_one(src, dst, self.backup, ctx)

    def verify(self, ctx: Ctx) -> str:
        pairs = self._pairs(ctx)
        done = sum(1 for src, dst in pairs if _same_file(src, dst))
        if done == len(pairs):
            return APPLIED
        return NOT_APPLIED if done == 0 else PARTIAL

    def revert(self, ctx: Ctx) -> None:
        for src, dst in self._pairs(ctx):
            revert_one(src, dst, ctx)
