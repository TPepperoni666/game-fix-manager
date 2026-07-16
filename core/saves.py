"""save_paths: back up and restore saves that live OUTSIDE the Proton prefix.

Prefix backups cover everything under compatdata/<appid>/pfx. They do NOT
cover games that write their save next to the exe — The Crew's data.bin,
Simpsons' Save1, Heroes of the Pacific's Save/. Those are invisible to every
other backup we have: they die with the SD card, or the moment the game
folder is replaced/reinstalled/re-copied.

Manifest form:
  "save_paths": [
    "{game_dir}/data.bin",
    "{game_dir}/Save1",
    "{game_dir}/PROF_SAVE_*",
    "{prefix}/drive_c/users/steamuser/Documents/Foo/settings"
  ]

Entries resolve through Ctx.resolve_target, so the SAME template re-resolves
on the target machine (different SD mount point, different appid). Capture
stores by slot index + an index.json; restore re-resolves each template and
puts the files back where THIS machine says they belong — so a snapshot taken
before a reimage lands correctly after it.

An entry may name a file, a directory (copied whole) or a glob (matched
against its parent). Entries that don't exist yet are skipped and logged, so
listing a speculative path costs nothing.

Layout: <dest>/index.json + <dest>/<slot>/<name>
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from . import engine

INDEX_NAME = "index.json"
SAVE_BAK = ".gfm-savebak"
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


def capture(recipe, game_dir: Path, steam_root: Path | None, dest: Path,
            log: Callable[[str], None] = print) -> tuple[int, int]:
    """Snapshot every save_paths entry into <dest>.

    Returns (entries_captured, files_captured). Writing index.json last means
    a half-finished capture never leaves a manifest claiming more than it has.
    """
    entries, files = [], 0
    for slot, template in enumerate(getattr(recipe, "save_paths", [])):
        resolved = _resolve(recipe, game_dir, steam_root, template)
        if resolved is None:
            log(f"      ? {template} — no prefix yet, skipped")
            continue
        hits = _matches(resolved)
        if not hits:
            log(f"      ? {template} — nothing there yet, skipped")
            continue
        slot_dir = dest / str(slot)
        _discard(slot_dir)  # replace this slot's previous snapshot wholesale
        names = []
        for src in hits:
            try:
                _copy(src, slot_dir / src.name)
            except OSError as e:
                log(f"      ! {src}: {e}")
                continue
            names.append(src.name)
            files += 1
            log(f"      + {src}")
        if names:
            entries.append({"slot": slot, "template": template, "names": names})
    if entries:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / INDEX_NAME).write_text(
            json.dumps({"recipe": recipe.id, "entries": entries}, indent=2),
            encoding="utf-8")
    return len(entries), files


def read_index(src: Path) -> list[dict]:
    """Captured entries at <src>, or [] when there's no usable snapshot."""
    try:
        data = json.loads((src / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries", [])
    return entries if isinstance(entries, list) else []


def restore(recipe, game_dir: Path, steam_root: Path | None, src: Path,
            log: Callable[[str], None] = print) -> int:
    """Put captured saves back where THIS machine resolves them to.

    Anything already live is moved aside to <name>.gfm-savebak first — a
    restore must never be the thing that eats a newer save.
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
                bak = dst.with_name(dst.name + SAVE_BAK)
                _discard(bak)
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
