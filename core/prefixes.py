"""Existing Wine prefix identification and adoption.

When you copy prefixes over from an old install (or a reimage puts you next
to prefixes from before), the shortcut Steam creates gets a fresh appid, so
Steam looks in an empty compatdata folder while your real prefix sits at
the old appid. This module identifies which existing prefix belongs to
which recipe so the reconciler can rewire the shortcut to it.

Identification signals, in confidence order:
  1. GBM's non_steam_games.csv     (appid -> safe_name pairs, direct match)
  2. drive_c folder-name scan      (Program Files / AppData names)
  3. caller falls back to prompt   (multiple candidates, or none)

Nothing here touches disk — all reads. The reconciler in gfm.py does the
actual writes (Steam closed, backups, both shortcuts.vdf and config.vdf).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from . import detect

GBM_CSV_DEFAULT = Path.home() / "scripts" / "non_steam_games.csv"

# Prefix subdirs GBM has traditionally scanned for identifying markers.
_DRIVE_C_SCAN_DIRS = (
    "Program Files",
    "Program Files (x86)",
    "users/steamuser/AppData/Local",
    "users/steamuser/AppData/Roaming",
    "users/steamuser/Documents",
)

# Common Windows folders that live under Program Files but aren't games.
_DRIVE_C_SKIP = re.compile(
    r"^(microsoft|windows|common files|internet explorer|windowsapps|"
    r"packages|windows nt|windows defender|windows media player|"
    r"windows sidebar|windows mail|windows portable devices|default|"
    r"public|desktop|my (games|music|pictures|videos))$",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_gbm_csv(path: Path | None = None) -> dict[str, str]:
    """Read GBM's non_steam_games.csv -> {appid_str: safe_name}."""
    path = path or GBM_CSV_DEFAULT
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip().isdigit():
                result[row[0].strip()] = row[1].strip()
    return result


def compatdata_prefixes(steam_root: Path) -> list[Path]:
    """Every compatdata/<numeric-id> folder across all Steam libraries."""
    out: list[Path] = []
    for lib in detect.library_folders(steam_root):
        root = lib / "steamapps" / "compatdata"
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name.isdigit():
                out.append(d)
    return out


def is_steam_owned(appid: str, steam_root: Path) -> bool:
    """A compatdata folder whose id is a Steam-game appid should not be
    re-wired — it's rightfully owned by that game."""
    for lib in detect.library_folders(steam_root):
        if (lib / "steamapps" / f"appmanifest_{appid}.acf").is_file():
            return True
    return False


def scan_drive_c(pfx: Path) -> set[str]:
    """Folder names found under the game-adjacent parts of drive_c —
    used as identity clues."""
    drive_c = pfx / "pfx" / "drive_c"
    if not drive_c.is_dir():
        return set()
    names: set[str] = set()
    for rel in _DRIVE_C_SCAN_DIRS:
        p = drive_c / rel
        if not p.is_dir():
            continue
        for d in p.iterdir():
            if d.is_dir() and not _DRIVE_C_SKIP.match(d.name):
                names.add(d.name)
    return names


def _recipe_id_set(recipe) -> set[str]:
    """Every name-shaped clue a recipe carries, normalised."""
    parts = list(recipe.all_names)
    parts += recipe.detect.get("install_dir_names", [])
    return {_norm(p) for p in parts if p}


def identify_prefix(pfx: Path, recipe, gbm_map: dict[str, str]) -> str | None:
    """If this prefix plausibly belongs to this recipe, return the signal
    that matched ('csv' or 'drive_c'). None means no match."""
    appid = pfx.name
    recipe_ids = _recipe_id_set(recipe)

    csv_name = gbm_map.get(appid)
    if csv_name and _norm(csv_name) in recipe_ids:
        return "csv"

    if _recipe_ids_hit_drive_c(pfx, recipe_ids):
        return "drive_c"

    return None


def _recipe_ids_hit_drive_c(pfx: Path, recipe_ids: set[str]) -> bool:
    return bool(recipe_ids & {_norm(n) for n in scan_drive_c(pfx)})


def find_candidates(steam_root: Path, recipe, gbm_map: dict[str, str],
                    exclude_appids: set[int]) -> list[tuple[Path, str]]:
    """Candidate prefixes that plausibly belong to this recipe.
    Excludes prefixes belonging to Steam games (appmanifest present) and
    any explicitly-excluded appids (e.g. shortcuts we're rewiring FROM)."""
    matches: list[tuple[Path, str]] = []
    for pfx in compatdata_prefixes(steam_root):
        if int(pfx.name) in exclude_appids:
            continue
        if is_steam_owned(pfx.name, steam_root):
            continue
        signal = identify_prefix(pfx, recipe, gbm_map)
        if signal:
            matches.append((pfx, signal))
    return matches


def shortcut_has_live_prefix(steam_root: Path, appid: int) -> bool:
    """True when compatdata/<appid> exists and looks populated (a bare Steam
    empty-shell prefix has an empty drive_c or none at all)."""
    for lib in detect.library_folders(steam_root):
        p = lib / "steamapps" / "compatdata" / str(appid) / "pfx" / "drive_c"
        if p.is_dir():
            try:
                next(p.iterdir())
            except StopIteration:
                continue
            return True
    return False
