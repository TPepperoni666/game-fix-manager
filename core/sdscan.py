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

import re
from pathlib import Path

from . import store
from .manifest import Recipe


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


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
