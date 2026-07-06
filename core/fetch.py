"""Remote payloads: files too big for the git repo (GitHub caps at 100MB)
live as release assets and are downloaded into the store on first apply.

Manifest form:
  "remote_payloads": [
    { "path": "payload/mod/data_win64/patch.dat",
      "url": "https://github.com/<user>/<repo>/releases/download/<tag>/<file>",
      "sha256": "...", "size": 273833146 } ]

With "extract_to", the downloaded file is treated as an archive (7z/zip/tar —
anything bsdtar reads; SteamOS and Windows both ship it) and unpacked into
that recipe-relative directory after hash verification:
  { "path": "payload/downloads/TCUServer-1.4.5.0.7z", "url": "...",
    "sha256": "...", "size": 32062282, "extract_to": "payload/patch" }

A file already present with the right size is trusted (hash was verified when
it was downloaded); a fresh download is always hash-checked before install.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from .hashutil import file_hash
from .manifest import Recipe


class FetchError(Exception):
    pass


def _find_tar() -> str | None:
    # bsdtar handles 7z; GNU tar does not — prefer it when both exist
    return shutil.which("bsdtar") or shutil.which("tar")


def _extract(archive: Path, dest: Path, log: Callable[[str], None]) -> None:
    tar = _find_tar()
    if tar is None:
        raise FetchError("no bsdtar/tar found to extract archives")
    log(f"      ⇲ extracting {archive.name} -> {dest}")
    if dest.exists():
        shutil.rmtree(dest)  # stale extraction from a previous version
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([tar, "-xf", str(archive), "-C", str(dest)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise FetchError(f"extraction failed: {(result.stderr or '').strip()}")


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
    """Make sure every remote payload exists locally; download what's missing,
    extract archives that declare extract_to."""
    for item in recipe.remote_payloads:
        target = recipe.dir / item["path"]
        expected_size = item.get("size")
        fresh = False
        if not (target.is_file() and
                (expected_size is None or target.stat().st_size == expected_size)):
            log(f"    ⬇ fetching {target.name} "
                f"({(expected_size or 0) // (1 << 20)} MB) — first run only")
            _download(item["url"], target, expected_size, log)
            actual = file_hash(target)
            if item.get("sha256") and actual != item["sha256"]:
                target.unlink()
                raise FetchError(
                    f"{target.name}: hash mismatch after download "
                    f"(got {actual[:12]}…, expected {item['sha256'][:12]}…) — "
                    "source file may have changed; recipe needs updating")
            log(f"      ✓ verified {target.name}")
            fresh = True
        if item.get("extract_to"):
            dest = recipe.dir / item["extract_to"]
            if fresh or not dest.is_dir():
                _extract(target, dest, log)
