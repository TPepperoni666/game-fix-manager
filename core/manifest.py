"""Recipe manifests: load and validate manifest.json files from the store."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "manifest.json"
# Step types accepted at LOAD time. This must stay in lockstep with the
# engine's registry — a name here with no implementation loads fine and then
# dies at apply time, which is the worst possible place to find out. The
# smoke test asserts the two sets are equal, so drift fails the build rather
# than a user's run. ("run_script" lived here unimplemented until 2026-07-20.)
KNOWN_STEP_TYPES = {"copy_files", "swap_exe", "launch_options", "systemd_unit",
                    "remove_files", "pak_edit", "wine_registry",
                    "proton_version", "symlink", "steam_shortcut",
                    "install_runner", "pitcrew_compile", "ini_edit",
                    "make_executable"}


class ManifestError(Exception):
    pass


def current_platform() -> str:
    """'windows' or 'linux' - what a manifest's "platforms" is matched
    against. Deliberately coarse: SteamOS and any other Linux are the same
    thing as far as a recipe is concerned."""
    return "windows" if os.name == "nt" else "linux"


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
    # Path templates for saves living OUTSIDE the prefix (in the game folder),
    # which prefix backups don't cover. Captured/restored by core/saves.py.
    save_paths: list[str]
    dir: Path  # recipe folder (contains manifest.json and payload/)
    # Which OSes this recipe may run on: ["linux"], ["windows"], or empty for
    # "anywhere". Defaulted and last so existing callers are unaffected.
    # Detection resolves steam_appid through the appmanifest on ANY OS, so a
    # SteamOS-only recipe would otherwise look applicable on Windows, and an
    # Apply there can break a working install of the same game.
    platforms: list[str] = field(default_factory=list)

    @property
    def payload_dir(self) -> Path:
        return self.dir / "payload"

    @property
    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    @property
    def runs_here(self) -> bool:
        """False when this recipe declares platforms and the current one
        is not among them. An empty list means 'anywhere'."""
        return not self.platforms or current_platform() in self.platforms


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

    # Steps are optional — a recipe with no steps is a documentation-only
    # entry (declares the game exists so detection, reconciliation and
    # mirroring know about it) and verifies as APPLIED.
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        raise ManifestError(f"{path}: 'steps' must be a list")
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
        save_paths=data.get("save_paths", []),
        platforms=[str(p).strip().lower()
                   for p in data.get("platforms", []) if str(p).strip()],
        dir=recipe_dir,
    )


def load_all(store_root: Path) -> list[Recipe]:
    """Load every recipe under <store_root>/games/, sorted alphabetically by
    display name. Bad manifests raise."""
    games_dir = store_root / "games"
    recipes = []
    if not games_dir.is_dir():
        return recipes
    for d in sorted(games_dir.iterdir()):
        if d.is_dir() and (d / MANIFEST_NAME).is_file():
            recipes.append(load_recipe(d))
    recipes.sort(key=lambda r: r.name.lower())
    return recipes
