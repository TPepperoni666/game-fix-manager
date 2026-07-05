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
    # steps that need Steam closed can queue work here; the caller batches it
    deferred_vdf_writes: list = field(default_factory=list)

    def resolve_target(self, template: str) -> Path:
        """Expand {game_dir} etc. in a manifest path and resolve it."""
        out = template.replace("{game_dir}", str(self.game_dir))
        out = out.replace("~", str(Path.home()))
        return Path(out)

    def payload_path(self, rel: str) -> Path:
        """A path inside the recipe folder (payload files)."""
        p = (self.recipe.dir / rel).resolve()
        if self.recipe.dir.resolve() not in p.parents and p != self.recipe.dir.resolve():
            raise StepError(f"payload path escapes recipe dir: {rel}")
        if not p.exists():
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
        impl.apply(ctx)


def verify_recipe(recipe: Recipe, ctx: Ctx) -> str:
    statuses = [_step_impl(s).verify(ctx) for s in recipe.steps]
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
from .steps import copy_files, swap_exe  # noqa: E402,F401
