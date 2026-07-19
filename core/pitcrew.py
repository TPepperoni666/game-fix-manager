"""Where the PitCrew toolchain lives, and how we find it.

PitCrew is staged ONCE and shared by every recipe that needs it, the same way
Proton runners are — it isn't per-game, and at ~25MB it has no business being
copied into each recipe's payload.

Lookup order (first hit wins):
  1. $GFM_PITCREW           — explicit override, for testing
  2. <SD>/steamos_restore/game_fixes/tools/PitCrew-Linux/
  3. ~/PitCrew-Linux/, ~/Applications/PitCrew-Linux/

The GitHub zip is built on Windows and so carries no executable bit — every
file unpacks as rw-r--r--. Running it then fails with a bare Permission
denied that looks like a much worse problem than it is, so find_compiler()
sets the bit itself rather than making anyone debug that.

Needs .NET Runtime 8.x on the host. On SteamOS that means a flatpak or the
self-contained build; the step surfaces the error if it's missing.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from . import store

TOOLS_REL = "steamos_restore/game_fixes/tools"
DIR_NAME = "PitCrew-Linux"


def tools_dir(ctx=None) -> Path:
    """Where staged tools go — next to the store mirror, so they survive a
    reimage and ride along with everything else on the card."""
    for sd in store.sd_card_roots():
        return sd / TOOLS_REL
    return store.CONFIG_DIR / "tools"


def _candidates(ctx=None) -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("GFM_PITCREW")
    if env:
        out.append(Path(env))
    for sd in store.sd_card_roots():
        out.append(sd / TOOLS_REL / DIR_NAME)
    home = Path.home()
    out += [home / DIR_NAME, home / "Applications" / DIR_NAME]
    return out


def _ensure_exec(p: Path) -> None:
    """Give the binary its executable bit back (the zip is built on Windows
    and loses it). Best effort — a read-only mount isn't fatal here, the
    caller will surface the real error if it can't run."""
    try:
        mode = p.stat().st_mode
        p.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def find_compiler(ctx=None, name: str = "PitCrewCompiler") -> Path | None:
    """The PitCrewCompiler binary, made executable, or None if not staged."""
    for root in _candidates(ctx):
        exe = root / name
        if exe.is_file():
            _ensure_exec(exe)
            # libminilzo sits beside it and is dlopen'd for The Crew's
            # compression; it needs the bit too.
            lib = root / "libminilzo"
            if lib.is_file():
                _ensure_exec(lib)
            return exe
    return None


def installed(ctx=None) -> bool:
    return find_compiler(ctx) is not None
