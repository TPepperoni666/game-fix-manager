"""wine_registry step: set registry values inside the game's Proton prefix.

Manifest form:
  { "type": "wine_registry",
    "key": "Software\\THQ\\Barnyard",
    "values": { "ControllerEnabled": 1, "SomeString": "hello" } }

int -> dword, str -> string. HKEY_CURRENT_USER only (that's user.reg — the
hive old games keep their settings in).

Wine's user.reg is a text file; sections look like:
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

from .. import detect
from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)


def _fmt_value(name: str, data) -> str:
    if isinstance(data, bool):
        data = int(data)
    if isinstance(data, int):
        return f'"{name}"=dword:{data:08x}'
    esc = str(data).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{name}"="{esc}"'


@register_step("wine_registry")
class WineRegistry:
    def __init__(self, step: dict):
        self.key = step["key"]
        self.values = step["values"]

    def _reg_file(self, ctx: Ctx):
        pfx = detect.find_prefix(ctx.recipe, ctx.steam_root)
        if pfx is None:
            raise StepError("no Proton prefix found — run the game once via "
                            "Steam first, then re-apply")
        reg = pfx / "user.reg"
        if not reg.is_file():
            raise StepError(f"user.reg missing in prefix {pfx}")
        return reg

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
        wanted = {n: _fmt_value(n, d) for n, d in self.values.items()}
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
        wanted = {n: _fmt_value(n, d) for n, d in self.values.items()}
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
