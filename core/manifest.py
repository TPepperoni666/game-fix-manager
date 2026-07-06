"""Recipe manifests: load and validate manifest.json files from the store."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "manifest.json"
KNOWN_STEP_TYPES = {"copy_files", "swap_exe", "launch_options", "systemd_unit",
                    "remove_files", "pak_edit", "wine_registry", "run_script"}


class ManifestError(Exception):
    pass


@dataclass
class Recipe:
    id: str
    name: str
    aliases: list[str]
    steam_appid: int | None
    detect: dict
    steps: list[dict]
    notes: str
    post_apply_message: str
    remote_payloads: list[dict]
    requires_game: bool  # False for tool recipes (no install dir; {game_dir} = home)
    dir: Path  # recipe folder (contains manifest.json and payload/)

    @property
    def payload_dir(self) -> Path:
        return self.dir / "payload"

    @property
    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]


def _require(data: dict, key: str, path: Path):
    if key not in data:
        raise ManifestError(f"{path}: missing required field '{key}'")
    return data[key]


def load_recipe(recipe_dir: Path) -> Recipe:
    path = recipe_dir / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path}: invalid JSON — {e}") from e

    steps = _require(data, "steps", path)
    if not isinstance(steps, list) or not steps:
        raise ManifestError(f"{path}: 'steps' must be a non-empty list")
    for i, step in enumerate(steps):
        stype = step.get("type")
        if stype not in KNOWN_STEP_TYPES:
            raise ManifestError(f"{path}: step {i + 1} has unknown type '{stype}'")

    return Recipe(
        id=_require(data, "id", path),
        name=_require(data, "name", path),
        aliases=data.get("aliases", []),
        steam_appid=data.get("steam_appid"),
        detect=data.get("detect", {}),
        steps=steps,
        notes=data.get("notes", ""),
        post_apply_message=data.get("post_apply_message", ""),
        remote_payloads=data.get("remote_payloads", []),
        requires_game=data.get("requires_game", True),
        dir=recipe_dir,
    )


def load_all(store_root: Path) -> list[Recipe]:
    """Load every recipe under <store_root>/games/. Bad manifests raise."""
    games_dir = store_root / "games"
    recipes = []
    if not games_dir.is_dir():
        return recipes
    for d in sorted(games_dir.iterdir()):
        if d.is_dir() and (d / MANIFEST_NAME).is_file():
            recipes.append(load_recipe(d))
    return recipes
