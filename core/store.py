"""Locate the fix store and the per-machine config.

Store resolution order (first hit wins):
  1. --store CLI argument
  2. GFM_STORE environment variable
  3. store_root remembered in machine config
  4. the repo's own store/ folder (git-clone bootstrap: clone repo -> it just works)
  5. SD card scan: /run/media/*/steamos_restore/game_fixes

Machine config (remembered game paths, chosen store) is deliberately separate
from the store itself — it describes THIS device, the store is portable.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("GFM_CONFIG_DIR", str(Path.home() / ".config" / "gfm")))
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def sd_card_roots() -> list[Path]:
    """Mounted removable media roots (candidates for a mirror destination)."""
    roots = []
    for media in (Path("/run/media") / os.environ.get("USER", "deck"),
                  Path("/run/media/deck")):
        if not media.is_dir():
            continue
        for card in media.iterdir():
            if card.is_dir() and card not in roots:
                roots.append(card)
    return roots


_MIRROR_SKIP_SUFFIXES = (".part", ".gfm-tmp")


def mirror_store(store_root: Path, dest: Path,
                 log=lambda _m: None) -> tuple[int, int]:
    """Incrementally copy the whole store (payloads included) to dest.
    Nothing is deleted at the destination. Returns (copied, up_to_date)."""
    copied = fresh = 0
    for src in sorted(store_root.rglob("*")):
        if not src.is_file() or src.name.endswith(_MIRROR_SKIP_SUFFIXES):
            continue
        rel = src.relative_to(store_root)
        d = dest / rel
        s_stat = src.stat()
        if (d.is_file() and d.stat().st_size == s_stat.st_size
                and int(d.stat().st_mtime) >= int(s_stat.st_mtime)):
            fresh += 1
            continue
        if s_stat.st_size > (8 << 20):
            log(f"    ⇉ {rel} ({s_stat.st_size // (1 << 20)} MB)")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
        copied += 1
    return copied, fresh


def _sd_card_stores() -> list[Path]:
    found = []
    for media in (Path("/run/media") / os.environ.get("USER", "deck"), Path("/run/media/deck")):
        if not media.is_dir():
            continue
        for card in media.iterdir():
            candidate = card / "steamos_restore" / "game_fixes"
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found


# Per-recipe data (payload overrides + captured artwork + saves) lives one
# folder deep under the local-payloads root, so the root holds only the four
# tidy system dirs (_recipes, _games, _runners, _state) instead of a folder
# per game littering it. Reads fall back to the legacy flat location so a
# not-yet-migrated NAS keeps working; new writes always use _recipes/.
RECIPE_DATA_DIR = "_recipes"


def recipe_data_root(local_payloads: Path) -> Path:
    return local_payloads / RECIPE_DATA_DIR


def recipe_data_dir(local_payloads: Path, recipe_id: str, *sub: str,
                    for_write: bool = False) -> Path:
    """A recipe's data folder (optionally a subpath like 'artwork'/'saves').

    for_write=True always returns the new _recipes/<id>/… home. For reads it
    prefers the new home but falls back to the legacy flat <root>/<id>/… when
    the new one doesn't exist yet — so migration can happen in any order."""
    new = recipe_data_root(local_payloads).joinpath(recipe_id, *sub)
    if for_write:
        return new
    legacy = local_payloads.joinpath(recipe_id, *sub)
    try:
        if not new.exists() and legacy.exists():
            return legacy
    except OSError:
        pass
    return new


def artwork_dir(local_payloads: Path, appid) -> Path:
    """Where a NON-recipe managed game's captured artwork lives, keyed by its
    (stable) appid: <local_payloads>/_state/artwork/<appid>/. Recipe games keep
    their art under _recipes/<id>/artwork/; this is the home for adopted and
    generic-deploy games so EVERY managed game's art is captured and
    restorable, not just ones with a hand-written recipe. Art is
    device-agnostic (grid files are the same everywhere), so no host key."""
    return local_payloads / "_state" / "artwork" / str(appid)


def resolve_local_payloads(cli_arg: str | None, cfg: dict) -> Path | None:
    """Where local-only override payloads live (NAS mount, SD folder, …).
    Order: --local-payloads → GFM_LOCAL_PAYLOADS env → config → SD default.
    Returns None if nothing is configured/present — overrides just don't fire."""
    for cand in (cli_arg, os.environ.get("GFM_LOCAL_PAYLOADS"),
                 cfg.get("local_payloads_dir")):
        if cand and Path(cand).is_dir():
            return Path(cand)
    for sd in sd_card_roots():
        cand = sd / "steamos_restore" / "game_fixes" / "local_payloads"
        if cand.is_dir():
            return cand
    return None


def resolve_store(cli_arg: str | None, cfg: dict) -> Path | None:
    for candidate in (cli_arg, os.environ.get("GFM_STORE"), cfg.get("store_root")):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    repo_store = APP_DIR / "store"
    if (repo_store / "games").is_dir():
        return repo_store
    cards = _sd_card_stores()
    return cards[0] if cards else None
