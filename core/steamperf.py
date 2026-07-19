"""Per-game Steam Deck display/perf settings — capture and restore.

The Deck's per-game framerate limit, allow-tearing and disable-framelimit
toggles live in localconfig.vdf under a `Gamescope` section, keyed by appid:

    "Gamescope"
    {
        "AppTargetFrameRate" { "<appid>" "60"  "<appid>" "144" … }
        "AllowTearing"       { "<appid>" "0" … }
        "DisableFrameLimit"  { "<appid>" "0" … }
    }

(TDP / GPU clock / scaling filter are NOT here — those are global or set by a
Decky plugin, and are APU-specific anyway, so they're out of scope.)

`gfm capture` already snapshots the whole localconfig.vdf to _state/. Restore
is SURGICAL: it loads the fresh post-reimage localconfig, merges ONLY these
per-appid Gamescope entries back in from the snapshot, and writes it — leaving
everything else Steam regenerated (friends, cloud, library state) untouched.
Because gospel appids are pinned, a restored setting reattaches to the right
game.

Steam rewrites localconfig.vdf on exit, so restore must run with Steam CLOSED
(same rule as launch-option / compat-tool writes).
"""
from __future__ import annotations

from pathlib import Path

from . import steamvdf

# The per-appid maps we carry across. Add-only: unknown keys in a snapshot are
# ignored, so a newer Steam adding a fourth map won't break an old restore.
GAMESCOPE_KEYS = ("AppTargetFrameRate", "AllowTearing", "DisableFrameLimit")


def _store(tree: dict) -> dict:
    """The inner UserLocalConfigStore map (or the tree itself if unwrapped)."""
    return steamvdf._child_ci(tree, "UserLocalConfigStore") or tree


def extract(tree: dict) -> dict:
    """{key: {appid: value}} of the per-game Gamescope settings in a parsed
    localconfig tree. Empty when there's no Gamescope section."""
    gs = steamvdf._child_ci(_store(tree), "Gamescope")
    out: dict[str, dict] = {}
    if not isinstance(gs, dict):
        return out
    for key in GAMESCOPE_KEYS:
        node = steamvdf._child_ci(gs, key)
        if isinstance(node, dict):
            vals = {k: v for k, v in node.items() if isinstance(v, str)}
            if vals:
                out[key] = vals
    return out


def restore_into(target_tree: dict, saved: dict,
                 only_appids: set[str] | None = None) -> int:
    """Merge saved per-appid Gamescope settings into a parsed localconfig tree
    (in place). If only_appids is given, restore just those (as strings);
    otherwise restore everything in the snapshot. Existing entries are
    overwritten. Returns the number of appid values written."""
    gs = steamvdf._child_ci(_store(target_tree), "Gamescope", create=True)
    n = 0
    for key, appmap in saved.items():
        node = steamvdf._child_ci(gs, key, create=True)
        for appid, val in appmap.items():
            if only_appids is not None and str(appid) not in only_appids:
                continue
            node[str(appid)] = str(val)
            n += 1
    return n


def snapshot_path(state_dir: Path, uid: str) -> Path:
    return state_dir / f"localconfig-{uid}.vdf"


def restore_file(snapshot: Path, live: Path,
                 only_appids: set[str] | None = None) -> int:
    """Merge a snapshot's Gamescope settings into a live localconfig.vdf on
    disk. Returns values written (0 if the snapshot has none). Caller must have
    Steam CLOSED. The live file is loaded, merged and rewritten — its other
    contents are preserved by the lossless VDF round-trip."""
    saved = extract(steamvdf.vdf_loads(snapshot.read_text(
        encoding="utf-8", errors="surrogateescape")))
    if not saved:
        return 0
    tree = steamvdf.vdf_loads(live.read_text(
        encoding="utf-8", errors="surrogateescape"))
    n = restore_into(tree, saved, only_appids)
    if n:
        live.write_text(steamvdf.vdf_dumps(tree),
                        encoding="utf-8", errors="surrogateescape")
    return n
