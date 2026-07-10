"""install_runner step: make sure a custom compat runner (e.g. a GE-Proton
build) is present in Steam's compatibilitytools.d, side-loading it from the
NAS local-payloads when it isn't.

Steam only auto-fetches OFFICIAL Proton — a recipe that pins a GE build would
otherwise show "missing" until the user runs ProtonUp-Qt. This drops a cached
copy in for them, which is exactly what you want after a fresh SteamOS reimage.

Manifest form:
  { "type": "install_runner", "name": "GE-Proton10-34" }

Source: <local_payloads_dir>/_runners/<name>/  — a shared spot on the NAS,
sibling to the per-recipe payload folders, holding the EXTRACTED runner folder.
Dest:   <steam_root>/compatibilitytools.d/<name>/
No-op if it's already installed. Never removed on revert (it's shared across
games, and Steam needs a restart to notice new runners — the apply run's Steam
bounce handles that)."""
from __future__ import annotations

import os
import shutil

from ..engine import APPLIED, NOT_APPLIED, Ctx, StepError, register_step


@register_step("install_runner")
class InstallRunner:
    def __init__(self, step: dict):
        self.name = step["name"]

    def _dest(self, ctx: Ctx):
        return None if ctx.steam_root is None else \
            ctx.steam_root / "compatibilitytools.d" / self.name

    def apply(self, ctx: Ctx) -> None:
        dest = self._dest(ctx)
        if dest is None:
            raise StepError("Steam root not found — cannot install a runner")
        if dest.is_dir():
            ctx.log(f"      = runner {self.name} already installed")
            return
        if ctx.local_payloads_dir is None:
            raise StepError(
                f"runner {self.name} not installed and no local-payloads dir to "
                "side-load from — install it via ProtonUp-Qt")
        runners = ctx.local_payloads_dir / "_runners"
        tarball = runners / f"{self.name}.tar.gz"
        folder = runners / self.name
        try:
            have_tar = tarball.is_file()
            have_dir = folder.is_dir()
        except OSError as e:
            raise StepError(f"local-payloads mount is down: {e}")
        if not (have_tar or have_dir):
            raise StepError(
                f"runner {self.name} not found ({tarball} or {folder}) — stage "
                "it with `gfm stage-runner` or install via ProtonUp-Qt")
        ctx.log(f"      + installing runner {self.name} into compatibilitytools.d")
        if not ctx.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if have_tar:
                import tarfile
                with tarfile.open(tarball) as tf:
                    try:  # 'data' filter (Py 3.12+) guards path traversal
                        tf.extractall(dest.parent, filter="data")
                    except TypeError:
                        tf.extractall(dest.parent)
            else:
                shutil.copytree(folder, dest)
            self._ensure_executable(dest)

    @staticmethod
    def _ensure_executable(dest) -> None:
        """A runner staged from a Windows/Syncthing copy loses its Unix exec
        bits (NTFS can't store them), so Steam couldn't launch it. Restore +x on
        the proton launcher and anything under a bin/ dir (and .sh scripts)."""
        p = dest / "proton"
        try:
            if p.is_file():
                os.chmod(p, os.stat(p).st_mode | 0o755)
        except OSError:
            pass
        for root_dir, _dirs, files in os.walk(dest):
            in_bin = "/bin" in root_dir.replace(os.sep, "/")
            for f in files:
                if in_bin or f.endswith(".sh"):
                    fp = os.path.join(root_dir, f)
                    try:
                        os.chmod(fp, os.stat(fp).st_mode | 0o111)
                    except OSError:
                        pass

    def verify(self, ctx: Ctx) -> str:
        dest = self._dest(ctx)
        return APPLIED if (dest is not None and dest.is_dir()) else NOT_APPLIED

    def revert(self, ctx: Ctx) -> None:
        ctx.log(f"      (install_runner) leaving {self.name} in "
                "compatibilitytools.d — it's shared across games")
