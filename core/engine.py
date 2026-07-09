"""Step runner: apply / verify / revert a recipe's steps in order.

Every step type registers a class with three methods:
  apply(ctx)  -> None      idempotent — safe to run twice
  verify(ctx) -> str       one of APPLIED / NOT_APPLIED / PARTIAL
  revert(ctx) -> None      undo, restoring *.gfm-orig backups where present
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .manifest import Recipe

APPLIED = "applied"
NOT_APPLIED = "not_applied"
PARTIAL = "partial"

BACKUP_SUFFIX = ".gfm-orig"

_REGISTRY: dict[str, type] = {}


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
        ~           — the user's home dir
        Prefix templates need the game to have run once; a StepError is
        raised (usually caught by an optional step) if no prefix exists yet.
        """
        out = template
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
        out = out.replace("~", str(Path.home()))
        return Path(out)

    def payload_path(self, rel: str) -> Path:
        """Resolve a payload reference. A local override at
        <local_payloads_dir>/<recipe_id>/<rel> wins when present; otherwise
        the recipe's own folder is used. A down NAS mount (OSError on access)
        is treated as 'override not present' — never crashes."""
        mount_err = None
        if self.local_payloads_dir is not None:
            try:
                base = (self.local_payloads_dir / self.recipe.id).resolve()
                cand = (base / rel).resolve()
                if (base == cand or base in cand.parents) and cand.exists():
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
from .steps import (copy_files, launch_options, pak_edit,  # noqa: E402,F401
                    proton_version, remove_files, steam_shortcut, swap_exe,
                    symlink, systemd_unit, wine_registry)
