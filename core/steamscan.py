"""Scan every Steam library on the Deck (internal SSD, SD card, external)
and inventory what's actually installed. Result is written into sd_map.json's
'steam_games' section, cross-referenced with recipes so we can see at a glance
which owned games already have fixes and which don't.

The point isn't runtime detection — Steam appid lookups already handle that.
It's giving Claude a visible ledger of Tony's Steam library so recipe
suggestions become proactive ('you have X, no recipe yet') instead of
reactive ('here's a recipe, does X match?').
"""
from __future__ import annotations

import re
from pathlib import Path

from . import detect
from .manifest import Recipe

_KV = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def _fields(manifest_text: str) -> dict:
    return {k.lower(): v for k, v in _KV.findall(manifest_text)}


def _library_kind(lib: Path) -> str:
    s = str(lib)
    if "/run/media/" in s:
        return "sd"
    if s.startswith("/home/") or s.startswith("/root/"):
        return "internal"
    return "other"


def scan(steam_root: Path | None) -> list[dict]:
    """Every installed Steam game across all libraries: appid, name,
    install_dir, library path, library kind. Filters junk (Proton runtimes,
    Steamworks stubs) so what remains is actually playable games."""
    if steam_root is None:
        return []
    skip_name_prefixes = ("Proton", "Steamworks", "Steam Linux Runtime", "Steam")
    out: list[dict] = []
    for lib in detect.library_folders(steam_root):
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
            data = _fields(manifest.read_text(encoding="utf-8", errors="replace"))
            appid = data.get("appid")
            name = data.get("name", "")
            installdir = data.get("installdir")
            if not (appid and installdir):
                continue
            if any(name.startswith(p) for p in skip_name_prefixes):
                continue
            install_path = steamapps / "common" / installdir
            if not install_path.is_dir():
                continue  # partial download / uninstalled leftover
            out.append({
                "appid": appid,
                "name": name,
                "install_dir": str(install_path),
                "library": str(lib),
                "library_kind": _library_kind(lib),
            })
    return out


def cross_reference(games: list[dict],
                    recipes: list[Recipe]) -> tuple[int, int]:
    """Annotate games with has_recipe/recipe_id in place. Returns
    (with_recipe_count, without_recipe_count)."""
    by_appid = {r.steam_appid: r.id for r in recipes if r.steam_appid}
    with_recipe = without = 0
    for g in games:
        appid = int(g["appid"])
        if appid in by_appid:
            g["has_recipe"] = True
            g["recipe_id"] = by_appid[appid]
            with_recipe += 1
        else:
            g["has_recipe"] = False
            without += 1
    return with_recipe, without
