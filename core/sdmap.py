"""The SD map: one JSON file that says where every non-Steam game lives.

Lives at <SD>/steamos_restore/game_fixes/sd_map.json — the same folder the
store mirror rides in, so it survives reimages naturally and can be
Syncthing-mirrored to a workstation for visibility.

When the map has a game, detection uses it authoritatively — no fallbacks.
The messier detection stack (shortcuts.vdf, folder scans, prompts) only
fires for games the map hasn't heard of.

Schema:
  {
    "_meta": { "host": "steamdeck", "scanned_at": "...", "sd_root": "..." },
    "games_dir": "/run/media/deck/primary/Games",
    "games": {
      "the-crew": { "path": ".../Games/TheCrew",
                    "exes": [ { "rel": "TheCrew.exe", "size": 123 } ] },
      "barnyard": { "path": "...", "notes": "optional freeform" }
    },
    "unmatched": [
      { "path": ".../Games/Some Folder",
        "exes": [ { "rel": "game.exe", "size": 456 } ] }
    ]
  }
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from . import store

MAP_REL_PATH = "steamos_restore/game_fixes/sd_map.json"


def log_path() -> Path:
    """Where to write gfm.log — next to the map (so it Syncthing-mirrors to
    the workstation for debugging), else the config dir as a fallback."""
    dest = default_write_path()
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest.parent / "gfm.log"
    from . import store
    store.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return store.CONFIG_DIR / "gfm.log"


def find_map_files() -> list[Path]:
    """Every sd_map.json present across the mounted SD cards."""
    return [p for sd in store.sd_card_roots()
            if (p := sd / MAP_REL_PATH).is_file()]


def load_first() -> dict:
    """First (and typically only) map found, or {}."""
    for f in find_map_files():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return {}


def get_game_path(game_id: str, data: dict | None = None) -> Path | None:
    """The Path for a game_id from the map, or None if the map doesn't
    know about it. This is what detection consults FIRST."""
    data = data if data is not None else load_first()
    entry = data.get("games", {}).get(game_id)
    if not entry:
        return None
    raw = entry.get("path")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def default_write_path() -> Path | None:
    """Where to write when nobody's told us otherwise: the first SD card's
    steamos_restore/game_fixes/. None if there isn't one."""
    for sd in store.sd_card_roots():
        return sd / MAP_REL_PATH
    return None


def _launch_info(recipe, steam_root: Path) -> dict:
    """How this game actually launches right now: appid, exe, start dir,
    launch options, and the forced Proton/compat tool.

    Recording only the appid told us a shortcut EXISTED but nothing about
    what it does — so a change made by hand in Steam (different Proton,
    edited launch options, exe repointed) was invisible to a scan and had to
    be asked about instead. This is the difference between the map saying
    "FUEL has a shortcut" and "FUEL launches SecuLauncher.exe under
    GE-Proton10-34 with WINEDLLOVERRIDES=...".

    Never raises — a scan must not die because Steam's config is mid-write
    or a VDF is unreadable. Anything it can't determine is simply absent.
    """
    from . import shortcutsvdf, steamvdf
    out: dict = {}
    try:
        info = shortcutsvdf.describe(steam_root, recipe.all_names)
    except Exception:
        info = None
    if info:
        out["shortcut_appid"] = info.get("appid")
        out["launch"] = {k: info[k] for k in
                         ("exe", "start_dir", "launch_options") if k in info}
    else:
        out["shortcut_appid"] = None

    appid = out.get("shortcut_appid") or recipe.steam_appid
    if appid:
        try:
            compat = steamvdf.get_compat_tool(steam_root, int(appid))
        except Exception:
            compat = None
        if compat:
            # "name" is the Proton/GE/CachyOS build Steam is forcing.
            out.setdefault("launch", {})["compat_tool"] = \
                compat.get("name", "")
    return out


def write(matched: list, unmatched: list, games_dir: Path,
          dest: Path, existing: dict | None = None,
          steam_root: Path | None = None) -> dict:
    """Write the map. matched is [(recipe, folder, signal), ...],
    unmatched is [folder, ...]. Existing entries not touched by the scan
    are preserved (freeform notes survive). When steam_root is given, each
    matched game also records its live non-Steam shortcut appid, so a scan
    surfaces the appid Steam actually assigned (for gospel reconciliation)."""
    from . import sdscan, shortcutsvdf
    existing = existing or {}
    games = dict(existing.get("games", {}))
    for recipe, folder, signal in matched:
        prev = games.get(recipe.id, {})
        entry = {**prev, "path": str(folder), "matched_by": signal,
                 "exes": sdscan.find_exes(folder),
                 "readmes": sdscan.find_readmes(folder)}
        if steam_root is not None:
            entry.update(_launch_info(recipe, steam_root))
        games[recipe.id] = entry
    payload = {
        "_meta": {
            "host": socket.gethostname(),
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sd_root": str(dest.parents[2]) if len(dest.parents) >= 3 else "",
        },
        "games_dir": str(games_dir),
        "games": games,
        "unmatched": [{"path": str(f), "exes": sdscan.find_exes(f),
                       "readmes": sdscan.find_readmes(f)}
                      for f in unmatched],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def write_steam_section(games: list, dest: Path,
                        existing: dict | None = None) -> dict:
    """Merge/overwrite the steam_games section of the map. Games list is
    what steamscan.scan() + cross_reference() produced. Preserves everything
    else in the file (SD games, notes, unmatched folders)."""
    import socket
    import time
    payload = dict(existing or {})
    payload.setdefault("_meta", {}).update({
        "host": socket.gethostname(),
        "steam_scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    payload["steam_games"] = {g["appid"]: {k: v for k, v in g.items()
                                            if k != "appid"}
                              for g in games}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def write_prefix_backups_section(entries: list, dest: Path,
                                 existing: dict | None = None) -> dict:
    """Merge/overwrite the prefix_backups section — what's actually sitting in
    <SD>/steamos_restore/prefix_backups/, and which game each one belongs to.
    Lets a pile of backups dragged in from the old tool be identified rather
    than just found. Preserves everything else in the file."""
    import socket
    import time
    payload = dict(existing or {})
    payload.setdefault("_meta", {}).update({
        "host": socket.gethostname(),
        "prefix_backups_scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    payload["prefix_backups"] = entries
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def diff(before: dict, after: dict) -> dict:
    """Human-readable diff for the UI: added/changed/removed games."""
    b = before.get("games", {})
    a = after.get("games", {})
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = sorted(g for g in set(a) & set(b)
                     if a[g].get("path") != b[g].get("path"))
    return {"added": added, "changed": changed, "removed": removed}
