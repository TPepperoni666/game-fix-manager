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

import os
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
    # Unpack to a sibling and swap it in only on success. dest used to be
    # rmtree'd and recreated BEFORE bsdtar ran, with cleanup only on a
    # non-zero exit - so anything that was not a tar error (Ctrl-C through a
    # multi-minute 7z, a SIGKILL, the card pulled, the Deck losing power)
    # left dest present and half-populated. The caller then skipped
    # re-extraction because dest.is_dir() was True and the archive was still
    # at its declared size, so the partial tree became the payload
    # permanently. The sha256 gate never covers it: that only checks a
    # freshly downloaded archive, never the extracted tree the steps consume.
    staging = dest.with_name(dest.name + ".gfm-extracting")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([tar, "-xf", str(archive), "-C", str(staging)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise FetchError(
                f"extraction failed: {(result.stderr or '').strip()}")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(dest)
    except BaseException:
        # BaseException, not Exception: KeyboardInterrupt and SystemExit are
        # precisely the interruptions that left the half-extracted tree there.
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _extracted_ok(dest: Path) -> bool:
    """Is there a real extraction here, or only a directory?

    dest.is_dir() was the whole check, so an empty or abandoned directory
    counted as done forever."""
    try:
        if not dest.is_dir():
            return False
        for _dirpath, _dirnames, names in os.walk(dest):
            if names:
                return True
        return False
    except OSError:
        return False


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
            # Not just is_dir(): an empty directory is not an extraction.
            if fresh or not _extracted_ok(dest):
                _extract(target, dest, log)
