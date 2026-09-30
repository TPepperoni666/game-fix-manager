"""File hashing/comparison helpers — dependency-free so any module
(steps, fetch, tests) can import them without cycles.

same_file() is the hot path: every recipe's verify() calls it, and `gfm.py
list` verifies all of them. That was fine until a 12 GB recipe arrived (F1
Manager's pak mods), at which point list went from ~10s to 175s — it hashed
12 GB off the NAS and 12 GB off local disk on every invocation, to answer a
question whose inputs had not changed.

So hashes are cached by (path, size, mtime_ns). The cache is OPT-IN, off by
default, because fetch.py hashes a freshly downloaded payload to check it
against a declared sha256 — an integrity gate must never be answered from a
cache. Only same_file() opts in.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import tempfile
from pathlib import Path

# Only files at least this big are worth caching. Below it the stat plus dict
# lookup costs more than the hash — and, more importantly, it confines the
# cache's one weakness (a file rewritten at the same size within a single
# mtime tick, which coarse CIFS timestamps make conceivable) to large files,
# where an in-place same-second rewrite is least plausible. Small files are
# always hashed for real.
CACHE_MIN_SIZE = 8 << 20          # 8 MB
# A machine tracks a few hundred payload files. Five figures means something
# has gone wrong (or a path scheme changed); drop the lot and rebuild rather
# than grow without bound.
CACHE_MAX_ENTRIES = 20_000

_cache: dict[str, str] | None = None
_dirty = False


def cache_path() -> Path:
    """Local cache location — deliberately NOT on the NAS, which is the slow
    thing we are avoiding reading."""
    base = os.environ.get("XDG_CACHE_HOME", "")
    if not (base and os.path.isabs(base)):
        base = str(Path.home() / ("AppData/Local" if os.name == "nt"
                                  else ".cache"))
    return Path(base) / "gfm" / "hashes.json"


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(cache_path().read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            # A missing or corrupt cache is a cold start, never an error.
            _cache = {}
    return _cache


def save_cache() -> None:
    """Persist the cache. Best effort: a cache we cannot write is a speed
    loss, never a correctness one."""
    global _dirty
    if not _dirty or _cache is None:
        return
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename. A half-written file would fail to parse on the
        # next run and throw the whole cache away.
        with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False,
                                         encoding="utf-8") as fh:
            json.dump(_cache, fh)
            tmp = Path(fh.name)
        tmp.replace(p)
        _dirty = False
    except OSError:
        pass


atexit.register(save_cache)


def _key(path: Path, st: os.stat_result) -> str:
    return f"{path}|{st.st_size}|{st.st_mtime_ns}"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_hash(path: Path, use_cache: bool = False) -> str:
    """sha256 of a file.

    use_cache is off by default on purpose — see the module docstring.
    """
    global _dirty
    st = None
    if use_cache:
        try:
            st = path.stat()
        except OSError:
            st = None
    cacheable = st is not None and st.st_size >= CACHE_MIN_SIZE
    if cacheable:
        hit = _load().get(_key(path, st))
        if hit:
            return hit

    digest = _digest(path)

    if cacheable:
        # Re-stat: if the file changed WHILE we were reading it, the digest
        # does not belong to the state the key describes, and caching it would
        # pin a wrong answer until the file changed again.
        try:
            st2 = path.stat()
        except OSError:
            st2 = None
        if st2 is not None and (st2.st_size, st2.st_mtime_ns) == (
                st.st_size, st.st_mtime_ns):
            cache = _load()
            if len(cache) >= CACHE_MAX_ENTRIES:
                cache.clear()
            cache[_key(path, st)] = digest
            _dirty = True
    return digest


def same_file(a: Path, b: Path) -> bool:
    """Same size and same content. Cached, because this runs for every file of
    every recipe on every `list`.

    An unreadable file answers False rather than raising: a NAS blip during a
    verify should read as 'not applied', not take the command down."""
    try:
        if not (a.is_file() and b.is_file()):
            return False
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    try:
        return file_hash(a, use_cache=True) == file_hash(b, use_cache=True)
    except OSError:
        return False
