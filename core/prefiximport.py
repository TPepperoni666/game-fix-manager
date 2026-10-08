"""Prefix import: put prefixes backed up by the older Linux Prefix Manager
back into Steam's compatdata after a reimage.

Each backup is a full rsync of steamapps/compatdata/<appid>/, laid out as:
  <SD>/steamos_restore/prefix_backups/<safe_name>/<appid>/   (Deck/SteamOS)
  <SD>/bazzite_restore/prefix backups/<safe_name>/<appid>/   (Bazzite — note
                                                              the SPACE)
There is no json manifest — the folder tree IS the manifest: the parent
folder is the name, the leaf folder name is the appid.

Restoring = copy <appid>/ back into the right library's compatdata/<appid>/.
For NON-Steam games that appid is a shortcut id Steam randomises on every
fresh install, which is the whole reason store/prefix_registry.json pins the
gospel appid and the steam_shortcut step forces it: the shortcut's appid and
the restored prefix's folder name must agree, or Steam looks in an empty
compatdata and the saves may as well not exist.

Ordering: install the game (and let Steam make its shortcut) BEFORE importing
— Steam creates/wipes compatdata around first launch, so importing last wins.

symlinks=True on the copy is load-bearing: a Wine prefix contains
dosdevices/z: -> /, and following that would try to copy the entire root
filesystem into compatdata.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import detect

PREFIX_BAK = ".gfm-prefixbak"

# (restore_dir, backups_dir) — the Deck variant uses underscores, the older
# Bazzite one has a space in "prefix backups". Both exist in Tony's history.
_LAYOUTS = (
    ("steamos_restore", "prefix_backups"),
    ("bazzite_restore", "prefix backups"),
)


@dataclass
class Backup:
    appid: str
    safe_name: str
    path: Path  # .../<safe_name>/<appid>/

    @property
    def has_pfx(self) -> bool:
        """A real prefix backup has a pfx/ with FILES in it.

        This used to be `(self.path / "pfx").is_dir()`, which a husk passes:
        prefixbackup.backup() pre-creates the whole destination tree before
        copying anything, so a run where every copy failed leaves the exact
        shape of a backup with nothing inside. This is the only gate on
        _import_one, and importing a husk moves the LIVE prefix aside and
        replaces it with nothing."""
        from . import prefixbackup      # deferred: avoid an import cycle
        return prefixbackup.is_whole(self.path)


def backup_roots(sd_roots: list[Path] | None = None) -> list[Path]:
    """Every prefix-backup root present, across both known layouts."""
    from . import store  # deferred — store may import us
    if sd_roots is None:
        sd_roots = store.sd_card_roots()
    out: list[Path] = []
    for sd in sd_roots:
        for restore_dir, backups_dir in _LAYOUTS:
            root = sd / restore_dir / backups_dir
            try:
                if root.is_dir():
                    out.append(root)
            except OSError:
                continue
    return out


def list_backups(roots: list[Path] | None = None) -> list[Backup]:
    """Every backed-up prefix found, newest layout winning on appid clashes."""
    roots = backup_roots() if roots is None else roots
    found: dict[str, Backup] = {}
    for root in roots:
        try:
            name_dirs = sorted(root.iterdir())
        except OSError:
            continue
        for name_dir in name_dirs:
            if not name_dir.is_dir():
                continue
            try:
                id_dirs = sorted(name_dir.iterdir())
            except OSError:
                continue
            for id_dir in id_dirs:
                if id_dir.is_dir() and id_dir.name.isdigit():
                    found.setdefault(
                        id_dir.name, Backup(id_dir.name, name_dir.name, id_dir))
    return sorted(found.values(), key=lambda b: b.safe_name.lower())


def target_dir(steam_root: Path, appid: str) -> Path:
    """Where this machine wants the prefix: the compatdata of whichever
    library owns the game (Steam titles), else the primary library."""
    for lib in detect.library_folders(steam_root):
        if (lib / "steamapps" / f"appmanifest_{appid}.acf").is_file():
            return lib / "steamapps" / "compatdata" / appid
    return steam_root / "steamapps" / "compatdata" / appid


def is_live(steam_root: Path, appid: str) -> bool:
    try:
        return target_dir(steam_root, appid).exists()
    except OSError:
        return False


def _free_bak(dst: Path) -> Path:
    """A set-aside name that is not already taken.

    restore() used to rmtree an existing <appid>.gfm-prefixbak before moving
    the live prefix into it. On a SECOND import the live prefix IS the one the
    first import wrote, and the bak holds the user's real, current prefix —
    so the current prefix was destroyed and replaced by another copy of the
    backup, while the log said it had been kept safely. cmd_import_prefixes
    is step 2/4 of the Save Restore bundle and its own warning tells the user
    to re-run when the live prefix is newer, so a second run is the expected
    thing to do, not an unlikely one. A prefix holds the saves of every game
    that writes to Documents or AppData."""
    bak = dst.with_name(dst.name + PREFIX_BAK)
    if not bak.exists():
        return bak
    for n in range(2, 1000):
        cand = dst.with_name(f"{dst.name}{PREFIX_BAK}.{n}")
        if not cand.exists():
            return cand
    return dst.with_name(dst.name + PREFIX_BAK + ".last")


def restore(backup: Backup, steam_root: Path,
            log: Callable[[str], None] = print) -> tuple[Path, int]:
    """Copy a backed-up prefix into compatdata. Any prefix already live at the
    target is moved aside to <appid>.gfm-prefixbak — an import must never be
    what destroys a current save. Returns (destination, files_copied)."""
    # Check the SOURCE is real before touching the live prefix. Moving a
    # working prefix aside and then discovering the backup is empty is not
    # recoverable by a second attempt — the second attempt is what eats the
    # set-aside copy.
    if not backup.has_pfx:
        raise OSError(f"{backup.path} holds no prefix files — refusing to "
                      "import it over a live prefix")
    dst = target_dir(steam_root, backup.appid)
    if dst.exists():
        bak = _free_bak(dst)
        shutil.move(str(dst), str(bak))
        log(f"      ~ existing prefix kept as {bak.name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    files = 0

    def _copy(src, dest):
        nonlocal files
        files += 1
        shutil.copy2(src, dest)

    # symlinks=True: prefixes contain dosdevices/z: -> / ; following it would
    # copy the whole filesystem.
    shutil.copytree(backup.path, dst, symlinks=True, copy_function=_copy)
    log(f"      + {backup.path} -> {dst} ({files} files)")
    return dst, files
