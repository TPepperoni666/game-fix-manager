"""File hashing/comparison helpers — dependency-free so any module
(steps, fetch, tests) can import them without cycles."""
from __future__ import annotations

import hashlib
from pathlib import Path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_file(a: Path, b: Path) -> bool:
    if not (a.is_file() and b.is_file()):
        return False
    if a.stat().st_size != b.stat().st_size:
        return False
    return file_hash(a) == file_hash(b)
