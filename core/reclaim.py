"""Reclaim SD space: uninstall a tool-deployed game once YOU'VE removed its
Steam shortcut yourself.

The workflow Tony asked for: a game copied to the SD by `deploy`, whose Steam
shortcut GFM created, is a candidate for deletion the moment that shortcut
disappears from shortcuts.vdf (i.e. he deleted it). A weekly scan spots it and
frees the space — the game stays on the NAS, so it's a re-copy away, never a
loss.

This is the one place the tool DELETES user files, so every rule here is a
safety rule. A candidate must satisfy ALL of:

  1. It's in the DEPLOYED record — GFM copied it from the NAS. (Not something
     hand-copied; we only reclaim what we can put back.)
  2. Its shortcut was SEEN at least once and is now GONE. "Never applied" and
     "you deleted it" both look like "no shortcut right now"; only the second
     should delete. We latch `shortcut_seen` per game so a freshly-deployed,
     not-yet-applied game (the gap between Deploy and Apply) is never touched.
  3. It's still on the SD (else nothing to do) AND still staged on the NAS
     `_games/<name>/` (else deletion isn't reversible — refuse).
  4. It's larger than the size floor (default 34 GB). Every game whose save
     lives in the GAME FOLDER (The Crew, Simpsons, HOTP) is smaller than this,
     so the floor makes them ineligible by construction — the case that could
     actually lose data.
  5. If the recipe declares `save_paths` anyway, those saves must already be
     captured. Belt to the floor's braces.

Fail closed everywhere: no Steam root, an unreadable shortcuts.vdf, or a
suspicious number of games vanishing at once (a wiped/rewritten vdf) → reclaim
NOTHING. Losing disk space to caution is free; deleting a game isn't.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import deploy, saves, shortcutsvdf

DEFAULT_MIN_BYTES = 34 * (1 << 30)   # 34 GB — Tony's floor (2026-07-13)
# If more than this many deployed games lose their shortcut between two scans,
# something is wrong with shortcuts.vdf (reimage, reset, botched write) rather
# than the user deleting a couple by hand. Unattended runs refuse the batch.
MAX_AUTO_BATCH = 3


@dataclass
class Candidate:
    name: str
    sd_dir: Path
    size: int
    recipe: object = None


@dataclass
class ReclaimScan:
    candidates: list = field(default_factory=list)
    deployed: dict = field(default_factory=dict)   # updated record to persist
    notes: list = field(default_factory=list)       # things skipped, and why
    blocked: bool = False                            # fail-closed tripped


def dir_size(path: Path) -> int:
    """Total bytes under a directory, tolerant of unreadable entries."""
    total = 0
    for dp, _d, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dp, n))
            except OSError:
                continue
    return total


def _readable(steam_root: Path) -> bool:
    """shortcuts.vdf parses? A corrupt/locked file must fail closed, not be
    read as 'every shortcut is gone'."""
    try:
        shortcutsvdf.find_appids(steam_root, ["__gfm_preflight__"])
        return True
    except shortcutsvdf.ShortcutsError:
        return False


def _saves_safe(recipe, local_payloads: Path | None) -> bool:
    """True if this game's game-folder saves are already backed up (or it has
    none). save_paths games that AREN'T captured must never be deleted."""
    if not getattr(recipe, "save_paths", None):
        return True
    if local_payloads is None:
        return False
    return bool(saves.read_index(local_payloads / recipe.id / "saves"))


def scan(recipes, steam_root, deployed: dict, sd_games_dirs,
         local_payloads: Path | None, min_bytes: int = DEFAULT_MIN_BYTES,
         ) -> ReclaimScan:
    """Work out which deployed games are now reclaimable. Pure — deletes
    nothing. Returns candidates + an updated `deployed` record to persist
    (latched shortcut_seen flags, entries dropped once their folder is gone).
    """
    out = ReclaimScan(deployed=dict(deployed))
    if steam_root is None:
        out.notes.append("no Steam root — can't tell which shortcuts exist")
        out.blocked = True
        return out
    if not _readable(steam_root):
        out.notes.append("shortcuts.vdf unreadable — failing closed")
        out.blocked = True
        return out

    by_name = {r.name: r for r in recipes}

    def sd_dir_for(name: str):
        for g in sd_games_dirs or []:
            p = g / name
            try:
                if p.is_dir():
                    return p
            except OSError:
                continue
        return None

    for name in list(out.deployed.keys()):
        rec = out.deployed[name]
        recipe = by_name.get(name)
        sd_dir = sd_dir_for(name)
        if sd_dir is None:
            out.deployed.pop(name, None)       # already off the SD — forget it
            continue
        if recipe is None:
            out.notes.append(f"{name}: deployed but no recipe — can't track its "
                             "shortcut, left alone")
            continue
        try:
            has_shortcut = bool(
                shortcutsvdf.find_appids(steam_root, recipe.all_names))
        except shortcutsvdf.ShortcutsError:
            out.notes.append("shortcuts.vdf went unreadable mid-scan — failing "
                             "closed")
            out.blocked = True
            return ReclaimScan(deployed=dict(deployed), notes=out.notes,
                               blocked=True)
        if has_shortcut:
            rec["shortcut_seen"] = True         # latch it
            continue
        if not rec.get("shortcut_seen"):
            continue                            # deployed, never applied — skip
        # shortcut was there, now gone -> the user removed it. Now the guards.
        staged = deploy.staged_root(local_payloads) / name if local_payloads else None
        if staged is None or not _dir_ok(staged):
            out.notes.append(f"{name}: shortcut gone, but NOT staged on the NAS "
                             "— refusing to delete something we can't restore")
            continue
        if not _saves_safe(recipe, local_payloads):
            out.notes.append(f"{name}: shortcut gone, but its game-folder saves "
                             "aren't captured — refusing (run 🔍 Scan first)")
            continue
        size = dir_size(sd_dir)
        if size <= min_bytes:
            out.notes.append(f"{name}: shortcut gone, but {size / (1 << 30):.1f}"
                             f"GB is under the {min_bytes / (1 << 30):.0f}GB "
                             "floor — kept")
            continue
        out.candidates.append(Candidate(name, sd_dir, size, recipe))
    return out


def _dir_ok(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def uninstall(candidate: Candidate, deployed: dict,
              log: Callable[[str], None] = print) -> int:
    """Delete a candidate's SD folder and drop it from the deployed record.
    Returns bytes freed (0 on failure). The prefix and the NAS copy are left
    untouched, so this is reversible by re-deploying + re-applying."""
    try:
        shutil.rmtree(candidate.sd_dir)
    except OSError as e:
        log(f"      ! {candidate.name}: delete failed — {e}")
        return 0
    deployed.pop(candidate.name, None)
    log(f"      - {candidate.sd_dir} ({candidate.size / (1 << 30):.1f}GB freed)")
    return candidate.size
