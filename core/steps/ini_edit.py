"""ini_edit step: set key=value pairs inside a game's INI config.

The many old games we fix keep their resolution/FOV/display settings in a
plain INI (True Crime: Streets of LA's TrueCrime.ini, countless others), and
widescreen-on-the-Deck usually means "set ScreenWidth/ScreenHeight and maybe
an FOV". This is the generic tool for that — the INI-file sibling of
wine_registry.

Manifest form:
  { "type": "ini_edit",
    "target": "{game_dir}/TrueCrime.ini",   # resolve_target templates apply:
                                             # {game_dir} {prefix} ~ etc.
    "values": {
      "Renderer": { "ScreenWidth": 1280, "ScreenHeight": 800 },
      "Game":     { "Subtitles": 1 }
    } }

Section-aware: values are grouped under [Section] headers. A missing key is
inserted into its section; a missing section is appended. Everything else in
the file — comments, ordering, other keys — is preserved. A .gfm-bak copy is
written before the first change.

Matching is case-insensitive on section and key names (INIs are inconsistent),
but the ON-DISK spelling is kept when a key already exists. Values are written
verbatim (1280, not "1280") since these configs are unquoted key=value.

target must exist. Games that only write their INI on first run should mark
this step "optional": true and be re-applied after one launch — same pattern
as wine_registry with a missing prefix.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)


def _is_section(line: str) -> str | None:
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        return s[1:-1].strip()
    return None


def _key_of(line: str) -> str | None:
    """The key on a 'key=value' line (not a comment), else None."""
    s = line.strip()
    if not s or s.startswith((";", "#")) or "=" not in s:
        return None
    return s.split("=", 1)[0].strip()


@register_step("ini_edit")
class IniEdit:
    def __init__(self, step: dict):
        self.target = step["target"]
        raw = step.get("values", {})
        # Normalise to {section: {key: value}}. Keys/sections lower-cased for
        # matching; original spelling recovered from `disp`.
        self.values: dict = {}
        self.disp: dict = {}
        for section, kv in raw.items():
            sl = section.lower()
            self.values.setdefault(sl, {})
            self.disp.setdefault(sl, section)
            for k, v in kv.items():
                self.values[sl][k.lower()] = v
                self.disp[(sl, k.lower())] = k
        if not self.values:
            raise StepError("ini_edit: no values to set")

    def _file(self, ctx: Ctx) -> Path:
        p = ctx.resolve_target(self.target)
        if not p.is_file():
            raise StepError(f"ini_edit: {p} not found — if the game writes its "
                            "config on first run, launch it once and re-apply")
        return p

    @staticmethod
    def _fmt(value) -> str:
        if isinstance(value, bool):
            return str(int(value))
        return str(value)

    def _read(self, ctx: Ctx) -> dict[str, str]:
        """Current value per '<section>\\x00<key>' for everything we manage."""
        p = ctx.resolve_target(self.target)
        out: dict[str, str] = {}
        try:
            lines = p.read_text(encoding="utf-8",
                                errors="surrogateescape").splitlines()
        except OSError:
            return out
        cur = ""
        for line in lines:
            sec = _is_section(line)
            if sec is not None:
                cur = sec.lower()
                continue
            key = _key_of(line)
            if key is None:
                continue
            kl = key.lower()
            if cur in self.values and kl in self.values[cur]:
                out[f"{cur}\x00{kl}"] = line.split("=", 1)[1].strip()
        return out

    def _wanted(self) -> dict[str, str]:
        return {f"{s}\x00{k}": self._fmt(v)
                for s, kv in self.values.items() for k, v in kv.items()}

    def apply(self, ctx: Ctx) -> None:
        f = self._file(ctx)
        lines = f.read_text(encoding="utf-8",
                            errors="surrogateescape").splitlines()

        # Which (section,key) still need writing, per section.
        remaining = {s: dict(kv) for s, kv in self.values.items()}
        out: list[str] = []
        cur = ""
        for line in lines:
            sec = _is_section(line)
            if sec is not None:
                # Leaving a section: append any keys it was missing.
                self._flush_missing(out, cur, remaining, ctx)
                cur = sec.lower()
                out.append(line)
                continue
            key = _key_of(line)
            if key is not None and cur in remaining \
                    and key.lower() in remaining[cur]:
                kl = key.lower()
                val = self._fmt(remaining[cur].pop(kl))
                out.append(f"{key}={val}")
                ctx.log(f"      ✏ [{self.disp.get(cur, cur)}] {key}={val}")
            else:
                out.append(line)
        # End of file: flush the current section, then any whole new sections.
        self._flush_missing(out, cur, remaining, ctx)
        for sl, kv in remaining.items():
            if not kv:
                continue
            out.append(f"[{self.disp.get(sl, sl)}]")
            for kl, v in kv.items():
                name = self.disp.get((sl, kl), kl)
                out.append(f"{name}={self._fmt(v)}")
                ctx.log(f"      + [{self.disp.get(sl, sl)}] {name}")

        if not ctx.dry_run:
            # First write wins: never clobber the true original on re-apply.
            bak = f.with_name(f.name + ".gfm-bak")
            if not bak.exists():
                shutil.copy2(f, bak)
            f.write_text("\n".join(out) + "\n", encoding="utf-8",
                         errors="surrogateescape")

    def _flush_missing(self, out: list, section: str, remaining: dict,
                       ctx: Ctx) -> None:
        """Insert keys not found in a section, at its end (before the blank
        line that usually precedes the next header, if any)."""
        kv = remaining.get(section)
        if not kv:
            return
        # trim trailing blanks so inserted keys stay inside the section
        trailing = []
        while out and out[-1].strip() == "":
            trailing.append(out.pop())
        for kl, v in list(kv.items()):
            name = self.disp.get((section, kl), kl)
            out.append(f"{name}={self._fmt(v)}")
            ctx.log(f"      + [{self.disp.get(section, section)}] {name}")
        remaining[section] = {}
        out.extend(trailing)

    def verify(self, ctx: Ctx) -> str:
        try:
            self._file(ctx)
        except StepError:
            return NOT_APPLIED
        cur, want = self._read(ctx), self._wanted()
        done = sum(1 for k, v in want.items() if cur.get(k) == v)
        if done == len(want):
            return APPLIED
        return NOT_APPLIED if done == 0 else PARTIAL

    def revert(self, ctx: Ctx) -> None:
        f = ctx.resolve_target(self.target)
        bak = f.with_name(f.name + ".gfm-bak")
        if bak.is_file() and not ctx.dry_run:
            shutil.copy2(bak, f)
            bak.unlink()
