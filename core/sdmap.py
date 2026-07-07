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
      "the-crew": { "path": "/run/media/deck/primary/Games/TheCrew" },
      "barnyard": { "path": "...", "notes": "optional freeform" }
    },
    "unmatched": [
      { "path": "/run/media/deck/primary/Games/Some Folder" }
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


def write(matched: list, unmatched: list, games_dir: Path,
          dest: Path, existing: dict | None = None) -> dict:
    """Write the map. matched is [(recipe, folder, signal), ...],
    unmatched is [folder, ...]. Existing entries not touched by the scan
    are preserved (freeform notes survive)."""
    existing = existing or {}
    games = dict(existing.get("games", {}))
    for recipe, folder, signal in matched:
        prev = games.get(recipe.id, {})
        games[recipe.id] = {**prev, "path": str(folder), "matched_by": signal}
    payload = {
        "_meta": {
            "host": socket.gethostname(),
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sd_root": str(dest.parents[2]) if len(dest.parents) >= 3 else "",
        },
        "games_dir": str(games_dir),
        "games": games,
        "unmatched": [{"path": str(f)} for f in unmatched],
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


def diff(before: dict, after: dict) -> dict:
    """Human-readable diff for the UI: added/changed/removed games."""
    b = before.get("games", {})
    a = after.get("games", {})
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = sorted(g for g in set(a) & set(b)
                     if a[g].get("path") != b[g].get("path"))
    return {"added": added, "changed": changed, "removed": removed}
