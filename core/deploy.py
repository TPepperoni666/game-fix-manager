"""Deploy a staged game from the NAS onto the SD card.

The last missing link in a reimage restore: recipes, shortcuts+gospel appids,
runners, prefixes, saves and art can all be put back — but nothing restored
the GAME FILES. This does.

Layout (mirrors the existing _runners/ _state/ convention — underscore means
shared, sibling of the per-recipe payload folders):
    <local_payloads>/_games/<Folder Name>/   ->   <SD>/Games/<Folder Name>/

Keyed by the exact folder name it should become on the SD, NOT by recipe id,
so a game can be staged before anyone writes it a recipe.

Design notes:
  * FOLDER, not archive. Tony's rule is one copy on the NAS and one on the
    SD — an archive would mean a third (archive + extracted) transiently, and
    on a 12GB game that's 24GB of SD for no gain: game data is already
    compressed, so packing buys nothing.
  * RESUMABLE. Files matching on size + whole-second mtime are skipped, so a
    copy killed at 80% resumes instead of restarting. Whole-second because
    the SD is exFAT/FAT32 and can't store sub-second times.
  * INTRA-FILE progress. A game like BF3 is 12GB across ~368 files — single
    files run to multiple GB, so per-file progress would sit motionless for
    minutes. Progress is reported per chunk.
  * Empty directories are recreated too: a faithful copy, no cleverness about
    what looks like junk.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GAMES_DIR = "_games"
_CHUNK = 4 << 20        # 4 MiB — big enough to stream SMB efficiently
_TICK = 0.25            # seconds between progress callbacks


@dataclass
class StagedGame:
    name: str
    path: Path
    files: int | None = None   # None until measure() — see list_staged()
    size: int | None = None


def staged_root(local_payloads: Path) -> Path:
    return local_payloads / GAMES_DIR


def tree_stats(root: Path) -> tuple[int, int]:
    """(file_count, total_bytes) — tolerant of unreadable entries.

    EXPENSIVE over SMB: one round-trip per file. Call it for the ONE game the
    user picked, never for every game just to draw a menu.
    """
    files = size = 0
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            try:
                size += os.path.getsize(os.path.join(dirpath, n))
                files += 1
            except OSError:
                continue
    return files, size


def measure(game: StagedGame) -> StagedGame:
    """Fill in files/size for one game (walks its tree). Idempotent."""
    if game.files is None or game.size is None:
        game.files, game.size = tree_stats(game.path)
    return game


def list_staged(local_payloads: Path) -> list[StagedGame]:
    """Every game staged under _games/ — NAMES ONLY, deliberately.

    This used to walk each game's whole tree for a size, and the caller then
    walked them all AGAIN via plan() to work out resume state — two full
    walks per game, each a round-trip per file, before the menu even drew.
    With ~32k files staged that's ~65k SMB round-trips to render a list of
    names, and it took an age. Now it's one cheap iterdir(); size and resume
    state are measured for the ONE game that gets picked.

    Empty list if the mount is down.
    """
    root = staged_root(local_payloads)
    out: list[StagedGame] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if not d.is_dir() or d.name.startswith("."):
                continue
        except OSError:
            continue
        out.append(StagedGame(d.name, d))
    return out


def free_space(path: Path) -> int:
    """Free bytes at the nearest existing ancestor of path."""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def _same(src: Path, dst: Path) -> bool:
    """Already copied? Size + whole-second mtime (exFAT/FAT32 has no
    sub-second resolution), same rule mirror_store uses."""
    try:
        s, d = src.stat(), dst.stat()
    except OSError:
        return False
    return d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime)


def _copy_chunked(src: Path, dst: Path, on_bytes: Callable[[int], None]) -> None:
    """copy2 equivalent that reports progress as it goes."""
    tmp = dst.with_name(dst.name + ".gfm-part")
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                buf = fi.read(_CHUNK)
                if not buf:
                    break
                fo.write(buf)
                on_bytes(len(buf))
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)  # atomic: a killed copy never looks complete
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def plan(game: StagedGame, dest_root: Path) -> tuple[list[tuple[Path, Path]], int, int]:
    """(files_to_copy, bytes_to_copy, already_ok) — the resume calculation."""
    dst_root = dest_root / game.name
    todo: list[tuple[Path, Path]] = []
    size = skipped = 0
    for dirpath, _dirs, names in os.walk(game.path):
        rel_dir = Path(dirpath).relative_to(game.path)
        for n in sorted(names):
            src = Path(dirpath) / n
            dst = dst_root / rel_dir / n
            if _same(src, dst):
                skipped += 1
                continue
            try:
                size += src.stat().st_size
            except OSError:
                continue
            todo.append((src, dst))
    return todo, size, skipped


def deploy(game: StagedGame, dest_root: Path,
           progress: Callable[[int, int, str], None] | None = None,
           log: Callable[[str], None] = print) -> dict:
    """Copy a staged game to <dest_root>/<name>/. Resumes; reports progress as
    (bytes_done, bytes_total, current_relative_path)."""
    dst_root = dest_root / game.name
    # Recreate every directory first, empty ones included — a faithful copy.
    for dirpath, _dirs, _names in os.walk(game.path):
        rel_dir = Path(dirpath).relative_to(game.path)
        (dst_root / rel_dir).mkdir(parents=True, exist_ok=True)

    todo, total, skipped = plan(game, dest_root)
    done = copied = 0
    last = 0.0
    started = time.monotonic()
    for src, dst in todo:
        rel = str(src.relative_to(game.path))

        def _on(n: int) -> None:
            nonlocal done, last
            done += n
            now = time.monotonic()
            if progress and (now - last) >= _TICK:
                last = now
                progress(done, total, rel)

        _copy_chunked(src, dst, _on)
        copied += 1
    if progress:
        progress(done, total, "")
    return {"copied": copied, "skipped": skipped, "bytes": done,
            "total": total, "seconds": time.monotonic() - started,
            "dest": dst_root}
