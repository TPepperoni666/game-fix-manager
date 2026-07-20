"""Prefix BACKUP — the other half of prefiximport, and the last thing keeping
the old Linux Prefix Manager alive.

Walks compatdata/, works out what each prefix belongs to, and copies the ones
you pick to the SD in EXACTLY the layout prefiximport already reads:

    <SD>/steamos_restore/prefix_backups/<safe_name>/<appid>/

so backup and restore fit together with no translation. (Tony syncs that folder
to the NAS with Syncthing separately.)

Selection model — the old tool's behaviour, minus the clutter:
  * Non-Steam shortcuts and Steam games WITHOUT cloud are the default
    candidates. Rule of thumb: games old enough to need our fixes are old
    enough to predate Steam Cloud, so this is nearly always the right set.
  * Steam-Cloud games are HIDDEN by default (there can be ~80 of them and
    they'd bury the dozen that matter) behind a "show cloud games" toggle.
  * Anything you explicitly opt in is remembered and thereafter treated as a
    normal candidate — so a deliberate exception like Project CARS 3 (whose
    cloud saves aren't portable between the Windows and Linux builds) shows up
    in the main list forever after. Your choices always beat the heuristic.

Cloud detection is a heuristic — userdata/<uid>/<appid>/remote/ having content
means Steam is actually syncing that game. Parsing binary appinfo.vdf would be
"truer" but this is what's observably happening, and the manual override is the
safety valve either way.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import deploy, detect, prefixes, shortcutsvdf

RESTORE_DIR, BACKUPS_DIR = "steamos_restore", "prefix_backups"

# Junk that bloats a prefix and is regenerated on demand — skipped so backups
# stay lean. (Kept deliberately short: a prefix backup should otherwise be
# faithful, since anything missing breaks the game silently.)
SKIP_RELS = (
    "pfx/drive_c/users/steamuser/Temp",
    "pfx/drive_c/windows/Temp",
)


# Valve's own tools get compatdata dirs just like games do, and they were
# cluttering the picker — a Proton runner is not something you back up, it's
# reinstalled by Steam on demand. Filtered by appid AND by name, because the
# appid list goes stale every time Valve ships a new Proton.
TOOL_APPIDS = {
    "228980",                      # Steamworks Common Redistributables
    "1070560", "1391110", "1628350",   # Steam Linux Runtime scout/soldier/sniper
    "1493710",                     # Proton Experimental
    "858280", "930400", "961940", "1054830", "1113280",   # Proton 3.7 – 4.11
    "1245040", "1420170", "1580130", "1887720",           # Proton 5.0 – 7.0
    "2180100", "2230260", "2805730", "3658110",           # Proton 8.0 – hotfix
}

_TOOL_NAME_RE = re.compile(
    r"^(proton\b|steam linux runtime|steamworks common|steam-?play)", re.I)


def is_tool(appid: str, name: str) -> bool:
    """Is this prefix a Valve runtime/compat tool rather than a game?"""
    return appid in TOOL_APPIDS or bool(_TOOL_NAME_RE.match(name.strip()))


@dataclass
class PrefixInfo:
    appid: str
    name: str                 # friendly, for the folder name + UI
    path: Path                # compatdata/<appid>
    is_steam: bool
    has_cloud: bool
    size: int = 0
    backed_up: bool = False


def safe_name(name: str) -> str:
    """The old tool's sanitiser: sed 's/[^a-zA-Z0-9._-]/_/g'. Matching it means
    existing backup folders keep working."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name) or "unknown"


def backup_root(sd_root: Path) -> Path:
    return sd_root / RESTORE_DIR / BACKUPS_DIR


def _steam_game_name(appid: str, steam_root: Path) -> str | None:
    """Name from appmanifest_<appid>.acf, if this is a Steam-owned game."""
    for lib in detect.library_folders(steam_root):
        acf = lib / "steamapps" / f"appmanifest_{appid}.acf"
        try:
            if not acf.is_file():
                continue
            m = re.search(r'"name"\s+"([^"]+)"',
                          acf.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(1)
        except OSError:
            continue
    return None


def _shortcut_names(steam_root: Path) -> dict[str, str]:
    """{appid: AppName} for every non-Steam shortcut."""
    out: dict[str, str] = {}
    try:
        files = shortcutsvdf._shortcut_files(steam_root)
    except Exception:
        return out
    for f in files:
        try:
            root = shortcutsvdf.loads(f.read_bytes())
        except Exception:
            continue
        for entry in shortcutsvdf._entries(root):
            _k, _t, appid = shortcutsvdf._get_ci(entry, "appid")
            _k2, _t2, appname = shortcutsvdf._get_ci(entry, "AppName")
            if isinstance(appid, int) and isinstance(appname, str):
                out[str(appid & 0xFFFFFFFF)] = appname
    return out


def has_cloud(steam_root: Path, appid: str) -> bool:
    """Is Steam actually cloud-syncing this game? userdata/<uid>/<appid>/remote/
    with content is the observable signal."""
    ud = steam_root / "userdata"
    try:
        users = [d for d in ud.iterdir() if d.name.isdigit()]
    except OSError:
        return False
    for u in users:
        remote = u / appid / "remote"
        try:
            if remote.is_dir() and any(remote.iterdir()):
                return True
        except OSError:
            continue
    return False


def enumerate_prefixes(steam_root: Path, sd_root: Path | None = None,
                       measure: bool = True,
                       include_tools: bool = False) -> list[PrefixInfo]:
    """Every prefix in compatdata, identified and classified.

    measure=False skips the size walk. That walk is the whole cost of this
    function — it stats every file in every prefix, which measured 67s of
    dead silence on Tony's Deck before the picker even appeared, and he
    abandoned the run. The picker doesn't need sizes: plan() reports exact
    incremental bytes for the CHOSEN prefixes a moment later, which is the
    number that actually matters. Same lesson as the deploy menu.

    include_tools=False drops Valve runtimes and Proton builds, which are
    reinstalled by Steam on demand and have no business in a backup list."""
    shortcuts = _shortcut_names(steam_root)
    gbm = prefixes.load_gbm_csv()
    out: list[PrefixInfo] = []
    existing = backup_root(sd_root) if sd_root else None
    for pfx in prefixes.compatdata_prefixes(steam_root):
        appid = pfx.name
        steam_name = _steam_game_name(appid, steam_root)
        is_steam = steam_name is not None
        name = (steam_name or shortcuts.get(appid) or gbm.get(appid)
                or f"appid {appid}")
        if not include_tools and is_tool(appid, name):
            continue
        info = PrefixInfo(
            appid=appid, name=name, path=pfx, is_steam=is_steam,
            has_cloud=has_cloud(steam_root, appid) if is_steam else False)
        if measure:
            info.size = deploy.tree_stats(pfx)[1]
        if existing is not None:
            try:
                info.backed_up = (existing / safe_name(name) / appid).is_dir()
            except OSError:
                info.backed_up = False
        out.append(info)
    out.sort(key=lambda p: p.name.lower())
    return out


def candidates(all_prefixes: list[PrefixInfo], opted_in: set[str],
               show_cloud: bool = False) -> list[PrefixInfo]:
    """The list to show. Non-Steam + no-cloud Steam games are always in;
    cloud games only when explicitly opted in, or when show_cloud reveals them
    for picking."""
    out = []
    for p in all_prefixes:
        if not p.has_cloud or p.appid in opted_in or show_cloud:
            out.append(p)
    return out


def inventory(recipes, registry: dict | None = None,
              roots: list[Path] | None = None) -> list[dict]:
    """Inventory the prefix backups sitting on the SD and work out what each
    one IS — so old backups dragged in from the previous tool are identified,
    not just found.

    Matching, best signal first:
      'registry' — appid is a pinned gospel appid (store/prefix_registry.json)
      'recipe'   — safe_name matches a recipe's name/aliases/install dir
      'unknown'  — a backup we can't tie to a game (kept, just flagged)

    Returns plain dicts so this can be written straight into sd_map.json for
    Tony (and Claude) to see what's on the card.
    """
    from . import prefiximport
    registry = registry or {}
    reg_by_appid = {str(k): v for k, v in registry.items()}
    norm_recipes = []
    for r in recipes:
        names = {prefixes._norm(n) for n in r.all_names}
        names |= {prefixes._norm(n)
                  for n in r.detect.get("install_dir_names", [])}
        norm_recipes.append((r, {n for n in names if n}))

    out: list[dict] = []
    for b in prefiximport.list_backups(roots):
        entry = {
            "appid": b.appid,
            "safe_name": b.safe_name,
            "path": str(b.path),
            "has_pfx": b.has_pfx,
            "size": tree_size(b.path),
            "recipe_id": None,
            "name": b.safe_name.replace("_", " "),
            "matched_by": "unknown",
        }
        reg = reg_by_appid.get(b.appid)
        if reg:
            entry.update(recipe_id=reg.get("recipe_id"),
                         name=reg.get("name") or entry["name"],
                         matched_by="registry")
        else:
            want = prefixes._norm(b.safe_name)
            for r, names in norm_recipes:
                if want in names:
                    entry.update(recipe_id=r.id, name=r.name,
                                 matched_by="recipe")
                    break
        out.append(entry)
    out.sort(key=lambda e: e["name"].lower())
    return out


def tree_size(root: Path) -> int:
    """Bytes under a path, symlinks never followed."""
    total = 0
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for n in files:
            p = os.path.join(dirpath, n)
            if os.path.islink(p):
                continue
            try:
                total += os.path.getsize(p)
            except OSError:
                continue
    return total


def _skip(rel_posix: str) -> bool:
    return any(rel_posix == s or rel_posix.startswith(s + "/")
               for s in SKIP_RELS)


def plan(info: PrefixInfo, dest_root: Path) -> tuple[list, int, int]:
    """(files_to_copy, bytes_to_copy, already_ok) for one prefix — incremental,
    so a repeat backup only moves what changed."""
    dst_root = dest_root / safe_name(info.name) / info.appid
    todo, size, skipped = [], 0, 0
    for dirpath, dirnames, names in os.walk(info.path):
        rel_dir = Path(dirpath).relative_to(info.path)
        rel_posix = rel_dir.as_posix()
        if rel_posix != "." and _skip(rel_posix):
            dirnames[:] = []
            continue
        for n in sorted(names):
            src = Path(dirpath) / n
            if src.is_symlink():
                continue          # recreated verbatim, never followed
            dst = dst_root / rel_dir / n
            if deploy._same(src, dst):
                skipped += 1
                continue
            try:
                size += src.stat().st_size
            except OSError:
                continue
            todo.append((src, dst))
    return todo, size, skipped


def backup(info: PrefixInfo, dest_root: Path,
           progress: Callable[[int, int, str], None] | None = None,
           log: Callable[[str], None] = print) -> dict:
    """Copy one prefix to <dest_root>/<safe_name>/<appid>/, incrementally.

    Symlinks are RECREATED, never followed — a prefix contains
    dosdevices/z: -> / and following it would try to copy the whole
    filesystem into the backup."""
    dst_root = dest_root / safe_name(info.name) / info.appid
    todo, total, skipped = plan(info, dest_root)
    done = copied = links = 0

    def _relink(src: Path, dst: Path) -> None:
        """Recreate a symlink verbatim. NEVER follow it — a prefix has
        dosdevices/z: -> / and following that copies the whole filesystem."""
        nonlocal links
        try:
            if os.path.lexists(dst):
                return
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(src), dst)
            links += 1
        except OSError:
            pass

    for dirpath, dirnames, names in os.walk(info.path):
        rel = Path(dirpath).relative_to(info.path)
        if rel.as_posix() != "." and _skip(rel.as_posix()):
            dirnames[:] = []
            continue
        (dst_root / rel).mkdir(parents=True, exist_ok=True)
        for d in list(dirnames):                 # symlinked dirs (z:, etc.)
            p = Path(dirpath) / d
            if p.is_symlink():
                dirnames.remove(d)               # don't descend into it
                _relink(p, dst_root / rel / d)
        for n in names:                          # symlinked files
            p = Path(dirpath) / n
            if p.is_symlink():
                _relink(p, dst_root / rel / n)

    for src, dst in todo:
        def _on(n: int) -> None:
            nonlocal done
            done += n
            if progress:
                progress(done, total, str(src.relative_to(info.path)))
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            deploy._copy_chunked(src, dst, _on)
            copied += 1
        except OSError as e:
            log(f"      ! {src.name}: {e}")
    if progress:
        progress(done, total, "")
    return {"copied": copied, "skipped": skipped, "bytes": done,
            "links": links, "dest": dst_root}
