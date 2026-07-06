"""pak_edit step: replace/add members inside a zip-format game archive
(TFU2's LevelPacks/*.lp files are plain zips, as are many others).

Manifest form:
  { "type": "pak_edit", "archive": "LevelPacks/pak0.lp",
    "insert": [
      { "from": "payload/wet/maleAverage",
        "into": "Game/Disc/Characters/maleAverage" } ] }

"from" is a payload file or directory (tree structure preserved), "into" is
the member path prefix inside the archive.

Rewriting a multi-GB zip is unavoidable (zip members can't be edited in
place), so apply needs temp disk roughly the size of the archive and takes a
few minutes. The backup is NOT a full copy: replaced members' originals and
the list of added names go into <archive>.gfm-pakbak.zip (kilobytes), which
revert uses to rewrite the archive back to stock.
"""
from __future__ import annotations

import copy
import json
import os
import zipfile
from pathlib import Path

from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)

BACKUP_SUFFIX = ".gfm-pakbak.zip"
META_NAME = "gfm-added-members.json"


def _member_map(ctx: Ctx, inserts: list[dict]) -> dict[str, Path]:
    """archive member name -> payload file path"""
    mapping: dict[str, Path] = {}
    for ins in inserts:
        src = ctx.payload_path(ins["from"])
        into = ins["into"].strip("/")
        if src.is_file():
            mapping[f"{into}/{src.name}"] = src
        else:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(src).as_posix()
                    mapping[f"{into}/{rel}"] = f
    if not mapping:
        raise StepError("pak_edit: payload matched no files")
    return mapping


@register_step("pak_edit")
class PakEdit:
    def __init__(self, step: dict):
        self.archive = step["archive"]
        self.inserts = step["insert"]

    def _archive_path(self, ctx: Ctx) -> Path:
        p = ctx.game_dir / self.archive
        if not p.is_file():
            raise StepError(f"archive not found: {p}")
        return p

    def _status(self, ctx: Ctx) -> str:
        mapping = _member_map(ctx, self.inserts)
        with zipfile.ZipFile(self._archive_path(ctx)) as z:
            names = set(z.namelist())
            done = 0
            for name, src in mapping.items():
                if name in names and z.read(name) == src.read_bytes():
                    done += 1
        if done == len(mapping):
            return APPLIED
        return NOT_APPLIED if done == 0 else PARTIAL

    def apply(self, ctx: Ctx) -> None:
        mapping = _member_map(ctx, self.inserts)
        archive = self._archive_path(ctx)
        if self._status(ctx) == APPLIED:
            ctx.log(f"      = {archive.name} already contains the edit")
            return
        size_gb = archive.stat().st_size / (1 << 30)
        ctx.log(f"      ⚙ rewriting {archive.name} ({size_gb:.1f} GB — "
                f"needs that much free disk, takes a few minutes)")
        if ctx.dry_run:
            for name in mapping:
                ctx.log(f"        would set member {name}")
            return

        tmp = archive.with_suffix(archive.suffix + ".gfm-tmp")
        backup = archive.with_name(archive.name + BACKUP_SUFFIX)
        added: list[str] = []
        try:
            with zipfile.ZipFile(archive) as zin, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zout, \
                 zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zbak:
                existing = set(zin.namelist())
                for info in zin.infolist():
                    if info.filename in mapping:
                        # writestr mutates the ZipInfo it's given — the backup
                        # and the output MUST NOT share one object
                        zbak.writestr(copy.copy(info), zin.read(info.filename))
                        zout.writestr(info, mapping[info.filename].read_bytes())
                        ctx.log(f"        ~ replaced {info.filename}")
                    else:
                        zout.writestr(info, zin.read(info.filename))
                for name, src in mapping.items():
                    if name not in existing:
                        zout.writestr(name, src.read_bytes())
                        added.append(name)
                        ctx.log(f"        + added {name}")
                zbak.writestr(META_NAME, json.dumps(added))
        except BaseException:
            tmp.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
            raise
        os.replace(tmp, archive)

    def verify(self, ctx: Ctx) -> str:
        return self._status(ctx)

    def revert(self, ctx: Ctx) -> None:
        archive = self._archive_path(ctx)
        backup = archive.with_name(archive.name + BACKUP_SUFFIX)
        if not backup.is_file():
            ctx.log(f"      = no pak backup for {archive.name}, nothing to revert")
            return
        ctx.log(f"      ⚙ rewriting {archive.name} back to stock")
        if ctx.dry_run:
            return
        with zipfile.ZipFile(backup) as zbak:
            added = set(json.loads(zbak.read(META_NAME)))
            originals = {i.filename: i for i in zbak.infolist()
                         if i.filename != META_NAME}
            tmp = archive.with_suffix(archive.suffix + ".gfm-tmp")
            try:
                with zipfile.ZipFile(archive) as zin, \
                     zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                                     allowZip64=True) as zout:
                    for info in zin.infolist():
                        if info.filename in added:
                            continue  # we introduced it — drop
                        if info.filename in originals:
                            zout.writestr(originals[info.filename],
                                          zbak.read(info.filename))
                        else:
                            zout.writestr(info, zin.read(info.filename))
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        os.replace(tmp, archive)
        backup.unlink()
