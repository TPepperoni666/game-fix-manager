"""Remote payloads: files too big for the git repo (GitHub caps at 100MB)
live as release assets and are downloaded into the store on first apply.

Manifest form:
  "remote_payloads": [
    { "path": "payload/mod/data_win64/patch.dat",
      "url": "https://github.com/<user>/<repo>/releases/download/<tag>/<file>",
      "sha256": "...", "size": 273833146 } ]

A file already present with the right size is trusted (hash was verified when
it was downloaded); a fresh download is always hash-checked before install.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Callable

from .manifest import Recipe
from .steps.copy_files import file_hash


class FetchError(Exception):
    pass


def _download(url: str, dest: Path, size: int | None, log: Callable[[str], None]) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "game-fix-manager"})
    try:
        with urllib.request.urlopen(req) as resp, part.open("wb") as out:
            total = size or int(resp.headers.get("Content-Length") or 0)
            done, next_mark = 0, 10
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and done * 100 // total >= next_mark:
                    log(f"      … {done * 100 // total}% ({done // (1 << 20)} MB)")
                    next_mark += 10
    except OSError as e:
        part.unlink(missing_ok=True)
        raise FetchError(f"download failed: {url} — {e}") from e
    part.replace(dest)


def ensure_remote_payloads(recipe: Recipe, log: Callable[[str], None]) -> None:
    """Make sure every remote payload exists locally; download what's missing."""
    for item in recipe.remote_payloads:
        target = recipe.dir / item["path"]
        expected_size = item.get("size")
        if target.is_file() and (expected_size is None or target.stat().st_size == expected_size):
            continue
        log(f"    ⬇ fetching {target.name} "
            f"({(expected_size or 0) // (1 << 20)} MB) — first run only")
        _download(item["url"], target, expected_size, log)
        actual = file_hash(target)
        if item.get("sha256") and actual != item["sha256"]:
            target.unlink()
            raise FetchError(
                f"{target.name}: hash mismatch after download "
                f"(got {actual[:12]}…, expected {item['sha256'][:12]}…) — "
                "release asset may be corrupt or outdated")
        log(f"      ✓ verified {target.name}")
