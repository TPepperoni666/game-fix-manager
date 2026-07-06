"""systemd_unit step: install a unit file from the payload and enable it.

Manifest form:
  { "type": "systemd_unit", "unit": "payload/tcu-network.service",
    "scope": "system", "enable": true, "start": false }

scope "user"   -> ~/.config/systemd/user, plain systemctl --user
scope "system" -> /etc/systemd/system, commands run through sudo when not root
                  (SteamOS: needs a sudo password set once via `passwd`)

Graceful degradation: on hosts without systemctl (Windows dev box) the unit
file is still installed/verified by content; enable/start are skipped with a
warning. On the Deck this never triggers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)
from .copy_files import _same_file

SCOPES = {
    "system": {"dir": "/etc/systemd/system", "ctl": ["systemctl"], "root": True},
    "user": {"dir": "~/.config/systemd/user", "ctl": ["systemctl", "--user"], "root": False},
}


def _have_systemctl() -> bool:
    return shutil.which("systemctl") is not None


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _run(argv: list[str], ctx: Ctx) -> subprocess.CompletedProcess | None:
    ctx.log(f"      $ {' '.join(argv)}")
    if ctx.dry_run:
        return None
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise StepError(f"command failed ({result.returncode}): {' '.join(argv)}\n"
                        f"{(result.stderr or result.stdout).strip()}")
    return result


@register_step("systemd_unit")
class SystemdUnit:
    def __init__(self, step: dict):
        self.unit = step["unit"]
        self.scope = step.get("scope", "user")
        if self.scope not in SCOPES:
            raise StepError(f"systemd_unit: unknown scope '{self.scope}'")
        self.enable = step.get("enable", True)
        self.start = step.get("start", False)

    def _unit_dir(self) -> Path:
        override = os.environ.get(f"GFM_SYSTEMD_{self.scope.upper()}_DIR")
        return Path(override or SCOPES[self.scope]["dir"]).expanduser()

    def _sudo(self) -> list[str]:
        return ["sudo"] if SCOPES[self.scope]["root"] and not _is_root() else []

    def _ctl(self) -> list[str]:
        return self._sudo() + SCOPES[self.scope]["ctl"]

    def _paths(self, ctx: Ctx) -> tuple[Path, Path]:
        src = ctx.payload_path(self.unit)
        return src, self._unit_dir() / src.name

    def apply(self, ctx: Ctx) -> None:
        src, dst = self._paths(ctx)
        name = dst.name

        if _same_file(src, dst):
            ctx.log(f"      = {name} (already installed)")
        elif self._sudo():
            _run([*self._sudo(), "install", "-m", "644", str(src), str(dst)], ctx)
        else:
            ctx.log(f"      + {dst}")
            if not ctx.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        if not _have_systemctl():
            ctx.log("      ! systemctl not found — unit installed but not "
                    "enabled (fine on a dev box, not on the Deck)")
            return
        _run([*self._ctl(), "daemon-reload"], ctx)
        if self.enable:
            _run([*self._ctl(), "enable", name], ctx)
        if self.start:
            _run([*self._ctl(), "start", name], ctx)

    def verify(self, ctx: Ctx) -> str:
        src, dst = self._paths(ctx)
        if not _same_file(src, dst):
            return NOT_APPLIED
        if not (self.enable and _have_systemctl()):
            return APPLIED
        result = subprocess.run([*SCOPES[self.scope]["ctl"], "is-enabled", dst.name],
                                capture_output=True, text=True)
        return APPLIED if result.stdout.strip() == "enabled" else PARTIAL

    def revert(self, ctx: Ctx) -> None:
        src, dst = self._paths(ctx)
        if not _same_file(src, dst):
            ctx.log(f"      = {dst.name} not installed, nothing to revert")
            return
        if _have_systemctl():
            for verb in (["stop"] if self.start else []) + (["disable"] if self.enable else []):
                _run([*self._ctl(), verb, dst.name], ctx)
        if self._sudo():
            _run([*self._sudo(), "rm", str(dst)], ctx)
        else:
            ctx.log(f"      - {dst}")
            if not ctx.dry_run:
                dst.unlink()
        if _have_systemctl():
            _run([*self._ctl(), "daemon-reload"], ctx)
