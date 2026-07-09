"""Scan an SD card's Games/ folder and match each subfolder to a recipe.

The point: for every non-Steam game you keep on the SD card (The Crew,
Barnyard, V8 Supercars 3, Simpsons Hit & Run, whatever), skip the "prompt
you for a path" step during Apply. One scan populates every game's path in
the machine config, so future runs know exactly where to write fixes.

Match order per folder:
  1. exact folder name matches a recipe alias / install_dir_name
  2. all of the recipe's marker_files exist inside the folder
  3. unmatched (surfaced in the UI so the user can decide)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import store
from .manifest import Recipe


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# Executables that are never the game's launch target — installers,
# redistributables, crash handlers. Matched against the exe's stem.
_EXE_SKIP = re.compile(
    r"(?i)^(unins|uninstall|setup|install|redist|vc_?redist|dxsetup|"
    r"dxwebsetup|directx|dotnet|oalinst|crashreport|crashhandler|"
    r"unitycrashhandler|benchmark)"
)


def find_exes(folder: Path, max_depth: int = 3, limit: int = 8) -> list[dict]:
    """Candidate launch executables inside a game folder, best guess first.

    Each entry is {'rel': '<posix path under folder>', 'size': <bytes>}.
    Ranked shallow-then-big (the launch exe usually sits at/near the root and
    is one of the larger binaries; UE-style Binaries/Win32|64/*.exe still
    surface within max_depth). Installers/redists/crash handlers are skipped.

    Purely informational: it gives the map a real exe for each SD game so
    shortcut creation and recipe detect blocks have something to point at,
    and it disambiguates folders that ship more than one exe (e.g. a patched
    v1.1 alongside v1.0). Only meaningful where the game files are actually
    mounted (the Deck), so an empty list just means "not scanned here"."""
    if not folder.is_dir():
        return []
    base = len(folder.parts)
    found: list[tuple[int, int, str]] = []  # (depth, -size, rel) for sorting
    for dirpath, dirnames, filenames in os.walk(folder):
        depth = len(Path(dirpath).parts) - base
        if depth >= max_depth:
            dirnames[:] = []  # don't descend past max_depth
        for fn in filenames:
            if not fn.lower().endswith(".exe"):
                continue
            if _EXE_SKIP.match(Path(fn).stem):
                continue
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            found.append((depth, -size, p.relative_to(folder).as_posix()))
    found.sort()
    return [{"rel": rel, "size": -negsize} for _d, negsize, rel in found[:limit]]


# Filenames that tend to carry setup / crack / how-to-play instructions.
_README_HINT = re.compile(
    r"(?i)(how ?to ?play|read ?me|instruction|install|chemical|crack|setup)")
_README_EXT = (".txt", ".nfo", ".md", ".rtf")


def find_readmes(folder: Path, max_depth: int = 2, limit: int = 6,
                 embed_under: int = 8192) -> list[dict]:
    """Setup / how-to-play notes bundled with an SD game — especially the
    cracked-game 'how to play' readmes that spell out which exe is the crack,
    any reg fix, and disc/hardware-setup quirks (exactly the info that turned
    V8 Supercars 3 from a guess into a correct recipe).

    Each entry is {'rel', 'size'} plus 'text' when the file is small enough to
    embed (<= embed_under bytes). Embedding means a single Deck scan carries
    the instructions straight back into the map, so recipes get built from the
    author's own notes instead of guesswork. Shallow + small files first."""
    if not folder.is_dir():
        return []
    base = len(folder.parts)
    hits: list[tuple[int, int, dict]] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        depth = len(Path(dirpath).parts) - base
        if depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            if not (fn.lower().endswith(_README_EXT)
                    and _README_HINT.search(Path(fn).stem)):
                continue
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            entry = {"rel": p.relative_to(folder).as_posix(), "size": size}
            if 0 < size <= embed_under:
                try:
                    entry["text"] = p.read_text(
                        encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass
            hits.append((depth, size, entry))
    hits.sort(key=lambda t: (t[0], t[1]))  # shallow, then small
    return [e for _d, _s, e in hits[:limit]]


def find_games_dirs(sd_roots: list[Path] | None = None) -> list[Path]:
    """Every '<SD>/Games' folder present. Empty list = no games dir found."""
    if sd_roots is None:
        sd_roots = store.sd_card_roots()
    return [g for sd in sd_roots if (g := sd / "Games").is_dir()]


def _recipe_name_hits(recipe: Recipe) -> set[str]:
    hits = {_norm(n) for n in recipe.all_names}
    hits |= {_norm(n) for n in recipe.detect.get("install_dir_names", [])}
    return {h for h in hits if h}


def _match_folder(folder: Path, recipes: list[Recipe]) -> tuple[Recipe | None, str]:
    """Best-match recipe + signal ('name'/'marker'). None if no match."""
    norm_folder = _norm(folder.name)
    # Pass 1: name match (strongest — folder rename by the user is intent)
    for recipe in recipes:
        if norm_folder in _recipe_name_hits(recipe):
            return recipe, "name"
    # Pass 2: marker files (folder named oddly but contains the exe)
    for recipe in recipes:
        markers = recipe.detect.get("marker_files", [])
        if markers and all((folder / m).is_file() for m in markers):
            return recipe, "marker"
    return None, ""


def scan(games_dir: Path, recipes: list[Recipe]) -> dict:
    """Return {'matched': [(recipe, folder, signal)], 'unmatched': [folder]}."""
    matched: list[tuple[Recipe, Path, str]] = []
    unmatched: list[Path] = []
    if not games_dir.is_dir():
        return {"matched": matched, "unmatched": unmatched}
    seen_recipe_ids: set[str] = set()
    for folder in sorted(games_dir.iterdir()):
        if not folder.is_dir():
            continue
        recipe, signal = _match_folder(folder, recipes)
        if recipe and recipe.id not in seen_recipe_ids:
            matched.append((recipe, folder, signal))
            seen_recipe_ids.add(recipe.id)
        else:
            unmatched.append(folder)
    return {"matched": matched, "unmatched": unmatched}
