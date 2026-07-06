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


def resolve_store(cli_arg: str | None, cfg: dict) -> Path | None:
    for candidate in (cli_arg, os.environ.get("GFM_STORE"), cfg.get("store_root")):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    repo_store = APP_DIR / "store"
    if (repo_store / "games").is_dir():
        return repo_store
    cards = _sd_card_stores()
    return cards[0] if cards else None
