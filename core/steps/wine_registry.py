"""wine_registry step: set registry values inside the game's Proton prefix.

Manifest form:
  { "type": "wine_registry",
    "hive": "user",                       # or "system" (HKLM), default "user"
    "key": "Software\\THQ\\Barnyard",
    "values": { "ControllerEnabled": 1,
                "PATH_APPLICATION": "{game_dir_win}" } }

int -> dword, str -> string. String values expand these templates at apply
time: {game_dir}, {game_dir_win} (Linux path -> Z:\ escaped-backslash form),
{home}. That's what makes the same recipe work on any install path — the
game location comes from the shortcut, not the manifest.

Hives map to:
  user   -> user.reg    (HKCU)
  system -> system.reg  (HKLM — WOW6432Node paths for 32-bit games)

Both are text files with sections like:
  [Software\\THQ\\Barnyard] 1658323400
  "ControllerEnabled"=dword:00000001
Backslashes in section names are doubled. Edits are safe while the game is
not running (wineserver exits seconds after the game does and re-reads the
file on next start). A .gfm-bak copy is written before every change.

The prefix must exist — i.e. the game must have been RUN once through
Proton. Recipes should mark this step "optional": true so a missing prefix
skips with a warning instead of failing the whole recipe; re-apply after
first launch picks it up.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import detect
from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)

_HIVES = {"user": "user.reg", "system": "system.reg"}


def _fmt_value(name: str, data) -> str:
    if isinstance(data, bool):
        data = int(data)
    if isinstance(data, int):
        return f'"{name}"=dword:{data:08x}'
    esc = str(data).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{name}"="{esc}"'


def _wine_z_path(p: Path) -> str:
    """Linux path -> Wine Z:\\...\\... string (backslashes only)."""
    return "Z:" + str(p).replace("/", "\\")


def _expand(value, ctx: Ctx):
    if not isinstance(value, str):
        return value
    return (value
            .replace("{game_dir_win}", _wine_z_path(ctx.game_dir))
            .replace("{game_dir}", str(ctx.game_dir))
            .replace("{home}", str(Path.home())))


@register_step("wine_registry")
class WineRegistry:
    def __init__(self, step: dict):
        self.key = step["key"]
        self.values = step["values"]
        self.hive = step.get("hive", "user")
        if self.hive not in _HIVES:
            raise StepError(f"wine_registry: unknown hive '{self.hive}' "
                            f"(expected user or system)")

    def _reg_file(self, ctx: Ctx):
        pfx = detect.find_prefix(ctx.recipe, ctx.steam_root)
        if pfx is None:
            raise StepError("no Proton prefix found — run the game once via "
                            "Steam first, then re-apply")
        reg = pfx / _HIVES[self.hive]
        if not reg.is_file():
            raise StepError(f"{_HIVES[self.hive]} missing in prefix {pfx}")
        return reg

    def _resolved_values(self, ctx: Ctx) -> dict:
        return {n: _expand(d, ctx) for n, d in self.values.items()}

    def _header(self) -> str:
        return "[" + self.key.replace("\\", "\\\\") + "]"

    def _section_span(self, lines: list[str]) -> tuple[int, int] | None:
        """(header_index, end_index_exclusive) of our section, or None."""
        header = self._header()
        for i, line in enumerate(lines):
            if line.startswith(header) and (len(line) == len(header)
                                            or line[len(header)] in " \t"):
                end = i + 1
                while end < len(lines) and not lines[end].startswith("["):
                    end += 1
                return i, end
        return None

    def _current(self, lines: list[str]) -> dict[str, str | None]:
        """Managed value name -> current raw line (None = absent)."""
        state: dict[str, str | None] = {n: None for n in self.values}
        span = self._section_span(lines)
        if span is None:
            return state
        for line in lines[span[0] + 1:span[1]]:
            for name in self.values:
                if line.startswith(f'"{name}"='):
                    state[name] = line.strip()
        return state

    def apply(self, ctx: Ctx) -> None:
        reg = self._reg_file(ctx)
        lines = reg.read_text(encoding="utf-8",
                              errors="surrogateescape").splitlines()
        wanted = {n: _fmt_value(n, d)
                  for n, d in self._resolved_values(ctx).items()}
        current = self._current(lines)
        if all(current[n] == w for n, w in wanted.items()):
            ctx.log(f"      = registry values already set in {self.key}")
            return

        span = self._section_span(lines)
        if span is None:
            ctx.log(f"      + creating registry key {self.key}")
            lines += ["", f"{self._header()} {int(time.time())}",
                      *wanted.values()]
        else:
            head, end = span
            body = lines[head + 1:end]
            for name, formatted in wanted.items():
                for j, line in enumerate(body):
                    if line.startswith(f'"{name}"='):
                        body[j] = formatted
                        break
                else:
                    body.insert(0, formatted)
                ctx.log(f"      ✏ {self.key}\\{name}")
            lines[head + 1:end] = body

        if not ctx.dry_run:
            shutil.copy2(reg, reg.with_suffix(".reg.gfm-bak"))
            reg.write_text("\n".join(lines) + "\n", encoding="utf-8",
                           errors="surrogateescape")

    def verify(self, ctx: Ctx) -> str:
        try:
            reg = self._reg_file(ctx)
        except StepError:
            return NOT_APPLIED
        lines = reg.read_text(encoding="utf-8",
                              errors="surrogateescape").splitlines()
        wanted = {n: _fmt_value(n, d)
                  for n, d in self._resolved_values(ctx).items()}
        current = self._current(lines)
        done = sum(1 for n, w in wanted.items() if current[n] == w)
        if done == len(wanted):
            return APPLIED
        return NOT_APPLIED if done == 0 else PARTIAL

    def revert(self, ctx: Ctx) -> None:
        try:
            reg = self._reg_file(ctx)
        except StepError:
            return
        lines = reg.read_text(encoding="utf-8",
                              errors="surrogateescape").splitlines()
        span = self._section_span(lines)
        if span is None:
            return
        head, end = span
        managed = tuple(f'"{n}"=' for n in self.values)
        body = [l for l in lines[head + 1:end] if not l.startswith(managed)]
        ctx.log(f"      - removing managed values from {self.key}")
        lines[head + 1:end] = body
        if not ctx.dry_run:
            shutil.copy2(reg, reg.with_suffix(".reg.gfm-bak"))
            reg.write_text("\n".join(lines) + "\n", encoding="utf-8",
                           errors="surrogateescape")
