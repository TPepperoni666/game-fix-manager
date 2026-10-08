"""save_paths: back up and restore saves that live OUTSIDE the Proton prefix.

Prefix backups cover everything under compatdata/<appid>/pfx. They do NOT
cover games that write their save next to the exe — The Crew's data.bin,
Simpsons' save1, Heroes of the Pacific's Save/. Those are invisible to every
other backup we have: they die with the SD card, or the moment the game
folder is replaced/reinstalled/re-copied.

Manifest form:
  "save_paths": [
    "{game_dir}/data.bin",
    "{game_dir}/game/save*",
    "{prefix}/drive_c/users/steamuser/Documents/Foo/settings"
  ]

Entries resolve through Ctx.resolve_target, so the SAME template re-resolves
on the target machine (different SD mount point, different appid). Capture
stores by slot + an index.json; restore re-resolves each template and puts the
files back where THIS machine says they belong — so a snapshot taken before a
reimage lands correctly after it.

An entry may name a file, a directory (copied whole) or a glob (matched
against its parent). Entries that don't exist yet are skipped and logged, so
listing a speculative path costs nothing.

Layout: <dest>/index.json + <dest>/<slot>/<name>

TWO WAYS THIS USED TO DESTROY THE ONLY COPY OF A SAVE. Both were found by the
2026-10-09 audit and both were proven by execution, not argument:

1. capture() discarded a slot's existing snapshot BEFORE copying the new one.
   A failed copy — one dropped SMB handle mid-run is enough — left the
   snapshot gone and the index still advertising it, and the caller reported
   "No saves found yet, play it once then capture". Every entry is now staged
   to a sibling directory and swapped in only once every file is written. A
   failure leaves the previous snapshot where it was and is COUNTED, so no
   caller can describe a destroyed backup as an empty one.

2. Slots were the template's POSITION in save_paths. Inserting an entry
   mid-list shifted every later template down a slot, so the new occupant
   wiped the previous occupant's snapshot and the old index entry was dropped
   because its slot number had been claimed. Three recipes were edited that
   way in a single week (the-crew, simpsons-hit-and-run, heroes-over-europe).
   Slots are now a hash of the template, which is stable under insertion,
   deletion and reordering; existing numeric slots migrate on next capture.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable

from . import engine

INDEX_NAME = "index.json"
SAVE_BAK = ".gfm-savebak"
STAGE_SUFFIX = ".gfm-staging"
PREV_SUFFIX = ".gfm-prev"
_GLOB_CHARS = "*?["


def _resolve(recipe, game_dir: Path, steam_root: Path | None,
             template: str) -> Path | None:
    """Resolve one template; None when it can't be (no prefix yet)."""
    ctx = engine.Ctx(recipe=recipe, game_dir=game_dir, steam_root=steam_root,
                     log=lambda _m: None)
    try:
        return ctx.resolve_target(template)
    except engine.StepError:
        return None


def slot_for(template: str) -> str:
    """Stable directory name for a template — NOT its position in the list.

    Position-keyed slots meant that editing save_paths silently destroyed the
    snapshot of whichever entry got shifted out of a slot. A hash cannot be
    claimed by a neighbour just because someone inserted a line above it."""
    return hashlib.sha1(template.encode("utf-8")).hexdigest()[:12]


def _matches(resolved: Path) -> list[Path]:
    """What a resolved entry actually points at — glob-aware."""
    if any(c in resolved.name for c in _GLOB_CHARS):
        try:
            return sorted(resolved.parent.glob(resolved.name))
        except OSError:
            return []
    try:
        return [resolved] if resolved.exists() else []
    except OSError:
        return []


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _discard(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except OSError:
            pass


def _has_files(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def _migrate(dest: Path, old: dict | None, slot_dir: Path,
             log: Callable[[str], None]) -> bool:
    """Move a template's snapshot from its old positional slot to the stable
    one. Returns True if anything moved, so the caller knows the index needs
    rewriting even when nothing new was captured this run."""
    if not old:
        return False
    old_slot = str(old.get("slot", ""))
    if not old_slot or old_slot == slot_dir.name:
        return False
    src = dest / old_slot
    if not _has_files(src) or slot_dir.exists():
        return False
    try:
        src.replace(slot_dir)
    except OSError:
        return False
    log(f"      ~ moved snapshot of {old.get('template')} to a stable slot")
    return True


def _swap_in(staging: Path, slot_dir: Path) -> None:
    """Replace slot_dir with staging, with no window where neither exists.

    The old snapshot is renamed aside and only deleted once the new one is in
    place; if moving the new one in fails, the old is put back."""
    prev = slot_dir.with_name(slot_dir.name + PREV_SUFFIX)
    _discard(prev)
    moved = False
    try:
        if slot_dir.exists():
            slot_dir.replace(prev)
            moved = True
        staging.replace(slot_dir)
    except OSError:
        if moved and not slot_dir.exists():
            try:
                prev.replace(slot_dir)
            except OSError:
                pass
        raise
    _discard(prev)


def capture(recipe, game_dir: Path, steam_root: Path | None, dest: Path,
            log: Callable[[str], None] = print) -> tuple[int, int, int]:
    """Snapshot every save_paths entry into <dest>.

    Returns (entries_captured, files_captured, entries_FAILED). The third
    value exists so that a caller cannot report a destroyed or unwritable
    backup as "no saves found yet" — that message is precisely what hid the
    original bug for months.

    The written index MERGES with the previous one: an entry whose save isn't
    in the game folder right now keeps its earlier backup listed, as long as
    its files are still on disk. Without that, capturing after a game folder
    is replaced (exactly the reimage case, and Scan captures every game
    automatically) would rewrite the index without that entry — leaving a
    perfectly good backup on the NAS that restore can never find. Old entries
    are matched by TEMPLATE, never by slot number.
    """
    old_entries = read_index(dest)
    old_by_template = {e["template"]: e for e in old_entries
                       if e.get("template")}

    fresh: list[dict] = []
    files = failed = 0
    migrated = False

    for template in getattr(recipe, "save_paths", []):
        slot = slot_for(template)
        slot_dir = dest / slot
        migrated = _migrate(dest, old_by_template.get(template),
                            slot_dir, log) or migrated

        resolved = _resolve(recipe, game_dir, steam_root, template)
        if resolved is None:
            log(f"      ? {template} — no prefix yet, skipped")
            continue
        hits = _matches(resolved)
        if not hits:
            log(f"      ? {template} — nothing there yet, skipped")
            continue

        staging = dest / (slot + STAGE_SUFFIX)
        _discard(staging)
        names: list[str] = []
        ok = True
        for src in hits:
            try:
                _copy(src, staging / src.name)
            except OSError as e:
                # BREAK, not continue: a partial staging directory must never
                # be swapped in over a complete snapshot.
                log(f"      ! {src}: {e}")
                ok = False
                break
            names.append(src.name)
            log(f"      + {src}")

        if not ok or not names:
            _discard(staging)
            if not ok:
                failed += 1
                log(f"      ! {template} — capture FAILED; the previous "
                    "snapshot has been left untouched")
            continue
        try:
            _swap_in(staging, slot_dir)
        except OSError as e:
            _discard(staging)
            failed += 1
            log(f"      ! {template} — could not swap in the new snapshot "
                f"({e}); the previous one is intact")
            continue
        files += len(names)
        fresh.append({"slot": slot, "template": template, "names": names})

    if not fresh and not migrated:
        return 0, 0, failed

    merged = list(fresh)
    done = {e["template"] for e in fresh}
    for old in old_entries:
        template = old.get("template")
        if not template or template in done:
            continue
        entry = dict(old)
        stable = slot_for(template)
        if _has_files(dest / stable):
            entry["slot"] = stable          # migrated above
        elif not _has_files(dest / str(old.get("slot", ""))):
            continue                        # nothing left to point at
        merged.append(entry)
        log(f"      = keeping earlier backup of {template} "
            "(not in the game folder right now)")

    merged.sort(key=lambda e: str(e.get("template", "")))
    dest.mkdir(parents=True, exist_ok=True)
    (dest / INDEX_NAME).write_text(
        json.dumps({"recipe": recipe.id, "entries": merged}, indent=2),
        encoding="utf-8")
    return len(fresh), files, failed


def read_index(src: Path) -> list[dict]:
    """Captured entries at <src>, or [] when there's no usable snapshot."""
    try:
        data = json.loads((src / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries", [])
    return entries if isinstance(entries, list) else []


def index_is_whole(src: Path) -> bool:
    """Does every file the index promises actually exist?

    Several guards in this tool accepted "the directory is there" as "the
    backup is good", which is how an emptied slot still read as captured.
    Checking the files the index itself names is cheap, and is the question
    those guards meant to ask."""
    entries = read_index(src)
    if not entries:
        return False
    for e in entries:
        slot = str(e.get("slot", ""))
        for name in e.get("names", []):
            if not (src / slot / name).exists():
                return False
    return True


def _free_bak(dst: Path) -> Path:
    """A set-aside name that is not already taken.

    restore() used to delete an existing <name>.gfm-savebak before moving the
    live file into it. On a SECOND restore the live file IS the snapshot the
    first restore wrote, and the bak holds the user's real, newer save — so
    the newer save was destroyed and replaced by another copy of the old
    snapshot, while the log said it had been kept safely."""
    bak = dst.with_name(dst.name + SAVE_BAK)
    if not bak.exists():
        return bak
    for n in range(2, 1000):
        cand = dst.with_name(f"{dst.name}{SAVE_BAK}.{n}")
        if not cand.exists():
            return cand
    return dst.with_name(dst.name + SAVE_BAK + ".last")


def restore(recipe, game_dir: Path, steam_root: Path | None, src: Path,
            log: Callable[[str], None] = print) -> int:
    """Put captured saves back where THIS machine resolves them to.

    Anything already live is moved aside first, to a name that is never
    recycled — a restore must never be the thing that eats a newer save, and
    that has to hold for the SECOND restore too.
    """
    n = 0
    for e in read_index(src):
        template = e.get("template", "")
        resolved = _resolve(recipe, game_dir, steam_root, template)
        if resolved is None:
            log(f"      ? {template} — can't resolve here, skipped")
            continue
        for name in e.get("names", []):
            stored = src / str(e.get("slot")) / name
            if not stored.exists():
                log(f"      ? {name} — missing from the snapshot, skipped")
                continue
            dst = resolved.parent / name
            if dst.exists():
                # Say so when an OLDER file is about to go over a newer one.
                # The live copy is kept either way, so nothing is lost.
                try:
                    if dst.stat().st_mtime > stored.stat().st_mtime:
                        log(f"      ! {name} on disk is NEWER than the "
                            "snapshot — restoring anyway; the live one is "
                            "kept aside")
                except OSError:
                    pass
                bak = _free_bak(dst)
                try:
                    shutil.move(str(dst), str(bak))
                    log(f"      ~ existing {name} kept as {bak.name}")
                except OSError as e2:
                    log(f"      ! couldn't set {name} aside ({e2}) — skipped")
                    continue
            try:
                _copy(stored, dst)
            except OSError as e2:
                log(f"      ! {dst}: {e2}")
                continue
            log(f"      + {dst}")
            n += 1
    return n
