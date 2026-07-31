"""Appid pins the TOOL discovers, kept out of the git-tracked store.

store/prefix_registry.json is a CURATED file: hand-written gospel appids that
ship with the recipes. Adoption — noticing a shortcut you added to Steam by
hand and recording its appid — used to append to that same file. Two things
went wrong with that:

  1. It is git-tracked, so the moment the tool wrote to it, the next
     `git pull --ff-only` refused to overwrite it and every subsequent update
     failed. Tony's Deck adopted a game one morning and sat six commits
     behind for the rest of the day without anything saying why.

  2. Worse, and quieter: those pins lived in a git checkout ON THE DEVICE.
     A reimage wipes it. The whole reason a gospel appid exists is so a
     restored prefix lines up with its shortcut AFTER a wipe — so the half of
     the registry the tool generated was exactly the half that couldn't
     survive the event it was for.

Adopted pins therefore live beside the other reimage-surviving state, at
<local_payloads>/_state/adopted_appids.json — on the NAS, where captured
artwork and shortcut bodies already are. No NAS configured (a Windows box,
say) falls back to the config dir, which at least isn't version-controlled.

Curated always wins over adopted on the same appid: the file in git is the
one a human vouched for.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import store

FILENAME = "adopted_appids.json"


def path(local_payloads: Path | None) -> Path:
    """Where adopted pins live. NAS when there is one, config dir otherwise —
    never inside the checkout, which is the entire point."""
    if local_payloads is not None:
        return local_payloads / "_state" / FILENAME
    return store.CONFIG_DIR / FILENAME


def load(local_payloads: Path | None) -> list[dict]:
    """Adopted entries, or [] — a missing file, an unreachable NAS or corrupt
    JSON all mean 'nothing adopted', never a crash."""
    try:
        p = path(local_payloads)
        if not store.exists_safe(p):
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries", [])
    return entries if isinstance(entries, list) else []


def append(local_payloads: Path | None, new: list[dict]) -> Path | None:
    """Add entries, skipping appids already recorded. Returns where it wrote,
    or None if it couldn't."""
    if not new:
        return None
    existing = load(local_payloads)
    have = {str(e.get("appid")) for e in existing}
    fresh = [e for e in new if str(e.get("appid")) not in have]
    if not fresh:
        return None
    p = path(local_payloads)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "_about": "Appid pins GFM discovered by adopting hand-added Steam "
                      "shortcuts. Kept OUT of the git-tracked store so updates "
                      "never conflict, and on the NAS so they survive a "
                      "reimage. store/prefix_registry.json stays curated and "
                      "wins on any appid present in both.",
            "version": 1,
            "entries": existing + fresh,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return None
    return p


def curated_entries(store_root: Path | None) -> list[dict]:
    if store_root is None:
        return []
    try:
        data = json.loads(
            (store_root / "prefix_registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("entries", [])


def merged_entries(store_root: Path | None,
                   local_payloads: Path | None) -> list[dict]:
    """Curated first, then adopted pins for appids the curated file doesn't
    already cover. Callers can treat this as the whole registry."""
    curated = curated_entries(store_root)
    seen = {str(e.get("appid")) for e in curated if e.get("appid") is not None}
    extra = [e for e in load(local_payloads)
             if str(e.get("appid")) not in seen]
    return curated + extra
