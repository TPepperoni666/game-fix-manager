"""Locate Steam and the install directory for each game recipe.

Resolution order per recipe:
  1. Path remembered in machine config (user told us once before)
  2. Steam appid -> appmanifest_<id>.acf -> installdir  (all library folders)
  3. Marker scan: steamapps/common/* matched by install_dir_names / marker_files
Returns None when not found — caller prompts the user and remembers the answer.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import sdmap, shortcutsvdf
from .manifest import Recipe

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def find_steam_root(override: str | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    if os.name == "nt":
        return _find_steam_root_windows()
    home = Path.home()
    for candidate in (home / ".local/share/Steam", home / ".steam/steam"):
        if candidate.is_dir():
            return candidate
    return None


def _find_steam_root_windows() -> Path | None:
    """Windows Steam: the registry SteamPath first (authoritative), then the
    usual install dirs. userdata/config/steamapps sit here just like on Linux,
    so everything above this resolves the same."""
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    val = winreg.QueryValueEx(
                        k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER
                        else "InstallPath")[0]
                    p = Path(val)
                    if p.is_dir():
                        return p
            except OSError:
                continue
    except ImportError:
        pass
    for c in (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
              / "Steam",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam"):
        if c.is_dir():
            return c
    return None


def _vdf_pairs(text: str) -> dict[str, str]:
    """Flat key/value scrape of a text VDF. Good enough for the fields we read."""
    return {k.lower(): v for k, v in _KV_RE.findall(text)}


def _readable_dir(p: Path) -> bool:
    """Is this a directory we can actually LIST?

    is_dir() is not enough for removable media. Pull an SD card while it is
    mounted and the mountpoint survives as a zombie: stat() still succeeds, so
    is_dir() says True, but every read returns EIO. Steam's libraryfolders.vdf
    goes on naming it, so the dead path gets admitted here and the first
    appmanifest read takes the whole tool down with
    'OSError: [Errno 5] Input/output error'. Listing it is the cheap question
    that a zombie mount actually fails."""
    try:
        with os.scandir(p) as it:
            next(it, None)
        return True
    except OSError:
        return False


def library_folders(steam_root: Path) -> list[Path]:
    """All Steam library roots (internal + SD card etc.), steam_root always first.

    Unreadable roots are DROPPED rather than returned: a library on a card
    that has been yanked is not a library, and every caller here reads files
    out of what we hand back."""
    libs = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        present = vdf.is_file()
    except OSError:
        present = False
    if present:
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return libs
        for k, v in _KV_RE.findall(text):
            if k.lower() == "path":
                p = Path(v)
                if p not in libs and _readable_dir(p):
                    libs.append(p)
    return libs


def find_by_appid(appid: int, libs: list[Path]) -> Path | None:
    """Belt and braces alongside library_folders' filter: a card can be pulled
    BETWEEN that check and this read, and detection failing to find a game is
    always better than detection killing the process."""
    for lib in libs:
        manifest = lib / "steamapps" / f"appmanifest_{appid}.acf"
        try:
            if not manifest.is_file():
                continue
            pairs = _vdf_pairs(
                manifest.read_text(encoding="utf-8", errors="replace"))
            installdir = pairs.get("installdir")
            if installdir:
                game_dir = lib / "steamapps" / "common" / installdir
                if game_dir.is_dir():
                    return game_dir
        except OSError:
            continue
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
        # Same reason as find_by_appid: a yanked card leaves a mountpoint that
        # stats fine and reads EIO, so every walk of it has to be survivable.
        try:
            if common.is_dir():
                common_dirs.extend(d for d in common.iterdir() if d.is_dir())
        except OSError:
            continue

    for d in common_dirs:
        if _norm(d.name) in dir_names:
            return d
    if markers:
        for d in common_dirs:
            try:
                if all((d / m).is_file() for m in markers):
                    return d
            except OSError:
                continue
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
