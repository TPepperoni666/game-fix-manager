"""pitcrew_compile step: install The Crew mods by driving PitCrewCompiler.

PitCrew (github.com/Telonof/PitCrew) is a mod loader for The Crew 1/2. It is
an OFFLINE file tool, not a runtime hook — it merges mod archives into the
game's startup.fat/.dat and writes loose mod data under data_win32/mods/.
That's why we run the NATIVE LINUX build even though the game runs under
Proton: the game only ever sees the resulting files on disk, and a native
binary means no .NET-inside-the-prefix fight and a CLI we can actually drive.

How the pieces fit:

  <game_dir>/Data_Win32/            the game's data folder (case varies)
      startup.fat / startup.dat     what the compiler merges into (it makes
                                    its own backup before touching them)
      mods/                         where we drop the mod's files
      gfm-pitcrew.xml               the manifest we generate, then compile

Manifest form (Docs/English/CLI_Usage.md), packageversion 5 = The Crew 1,
6 = The Crew 2:

    <instance packageversion="5">
      <mod id="FTIWPlethoraRace">
        <file priority="998" loc="mods/FTIWPlethoraRace_aidata.xml" />
        ...
      </mod>
    </instance>

`loc` is relative to the manifest. A bare name (no extension) means a
.dat/.fat PAIR; an .xml means a binary that needs merging.

We do NOT hand-maintain that file list. Every PitCrew mod ships a .mdata
alongside its data, and the .mdata already contains the exact <file> nodes
with their priorities — that IS the mod half of the manifest. So this step
reads the .mdata and re-emits its entries with loc rewritten to mods/<name>.
A mod update that adds or drops files therefore needs no recipe change.

Manifest form:
  { "type": "pitcrew_compile",
    "payload": "racesaplenty",       # payload subdir holding .mdata + data
    "data_dir": "Data_Win32",        # optional, auto-detected case-insens.
    "package_version": 5,            # 5 = The Crew, 6 = The Crew 2
    "compiler": "PitCrewCompiler" }  # optional, resolved via tools dir

The compiler binary is staged once (like a Proton runner) rather than living
in the recipe — see core/pitcrew.py for where it's looked up. The zip from
GitHub carries no executable bit (it's built on Windows), so we chmod it.

Re-running is safe and is the normal path: after a redeploy from the NAS the
game files are clean again, and applying the recipe recompiles the mod in.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ..engine import (APPLIED, NOT_APPLIED, PARTIAL, Ctx, StepError,
                      register_step)

MANIFEST_NAME = "gfm-pitcrew.xml"
MODS_SUBDIR = "mods"

# The compiler refuses to run while the game is up, and merging into
# startup.dat under a live game would corrupt it either way.
_GAME_PROCS = ("TheCrew.exe", "Crew.exe", "TheCrew2.exe")


def _resolve_ci(base: Path, rel: str) -> Path | None:
    """Resolve a relative path under base, one segment at a time, matching
    case-insensitively. Needed because the game ships 'Data_Win32' but
    releases and repacks disagree on the casing, ext4 is case-SENSITIVE, and
    the path is nested ('the crew/Data_Win32') so a flat scan won't do."""
    cur = base
    for seg in Path(rel).parts:
        nxt = cur / seg
        if nxt.is_dir():
            cur = nxt
            continue
        try:
            hit = next((c for c in cur.iterdir()
                        if c.is_dir() and c.name.lower() == seg.lower()), None)
        except OSError:
            return None
        if hit is None:
            return None
        cur = hit
    return cur


def _find_data_dir(game_dir: Path, want: str | None) -> Path:
    """The game's data folder — the one holding startup.fat/.dat."""
    rels = [want] if want else ["Data_Win32", "the crew/Data_Win32"]
    for rel in rels:
        if rel and (hit := _resolve_ci(game_dir, rel)) is not None:
            return hit
    raise StepError(
        f"pitcrew_compile: no data folder ({', '.join(r for r in rels if r)}) "
        f"under {game_dir} — check the game path in the SD map")


def _mod_entries(mdata: Path) -> tuple[str, list[tuple[str, str]]]:
    """(mod_id, [(priority, loc), ...]) read from a PitCrew .mdata.

    mod_id is the .mdata's stem — PitCrew keys mods by filename, which is
    what its own 'Mod ID (File Name)' field means."""
    try:
        root = ET.parse(mdata).getroot()
    except (ET.ParseError, OSError) as e:
        raise StepError(f"pitcrew_compile: cannot read {mdata.name}: {e}")
    out = []
    for f in root.findall("./files/file"):
        loc = f.get("loc")
        if not loc:
            continue
        out.append((f.get("priority", "998"), loc))
    if not out:
        raise StepError(f"pitcrew_compile: {mdata.name} lists no files — "
                        f"either it's not a PitCrew mod or it's incomplete")
    return mdata.stem, out


def _build_manifest(mod_id: str, entries: list[tuple[str, str]],
                    package_version: int) -> str:
    inst = ET.Element("instance", {"packageversion": str(package_version)})
    mod = ET.SubElement(inst, "mod", {"id": mod_id})
    for priority, loc in entries:
        # loc is relative to the manifest, which sits in the data dir, and we
        # stage the mod's files into <data dir>/mods/.
        ET.SubElement(mod, "file",
                      {"priority": priority,
                       "loc": f"{MODS_SUBDIR}/{Path(loc).name}"})
    ET.indent(inst, space="  ")
    return ET.tostring(inst, encoding="unicode") + "\n"


@register_step("pitcrew_compile")
class PitCrewCompile:
    def __init__(self, step: dict):
        self.payload = step["payload"]
        self.data_dir = step.get("data_dir")
        self.package_version = int(step.get("package_version", 5))
        self.compiler = step.get("compiler", "PitCrewCompiler")

    # -- helpers ---------------------------------------------------------
    def _src(self, ctx: Ctx) -> Path:
        src = ctx.payload_path(self.payload)
        if not src.is_dir():
            raise StepError(
                f"pitcrew_compile: mod payload '{self.payload}' not found at "
                f"{src} — drop the unpacked mod (its .mdata plus data files) "
                f"there and re-apply")
        return src

    def _mdata(self, src: Path) -> Path:
        found = sorted(src.glob("*.mdata"))
        if not found:
            raise StepError(f"pitcrew_compile: no .mdata in {src} — a PitCrew "
                            f"mod always ships one; is the zip fully unpacked?")
        if len(found) > 1:
            raise StepError(f"pitcrew_compile: {len(found)} .mdata files in "
                            f"{src} — one mod per payload folder, please")
        return found[0]

    def _compiler_bin(self, ctx: Ctx) -> Path:
        from .. import pitcrew
        exe = pitcrew.find_compiler(ctx, self.compiler)
        if exe is None:
            raise StepError(
                "pitcrew_compile: PitCrewCompiler not staged. Download the "
                "Linux build from github.com/Telonof/PitCrew/releases and "
                "unpack it to " + str(pitcrew.tools_dir(ctx) / "PitCrew-Linux"))
        return exe

    def _game_running(self) -> str | None:
        """PitCrewCompiler bails if the game is up, and rightly so. Catch it
        ourselves so the message says what to do about it."""
        try:
            out = subprocess.run(["pgrep", "-af", "exe"], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None          # can't tell; let the compiler decide
        for proc in _GAME_PROCS:
            if proc.lower() in out.lower():
                return proc
        return None

    # -- step API --------------------------------------------------------
    def apply(self, ctx: Ctx) -> None:
        src = self._src(ctx)
        mdata = self._mdata(src)
        mod_id, entries = _mod_entries(mdata)
        data = _find_data_dir(ctx.game_dir, self.data_dir)

        if not (data / "startup.fat").is_file() and \
           not (data / "startup.dat").is_file():
            raise StepError(
                f"pitcrew_compile: no startup.fat/.dat in {data} — the "
                f"compiler merges into those, so it cannot run without them")

        running = self._game_running()
        if running:
            raise StepError(f"pitcrew_compile: {running} is running — close "
                            f"the game and re-apply")

        exe = self._compiler_bin(ctx)

        mods = data / MODS_SUBDIR
        mods.mkdir(parents=True, exist_ok=True)
        for f in sorted(src.iterdir()):
            if f.is_file() and f.suffix != ".mdata":
                shutil.copy2(f, mods / f.name)

        manifest = data / MANIFEST_NAME
        manifest.write_text(_build_manifest(mod_id, entries,
                                            self.package_version),
                            encoding="utf-8")

        try:
            r = subprocess.run([str(exe), str(manifest)], cwd=str(data),
                               capture_output=True, text=True, timeout=1800)
        except OSError as e:
            raise StepError(f"pitcrew_compile: cannot run {exe}: {e}")
        except subprocess.TimeoutExpired:
            raise StepError("pitcrew_compile: compiler timed out after 30min")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-6:]
            raise StepError("pitcrew_compile: compiler failed:\n    "
                            + "\n    ".join(tail))

    def verify(self, ctx: Ctx) -> str:
        """Compiled = our manifest is there AND every file it names is in
        mods/. We can't inspect inside startup.dat, so this checks what we
        control; a redeploy wipes both, which is exactly when we want to
        report NOT_APPLIED and recompile."""
        try:
            src = self._src(ctx)
            mdata = self._mdata(src)
            _mod_id, entries = _mod_entries(mdata)
            data = _find_data_dir(ctx.game_dir, self.data_dir)
        except StepError:
            return NOT_APPLIED
        if not (data / MANIFEST_NAME).is_file():
            return NOT_APPLIED
        mods = data / MODS_SUBDIR
        want = {Path(loc).name for _p, loc in entries}
        # A bare loc means a .dat/.fat pair, so check both halves exist.
        missing = 0
        for name in want:
            if Path(name).suffix:
                if not (mods / name).is_file():
                    missing += 1
            elif not ((mods / f"{name}.dat").is_file()
                      and (mods / f"{name}.fat").is_file()):
                missing += 1
        if missing == 0:
            return APPLIED
        return PARTIAL if missing < len(want) else NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        """Remove what we staged. This does NOT unmerge startup.dat — the
        compiler backs that up itself, and the real revert for a modded Crew
        is a redeploy from the NAS, which is the tool's whole model."""
        try:
            data = _find_data_dir(ctx.game_dir, self.data_dir)
            src = self._src(ctx)
            mdata = self._mdata(src)
            _mod_id, entries = _mod_entries(mdata)
        except StepError:
            return
        (data / MANIFEST_NAME).unlink(missing_ok=True)
        mods = data / MODS_SUBDIR
        for _p, loc in entries:
            name = Path(loc).name
            if Path(name).suffix:
                (mods / name).unlink(missing_ok=True)
            else:
                (mods / f"{name}.dat").unlink(missing_ok=True)
                (mods / f"{name}.fat").unlink(missing_ok=True)
