"""Locate Steam and the install directory for each game recipe.

Resolution order per recipe:
  1. Path remembered in machine config (user told us once before)
  2. Steam appid -> appmanifest_<id>.acf -> installdir  (all library folders)
  3. Marker scan: steamapps/common/* matched by install_dir_names / marker_files
Returns None when not found — caller prompts the user and remembers the answer.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import sdmap, shortcutsvdf
from .manifest import Recipe

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def find_steam_root(override: str | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    home = Path.home()
    for candidate in (home / ".local/share/Steam", home / ".steam/steam"):
        if candidate.is_dir():
            return candidate
    return None


def _vdf_pairs(text: str) -> dict[str, str]:
    """Flat key/value scrape of a text VDF. Good enough for the fields we read."""
    return {k.lower(): v for k, v in _KV_RE.findall(text)}


def library_folders(steam_root: Path) -> list[Path]:
    """All Steam library roots (internal + SD card etc.), steam_root always first."""
    libs = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        for k, v in _KV_RE.findall(vdf.read_text(encoding="utf-8", errors="replace")):
            if k.lower() == "path":
                p = Path(v)
                if p.is_dir() and p not in libs:
                    libs.append(p)
    return libs


def find_by_appid(appid: int, libs: list[Path]) -> Path | None:
    for lib in libs:
        manifest = lib / "steamapps" / f"appmanifest_{appid}.acf"
        if not manifest.is_file():
            continue
        pairs = _vdf_pairs(manifest.read_text(encoding="utf-8", errors="replace"))
        installdir = pairs.get("installdir")
        if installdir:
            game_dir = lib / "steamapps" / "common" / installdir
            if game_dir.is_dir():
                return game_dir
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_by_markers(recipe: Recipe, libs: list[Path]) -> Path | None:
    """Two passes: a folder-name match anywhere beats a marker-file match.
    Markers can be shared between engine siblings (e.g. Shift 2 and
    Automobilista 2 both ship PakFiles/BOOTFLOW.bff) — names are stronger
    evidence, so never let a marker hit shadow a name hit."""
    dir_names = {_norm(n) for n in recipe.detect.get("install_dir_names", [])}
    dir_names |= {_norm(n) for n in recipe.all_names}
    markers = recipe.detect.get("marker_files", [])

    common_dirs = []
    for lib in libs:
        common = lib / "steamapps" / "common"
        if common.is_dir():
            common_dirs.extend(d for d in common.iterdir() if d.is_dir())

    for d in common_dirs:
        if _norm(d.name) in dir_names:
            return d
    if markers:
        for d in common_dirs:
            if all((d / m).is_file() for m in markers):
                return d
    return None


def find_prefix(recipe: Recipe, steam_root: Path | None) -> Path | None:
    """The game's Proton/Wine prefix (compatdata/<id>/pfx). Steam games use
    the recipe appid; non-Steam games use the matching shortcut's appid."""
    if steam_root is None:
        return None
    ids: list[int] = []
    if recipe.steam_appid:
        ids.append(recipe.steam_appid)
    try:
        ids += shortcutsvdf.find_appids(steam_root, recipe.all_names)
    except shortcutsvdf.ShortcutsError:
        pass
    for lib in library_folders(steam_root):
        for appid in ids:
            pfx = lib / "steamapps" / "compatdata" / str(appid) / "pfx"
            if pfx.is_dir():
                return pfx
    return None


def find_game_dir(recipe: Recipe, steam_root: Path | None,
                  remembered: dict[str, str]) -> Path | None:
    # 1) Local override — user typed a path once, it's authoritative for them.
    saved = remembered.get(recipe.id)
    if saved and Path(saved).is_dir():
        return Path(saved)
    # 2) SD map — the tool's authoritative source. If the game is there,
    # use that path and skip every fallback. No probabilistic anything.
    mapped = sdmap.get_game_path(recipe.id)
    if mapped is not None:
        return mapped
    if steam_root is None:
        return None
    libs = library_folders(steam_root)
    if recipe.steam_appid:
        found = find_by_appid(recipe.steam_appid, libs)
        if found:
            return found
    # Non-Steam shortcut the user added themselves — strong evidence.
    # (The recommended flow for unobtainable games like The Crew: add the
    # exe to Steam first, and detection reads the location from there.)
    try:
        for p in shortcutsvdf.find_game_dirs(steam_root, recipe.all_names):
            return p
    except shortcutsvdf.ShortcutsError:
        pass  # malformed shortcuts.vdf shouldn't kill detection
    return find_by_markers(recipe, libs)
