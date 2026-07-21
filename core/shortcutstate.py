"""Reimage-surviving record of the NON-recipe Steam shortcuts on THIS device.

The gap this closes: recipe games recreate their own shortcuts on Apply Fixes
(the steam_shortcut step forces the gospel appid), so they survive a reimage.
But GENERIC games — deployed from _games or adopted from a hand-added Steam
shortcut — have no recipe to rebuild them, their entry in Steam's shortcuts.vdf
is wiped on reimage, and the config that remembered them (~/.config) is wiped
too. So after a reimage their files and prefix survived but the Steam entry was
gone, and you had to re-deploy each one by hand.

Here we save each generic shortcut's FULL definition (appid, name, exe,
start dir, launch options, runner) somewhere that survives a reimage: under
_state on the NAS/SD, keyed by hostname so a Deck and a Legion Go keep
separate sets — exactly like the Decky settings backup. Restore rewrites them
into the fresh shortcuts.vdf, forcing the pinned appid so the imported prefix,
saves and art line back up.

The appid stays authoritative in the gospel registry (identity); this file is
the per-device shortcut BODY. We store the game's folder name alongside the
absolute exe so restore can relocate it if the SD card mounts at a different
path after the reimage.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

STATE_REL = Path("_state") / "shortcuts"


def hostname() -> str:
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def manifest_path(local_payloads: Path, host: str | None = None) -> Path:
    return local_payloads / STATE_REL / f"{host or hostname()}.json"


def load(local_payloads: Path, host: str | None = None) -> list[dict]:
    """Every saved generic shortcut for this device, or []."""
    p = manifest_path(local_payloads, host)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("shortcuts", data) if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else []


def record(local_payloads: Path, entry: dict, host: str | None = None) -> None:
    """Upsert one shortcut by appid, preserving the rest. Idempotent — a
    re-deploy of the same game just refreshes its row."""
    if not entry.get("appid"):
        return
    p = manifest_path(local_payloads, host)
    current = load(local_payloads, host)
    current = [e for e in current if e.get("appid") != entry["appid"]]
    current.append(entry)
    current.sort(key=lambda e: str(e.get("name", "")).lower())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"host": host or hostname(),
                             "shortcuts": current}, indent=2) + "\n",
                 encoding="utf-8")


def remove(local_payloads: Path, appid, host: str | None = None) -> None:
    """Forget a shortcut (e.g. after reclaim deletes the game)."""
    p = manifest_path(local_payloads, host)
    current = [e for e in load(local_payloads, host)
               if str(e.get("appid")) != str(appid)]
    if not p.parent.exists():
        return
    p.write_text(json.dumps({"host": host or hostname(),
                             "shortcuts": current}, indent=2) + "\n",
                 encoding="utf-8")


def resolve_exe(entry: dict, sd_games_dirs: list[Path]) -> Path | None:
    """Where this game's exe actually is right now. Prefer the recorded
    absolute path (the common case — same SD, same mount); fall back to
    relocating by folder name under a live Games dir if the card came back at a
    different path. None if the files aren't present, so restore skips it
    rather than pointing a shortcut at nothing."""
    exe = entry.get("exe")
    if exe and Path(exe).is_file():
        return Path(exe)
    folder, rel = entry.get("folder"), entry.get("exe_rel")
    if folder and rel:
        for games in sd_games_dirs:
            cand = games / folder / rel
            if cand.is_file():
                return cand
    return None
