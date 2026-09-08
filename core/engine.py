"""Step runner: apply / verify / revert a recipe's steps in order.

Every step type registers a class with three methods:
  apply(ctx)  -> None      idempotent — safe to run twice
  verify(ctx) -> str       one of APPLIED / NOT_APPLIED / PARTIAL
  revert(ctx) -> None      undo, restoring *.gfm-orig backups where present
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .manifest import Recipe

APPLIED = "applied"
NOT_APPLIED = "not_applied"
PARTIAL = "partial"

BACKUP_SUFFIX = ".gfm-orig"

_REGISTRY: dict[str, type] = {}

# Platform-aware user-directory tokens, and where each one lives inside a
# Proton prefix relative to the steamuser home.
#
# The point of these is that ONE template has to resolve on BOTH platforms.
# saves.py captures by template and re-resolves it on the target machine, so
# "{documents}/My Game/save" captured from inside a Proton prefix on the Deck
# restores to the real Documents folder on Windows. Hardcoding
# {prefix}/drive_c/... instead would capture fine and never restore anywhere
# a Windows game would look.
_USER_DIRS = {
    "{localappdata}": ("AppData", "Local"),
    "{appdata}": ("AppData", "Roaming"),
    "{documents}": ("Documents",),
    "{savedgames}": ("Saved Games",),
}
# Shell-folder registry names for the two that aren't plain env vars.
_SHELL_FOLDER = {
    "{documents}": "Personal",
    "{savedgames}": "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}",
}


def _windows_user_dir(token: str) -> str:
    """The REAL Windows folder for a user-dir token.

    Documents and Saved Games come from the shell-folder registry rather than
    being built from the home directory, because REDIRECTION IS NORMAL —
    Tony's Documents live under OneDrive. Assuming ~/Documents would write
    somewhere the game never reads, and the failure is silent: the restore
    reports success and the save simply isn't there."""
    env = {"{localappdata}": "LOCALAPPDATA", "{appdata}": "APPDATA"}.get(token)
    if env and os.environ.get(env):
        return os.environ[env]
    name = _SHELL_FOLDER.get(token)
    if name:
        try:
            import winreg
            key = (r"Software\Microsoft\Windows\CurrentVersion"
                   r"\Explorer\User Shell Folders")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                raw, _ = winreg.QueryValueEx(k, name)
                if raw:
                    return os.path.expandvars(raw)
        except OSError:
            pass  # fall through to the home-relative guess
    return str(Path.home().joinpath(*_USER_DIRS[token]))


def _xdg(var: str, fallback: str) -> str:
    """XDG base dir, honouring the env var the spec says owns it.

    Only an ABSOLUTE value counts — the spec says a relative one is invalid and
    must be ignored, and treating one as valid would silently scatter saves
    into the current working directory."""
    raw = os.environ.get(var, "")
    if raw and os.path.isabs(raw):
        return raw
    return str(Path.home() / fallback)


def register_step(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        return cls
    return deco


class StepError(Exception):
    pass


@dataclass
class Ctx:
    recipe: Recipe
    game_dir: Path
    dry_run: bool = False
    log: Callable[[str], None] = print
    steam_root: Path | None = None
    # Optional folder (NAS mount, SD card, wherever) holding local-only
    # payloads that override the recipe's committed/fetched files. Layout:
    # <local_payloads_dir>/<recipe_id>/<same rel path>. Lets copyrighted or
    # custom binaries live off git entirely — a file here wins silently.
    local_payloads_dir: Path | None = None
    # steps that need Steam closed queue work here; the caller batches every
    # queued write behind a single close-Steam/restart-Steam at the end
    deferred_vdf_writes: list = field(default_factory=list)

    def resolve_target(self, template: str) -> Path:
        """Expand path templates in a manifest 'to' field.
        {game_dir}  — the game install directory
        {prefix}    — the game's Proton prefix (…/compatdata/<id>/pfx)
        {prefix_localappdata} — drive_c LocalAppData for steamuser
        {localappdata} {appdata} {documents} {savedgames}
                    — the user folder WHEREVER THE GAME ACTUALLY RUNS: the
                      prefix's copy under Proton, the real Windows folder on
                      Windows. One recipe then names a save location that
                      resolves on both platforms, which is what lets a save
                      captured on the Deck restore on the HTPC. On Windows,
                      Documents and Saved Games come from the shell-folder
                      registry, so OneDrive redirection is honoured.
        {xdg_data} {xdg_config}
                    — where a NATIVE Linux build keeps its saves/settings
                      (~/.local/share, ~/.config), and the Windows folders a
                      port of the same program uses (LocalAppData, Roaming).
                      Never rewritten into the prefix — that is the point.
        ~           — the user's home dir (leading only)
        Prefix templates need the game to have run once; a StepError is
        raised (usually caught by an optional step) if no prefix exists yet.
        """
        out = template
        for token, sub in _USER_DIRS.items():
            if token not in out:
                continue
            if os.name == "nt":
                out = out.replace(token, _windows_user_dir(token))
            else:
                # Under Proton the game only ever sees the prefix's copy, so
                # rewrite to a {prefix} path and let the block below expand it.
                out = out.replace(
                    token, "{prefix}/drive_c/users/steamuser/" + "/".join(sub))
        if "{programfilesx86}" in out:
            # Not a user folder, but the same cross-platform problem: Ubisoft
            # Connect keeps every game's saves under
            # Program Files (x86)/Ubisoft/Ubisoft Game Launcher/savegames,
            # which is inside the prefix on Linux and a real system folder on
            # Windows. Watch Dogs, AC Shadows and Wildlands all live there.
            if os.name == "nt":
                out = out.replace("{programfilesx86}",
                                  os.environ.get("ProgramFiles(x86)")
                                  or r"C:\Program Files (x86)")
            else:
                out = out.replace("{programfilesx86}",
                                  "{prefix}/drive_c/Program Files (x86)")
        # Native-Linux save locations. Unlike the _USER_DIRS tokens these must
        # NOT be rewritten into the prefix: a native build — OpenGOAL, Dolphin,
        # a Linux recomp — writes to the real XDG dirs, outside any prefix.
        # Recipes were spelling these as a raw "~/.config/...", which resolves
        # on Windows to C:\Users\<you>\.config, a path nothing ever writes to,
        # so those saves could be captured on the Deck and never restored.
        # The Windows halves are the conventional counterparts: XDG_DATA_HOME
        # is per-machine state (LocalAppData), XDG_CONFIG_HOME is roaming
        # user settings (AppData\Roaming).
        if "{xdg_data}" in out:
            out = out.replace("{xdg_data}", _windows_user_dir("{localappdata}")
                              if os.name == "nt" else _xdg("XDG_DATA_HOME",
                                                           ".local/share"))
        if "{xdg_config}" in out:
            out = out.replace("{xdg_config}", _windows_user_dir("{appdata}")
                              if os.name == "nt" else _xdg("XDG_CONFIG_HOME",
                                                           ".config"))
        if "{prefix" in out:
            from . import detect
            pfx = detect.find_prefix(self.recipe, self.steam_root)
            if pfx is None:
                raise StepError("no Proton prefix yet — run the game once via "
                                "Steam first, then re-apply")
            local_appdata = (pfx / "drive_c" / "users" / "steamuser"
                             / "AppData" / "Local")
            out = out.replace("{prefix_localappdata}", str(local_appdata))
            out = out.replace("{prefix}", str(pfx))
        out = out.replace("{game_dir}", str(self.game_dir))
        # LEADING ~ only. A blanket replace also ate a '~' anywhere else in the
        # path — a save named "settings.ini~", or a Windows box whose
        # ProgramFiles(x86) comes back as a short name like C:\PROGRA~2 — and
        # silently produced a path pointing nowhere near the real one. It runs
        # after the token expansions above, so any ~ left here that isn't in
        # column zero belongs to a real filename.
        if out.startswith("~"):
            out = str(Path.home()) + out[1:]
        return Path(out)

    def payload_path(self, rel: str) -> Path:
        """Resolve a payload reference. A local override wins when present;
        otherwise the recipe's own folder is used. A down NAS mount (OSError
        on access) is treated as 'override not present' — never crashes.

        The override lives at <root>/_recipes/<id>/<rel> (tidy home), with the
        legacy flat <root>/<id>/<rel> tried as a fallback so a not-yet-migrated
        NAS still resolves."""
        mount_err = None
        if self.local_payloads_dir is not None:
            from . import store
            bases = [store.recipe_data_root(self.local_payloads_dir)
                     / self.recipe.id,
                     self.local_payloads_dir / self.recipe.id]
            for base in bases:
                try:
                    base_r = base.resolve()
                    cand = (base_r / rel).resolve()
                    if (base_r == cand or base_r in cand.parents) \
                            and cand.exists():
                        return cand
                except OSError as e:
                    mount_err = e  # local-payloads mount unreachable (dead NAS)
        p = (self.recipe.dir / rel).resolve()
        rd = self.recipe.dir.resolve()
        if rd not in p.parents and p != rd:
            raise StepError(f"payload path escapes recipe dir: {rel}")
        try:
            present = p.exists()
        except OSError:
            present = False
        if not present:
            if mount_err is not None:
                raise StepError(
                    f"payload not reachable — the local-payloads mount "
                    f"({self.local_payloads_dir}) is down: {mount_err}")
            raise StepError(f"payload missing: {p}")
        return p


def _step_impl(step: dict):
    cls = _REGISTRY.get(step["type"])
    if cls is None:
        raise StepError(f"no implementation registered for step type '{step['type']}'")
    return cls(step)


def apply_recipe(recipe: Recipe, ctx: Ctx) -> None:
    for i, step in enumerate(recipe.steps, 1):
        impl = _step_impl(step)
        ctx.log(f"  [{i}/{len(recipe.steps)}] {step['type']}")
        try:
            impl.apply(ctx)
        except StepError as e:
            if not step.get("optional"):
                raise
            ctx.log(f"      ! optional step skipped: {e}")


def verify_recipe(recipe: Recipe, ctx: Ctx) -> str:
    statuses = []
    for s in recipe.steps:
        try:
            statuses.append(_step_impl(s).verify(ctx))
        except StepError:
            if not s.get("optional"):
                raise
    # No steps to check → nothing to un-do, count as applied.
    if not statuses:
        return APPLIED
    if all(s == APPLIED for s in statuses):
        return APPLIED
    if all(s == NOT_APPLIED for s in statuses):
        return NOT_APPLIED
    return PARTIAL


def revert_recipe(recipe: Recipe, ctx: Ctx) -> None:
    for i, step in enumerate(reversed(recipe.steps), 1):
        impl = _step_impl(step)
        ctx.log(f"  [{i}/{len(recipe.steps)}] revert {step['type']}")
        impl.revert(ctx)


# Import step modules for their registration side effects.
from .steps import (copy_files, ini_edit, install_runner,  # noqa: E402,F401
                    launch_options, make_executable, pak_edit, pitcrew_compile,
                    proton_version, remove_files, steam_shortcut, swap_exe,
                    symlink, systemd_unit, wine_registry)
