"""Decky plugin settings backup — the per-game TDP / GPU clock / power
profiles (and every other plugin's config).

The handheld's per-game TDP, GPU clock and power-governor profiles are NOT in
localconfig.vdf — they're written by Decky TDP plugins (SimpleDeckyTDP,
PowerTools, LegionGoRemapper, …) as JSON under `~/homebrew/settings/<Plugin>/`.
So restoring them after a reimage is just a file copy of that tree — no VDF
surgery. This also carries LegionGoRemapper's controller profiles,
brightness-bridge config, and anything else the user's plugins persist.

DEVICE-SPECIFIC — the important caveat: TDP wattage and clocks differ per
handheld (a Steam Deck profile is nonsense on a Legion Go 2), so backups are
keyed by HOSTNAME and only ever restored onto the same host. That also sidesteps
the fact that both devices share one Steam account (so a uid-keyed scheme would
collide).

Layout:  <local_payloads>/_state/decky/<hostname>/settings/<Plugin>/…
"""
from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path
from typing import Callable

SRC_REL = Path("homebrew") / "settings"       # under ~
BAK_SUFFIX = ".gfm-deckybak"


def hostname() -> str:
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def settings_dir(home: Path) -> Path:
    return home / SRC_REL


def backup_dir(dest_root: Path, host: str) -> Path:
    return dest_root / "decky" / host / "settings"


def _count(root: Path) -> int:
    n = 0
    for _dp, _dirs, files in os.walk(root):
        n += len(files)
    return n


def capture(home: Path, dest_root: Path, host: str | None = None,
            log: Callable[[str], None] = print) -> int:
    """Copy ~/homebrew/settings/ into the backup, keyed by hostname. Returns
    files captured (0 if Decky isn't installed / no settings yet)."""
    src = settings_dir(home)
    if not src.is_dir():
        return 0
    host = host or hostname()
    dest = backup_dir(dest_root, host)
    try:
        if dest.exists():
            shutil.rmtree(dest)          # replace this host's snapshot wholesale
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
    except OSError as e:
        log(f"      ! decky settings capture failed: {e}")
        return 0
    n = _count(dest)
    log(f"      + decky settings for {host}: {n} file(s)")
    return n


def hosts_available(dest_root: Path) -> list[str]:
    """Hostnames that have a Decky-settings backup."""
    root = dest_root / "decky"
    try:
        return sorted(d.name for d in root.iterdir()
                      if (d / "settings").is_dir())
    except OSError:
        return []


def restore(dest_root: Path, home: Path, host: str | None = None,
            log: Callable[[str], None] = print) -> int:
    """Restore this host's Decky settings backup over ~/homebrew/settings/.
    Only restores the backup matching THIS host (TDP is device-specific). The
    existing settings tree is moved aside to <name>.gfm-deckybak first. Returns
    files restored (0 if there's no backup for this host)."""
    host = host or hostname()
    src = backup_dir(dest_root, host)
    if not src.is_dir():
        return 0
    dst = settings_dir(home)
    try:
        if dst.exists():
            bak = dst.with_name(dst.name + BAK_SUFFIX)
            if bak.exists():
                shutil.rmtree(bak, ignore_errors=True)
            shutil.move(str(dst), str(bak))
            log(f"      ~ existing decky settings kept as {bak.name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    except OSError as e:
        log(f"      ! decky settings restore failed: {e}")
        return 0
    n = _count(dst)
    log(f"      + restored {n} decky settings file(s) for {host}")
    return n
