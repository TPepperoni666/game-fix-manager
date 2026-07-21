"""Custom artwork for a (non-Steam) shortcut lives in
userdata/<id>/config/grid/, named by the shortcut appid:

  <appid>.png / .jpg     horizontal capsule / library header
  <appid>p.png           vertical capsule (library portrait, 600x900)
  <appid>_hero.png       hero banner
  <appid>_logo.png       logo
  <appid>_icon.png/.ico  icon

Because GFM pins the gospel appid, art captured for a game restores onto the
SAME game after a reimage (the filenames already carry the appid). capture()
snapshots those files off to storage; restore() drops them back into every
user's grid folder, ready for the recreated shortcut to pick up.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def _grid_dirs(steam_root: Path | None) -> list[Path]:
    # steam_root is None when Steam wasn't found. Guard here rather than at
    # every call site: capture() runs across every recipe during 🔍 Scan, and
    # `None / "userdata"` would take the whole scan down with a TypeError.
    if steam_root is None:
        return []
    ud = Path(steam_root) / "userdata"
    if not ud.is_dir():
        return []
    return [d / "config" / "grid" for d in sorted(ud.iterdir())
            if d.name.isdigit() and d.name != "0" and (d / "config").is_dir()]


def _art_files(grid: Path, appid: int) -> list[Path]:
    """Grid files that belong to this appid. Exact-matches the base name so a
    longer appid sharing a numeric prefix can't be swept up by mistake."""
    if not grid.is_dir():
        return []
    pfx = str(appid)
    out = []
    for f in grid.iterdir():
        if not f.is_file():
            continue
        b = f.stem
        if b == pfx or b == pfx + "p" or b.startswith(pfx + "_"):
            out.append(f)
    return out


def capture(steam_root: Path, appid: int, dest_dir: Path) -> int:
    """Copy this appid's custom grid art from the first user that has any into
    dest_dir. Returns the number of files captured (0 = no custom art set)."""
    dest_dir = Path(dest_dir)
    for grid in _grid_dirs(steam_root):
        files = _art_files(grid, appid)
        if files:
            dest_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, dest_dir / f.name)
            return len(files)
    return 0


def capture_icon(appid: int, icon_path: str, dest_dir: Path) -> bool:
    """Preserve a non-Steam shortcut's ICON. Unlike the library grid art
    (capsule/hero/logo, which live in the grid folder named by appid), the icon
    is a FILE referenced by PATH in shortcuts.vdf — often the exe's own icon or
    a custom .ico anywhere on disk — so the grid sweep never sees it. We copy
    that file into the artwork folder as <appid>_icon<ext> so it rides along
    with the rest and can be restored. Returns True if an icon file was copied."""
    if not icon_path:
        return False
    src = Path(icon_path)
    try:
        if not src.is_file():
            return False
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = src.suffix.lower() or ".ico"
        shutil.copy2(src, dest_dir / f"{appid}_icon{ext}")
        return True
    except OSError:
        return False


def captured_icon(src_dir: Path, appid: int) -> Path | None:
    """The captured icon file for appid inside src_dir, if any."""
    try:
        for f in Path(src_dir).glob(f"{appid}_icon.*"):
            if f.is_file():
                return f
    except OSError:
        pass
    return None


def restore(steam_root: Path, appid: int, src_dir: Path) -> int:
    """Copy captured art from src_dir into every Steam user's grid folder. The
    files are already named by appid, so they land on the right shortcut.
    Returns files written. appid is accepted for symmetry / future filtering."""
    src_dir = Path(src_dir)
    try:
        files = [f for f in src_dir.iterdir() if f.is_file()] if src_dir.is_dir() else []
    except OSError:
        return 0
    if not files:
        return 0
    written = 0
    for grid in _grid_dirs(steam_root):
        grid.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, grid / f.name)
            written += 1
    return written
