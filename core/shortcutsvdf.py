"""Non-Steam shortcuts: read/edit userdata/*/config/shortcuts.vdf.

shortcuts.vdf is BINARY KeyValues (unlike text localconfig.vdf):
  0x00 <key>\0 -> nested map, terminated by 0x08
  0x01 <key>\0 <string>\0
  0x02 <key>\0 <int32 LE>
Values keep their type tag through parse/serialize so a load->dump round-trip
is byte-identical. Key casing varies per Steam era ("AppName"/"appname") —
all lookups are case-insensitive.

Used two ways:
  * detection — the user adds the game to Steam themselves, and the shortcut's
    AppName/StartDir tells us where the game lives (no path prompt)
  * launch options — written into the matching shortcut entry (Steam must be
    closed, same batching as localconfig writes)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

TYPE_MAP, TYPE_STR, TYPE_INT, END = 0x00, 0x01, 0x02, 0x08


class ShortcutsError(Exception):
    pass


# --- binary KV parse / serialize (typed values: (type, value)) --------------

def _read_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", errors="surrogateescape"), end + 1


def _parse_map(buf: bytes, pos: int) -> tuple[dict, int]:
    out: dict = {}
    while True:
        if pos >= len(buf):
            raise ShortcutsError("truncated shortcuts.vdf")
        t = buf[pos]
        pos += 1
        if t == END:
            return out, pos
        key, pos = _read_cstr(buf, pos)
        if t == TYPE_MAP:
            val, pos = _parse_map(buf, pos)
        elif t == TYPE_STR:
            val, pos = _read_cstr(buf, pos)
        elif t == TYPE_INT:
            val = int.from_bytes(buf[pos:pos + 4], "little")
            pos += 4
        else:
            raise ShortcutsError(f"unknown type byte 0x{t:02X} at offset {pos - 1}")
        out[key] = (t, val)
    # unreachable


def loads(buf: bytes) -> dict:
    # The top level is a map; well-formed files carry their own final 0x08.
    # A sentinel covers files that don't, and both outcomes are accepted.
    root, pos = _parse_map(buf + b"\x08", 0)
    if pos not in (len(buf), len(buf) + 1):
        raise ShortcutsError("trailing data in shortcuts.vdf")
    return root


def dumps(root: dict) -> bytes:
    def emit(node: dict) -> bytes:
        out = bytearray()
        for key, (t, val) in node.items():
            out.append(t)
            out += key.encode("utf-8", errors="surrogateescape") + b"\x00"
            if t == TYPE_MAP:
                out += emit(val)
            elif t == TYPE_STR:
                out += str(val).encode("utf-8", errors="surrogateescape") + b"\x00"
            elif t == TYPE_INT:
                out += int(val).to_bytes(4, "little")
        out.append(END)
        return bytes(out)

    return emit(root)  # root's END byte is the file's standard terminator


# --- shortcut helpers --------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _get_ci(node: dict, name: str):
    for k, (t, v) in node.items():
        if k.lower() == name.lower():
            return k, t, v
    return None, None, None


def _shortcut_files(steam_root: Path) -> list[Path]:
    userdata = steam_root / "userdata"
    if not userdata.is_dir():
        return []
    return [f for d in sorted(userdata.iterdir())
            if d.name.isdigit() and d.name != "0"
            and (f := d / "config" / "shortcuts.vdf").is_file()]


def _entries(root: dict):
    _, _, shortcuts = _get_ci(root, "shortcuts")
    if not isinstance(shortcuts, dict):
        return
    for idx, (t, entry) in shortcuts.items():
        if t == TYPE_MAP:
            yield entry


def _matches(entry: dict, names_norm: set[str]) -> bool:
    _, _, appname = _get_ci(entry, "AppName")
    return isinstance(appname, str) and _norm(appname) in names_norm


def find_game_dirs(steam_root: Path, names: list[str]) -> list[Path]:
    """Install dirs of non-Steam shortcuts whose AppName matches any name."""
    names_norm = {_norm(n) for n in names}
    found: list[Path] = []
    for f in _shortcut_files(steam_root):
        for entry in _entries(loads(f.read_bytes())):
            if not _matches(entry, names_norm):
                continue
            _, _, start_dir = _get_ci(entry, "StartDir")
            _, _, exe = _get_ci(entry, "Exe")
            for cand in (start_dir, exe):
                if not isinstance(cand, str) or not cand:
                    continue
                p = Path(cand.strip('"'))
                p = p if p.is_dir() else p.parent
                if p.is_dir() and p not in found:
                    found.append(p)
                    break
    return found


def find_appids(steam_root: Path, names: list[str]) -> list[int]:
    """Shortcut appids (unsigned, = compatdata prefix dir name) whose
    AppName matches any of the given names."""
    names_norm = {_norm(n) for n in names}
    ids = []
    for f in _shortcut_files(steam_root):
        for entry in _entries(loads(f.read_bytes())):
            if _matches(entry, names_norm):
                _, t, appid = _get_ci(entry, "appid")
                if t == TYPE_INT and appid and appid not in ids:
                    ids.append(appid)
    return ids


def get_launch_options(steam_root: Path, names: list[str]) -> dict[str, str]:
    """Current LaunchOptions per matching shortcut ("file#AppName" -> value)."""
    names_norm = {_norm(n) for n in names}
    result = {}
    for f in _shortcut_files(steam_root):
        for entry in _entries(loads(f.read_bytes())):
            if _matches(entry, names_norm):
                _, _, appname = _get_ci(entry, "AppName")
                _, _, lo = _get_ci(entry, "LaunchOptions")
                result[f"{f}#{appname}"] = lo if isinstance(lo, str) else ""
    return result


def set_appid(steam_root: Path, names: list[str], new_appid: int) -> int:
    """Rewrite the appid of every shortcut whose AppName matches. Steam
    must be closed. Used when adopting an existing compatdata prefix —
    the shortcut jumps to that prefix's appid rather than the prefix
    being renamed to Steam's chosen id. Returns files updated."""
    names_norm = {_norm(n) for n in names}
    new_appid = int(new_appid) & 0xFFFFFFFF
    updated = 0
    for f in _shortcut_files(steam_root):
        raw = f.read_bytes()
        root = loads(raw)
        changed = False
        for entry in _entries(root):
            if not _matches(entry, names_norm):
                continue
            key, t, current = _get_ci(entry, "appid")
            if t == TYPE_INT and current != new_appid:
                entry[key or "appid"] = (TYPE_INT, new_appid)
                changed = True
        if changed:
            shutil.copy2(f, f.with_suffix(".vdf.gfm-bak"))
            f.write_bytes(dumps(root))
            updated += 1
    return updated


def _user_config_dirs(steam_root: Path) -> list[Path]:
    """userdata/<id>/config dirs for real logged-in users (id != 0)."""
    userdata = steam_root / "userdata"
    if not userdata.is_dir():
        return []
    return [d / "config" for d in sorted(userdata.iterdir())
            if d.name.isdigit() and d.name != "0"]


def _set_field(entry: dict, name: str, t: int, val) -> None:
    """Set a shortcut field case-insensitively (preserving existing casing)."""
    k, _, _ = _get_ci(entry, name)
    entry[k or name] = (t, val)


def _new_entry(appname: str, exe: str, start_dir: str,
               launch_options: str, appid: int | None) -> dict:
    """A complete non-Steam shortcut entry. Exe/StartDir are quoted the way
    Steam stores them. Missing fields would let Steam re-derive an appid, so
    we write the full set including a forced appid when given."""
    entry: dict = {}
    if appid is not None:
        entry["appid"] = (TYPE_INT, int(appid) & 0xFFFFFFFF)
    entry.update({
        "AppName": (TYPE_STR, appname),
        "Exe": (TYPE_STR, f'"{exe}"'),
        "StartDir": (TYPE_STR, f'"{start_dir}"'),
        "icon": (TYPE_STR, ""),
        "ShortcutPath": (TYPE_STR, ""),
        "LaunchOptions": (TYPE_STR, launch_options),
        "IsHidden": (TYPE_INT, 0),
        "AllowDesktopConfig": (TYPE_INT, 1),
        "AllowOverlay": (TYPE_INT, 1),
        "OpenVR": (TYPE_INT, 0),
        "Devkit": (TYPE_INT, 0),
        "DevkitGameID": (TYPE_STR, ""),
        "DevkitOverrideAppID": (TYPE_INT, 0),
        "LastPlayTime": (TYPE_INT, 0),
        "FlatpakAppID": (TYPE_STR, ""),
        "tags": (TYPE_MAP, {}),
    })
    return entry


def ensure_shortcut(steam_root: Path, appname: str, exe: str, start_dir: str,
                    launch_options: str = "", appid: int | None = None,
                    aliases: list[str] | None = None) -> int:
    """Create (or update) a non-Steam shortcut in every user's shortcuts.vdf.
    Steam must be closed. Idempotent: an existing entry whose AppName matches
    appname/aliases is updated in place (Exe/StartDir/LaunchOptions/appid);
    otherwise a new entry is appended. Forcing `appid` to a gospel value makes
    Steam use that same id for the game's compatdata prefix, so a restored
    prefix lines up. Backs up each file to *.vdf.gfm-bak first. Returns files
    written."""
    names_norm = {_norm(appname)} | {_norm(a) for a in (aliases or [])}
    dirs = _user_config_dirs(steam_root)
    if not dirs:
        raise ShortcutsError(
            "no Steam user directory (userdata/<id>) — has Steam run and "
            "logged in on this machine?")
    written = 0
    for cfg in dirs:
        f = cfg / "shortcuts.vdf"
        root = loads(f.read_bytes()) if f.is_file() else {"shortcuts": (TYPE_MAP, {})}
        skey, _, shortcuts = _get_ci(root, "shortcuts")
        if not isinstance(shortcuts, dict):
            shortcuts = {}
            root[skey or "shortcuts"] = (TYPE_MAP, shortcuts)
        existing = next((e for _t, e in
                         ((t, v) for t, v in shortcuts.values())
                         if _t == TYPE_MAP and _matches(e, names_norm)), None)
        if existing is not None:
            _set_field(existing, "Exe", TYPE_STR, f'"{exe}"')
            _set_field(existing, "StartDir", TYPE_STR, f'"{start_dir}"')
            _set_field(existing, "LaunchOptions", TYPE_STR, launch_options)
            if appid is not None:
                _set_field(existing, "appid", TYPE_INT, int(appid) & 0xFFFFFFFF)
        else:
            idxs = [int(k) for k in shortcuts if str(k).isdigit()]
            nkey = str(max(idxs) + 1 if idxs else 0)
            shortcuts[nkey] = (TYPE_MAP, _new_entry(
                appname, exe, start_dir, launch_options, appid))
        cfg.mkdir(parents=True, exist_ok=True)
        if f.is_file():
            shutil.copy2(f, f.with_suffix(".vdf.gfm-bak"))
        f.write_bytes(dumps(root))
        written += 1
    return written


def set_launch_options(steam_root: Path, names: list[str], value: str) -> int:
    """Set LaunchOptions on every matching shortcut. Steam must be closed.
    Returns number of files updated."""
    names_norm = {_norm(n) for n in names}
    updated = 0
    for f in _shortcut_files(steam_root):
        raw = f.read_bytes()
        root = loads(raw)
        changed = False
        for entry in _entries(root):
            if not _matches(entry, names_norm):
                continue
            key, _, current = _get_ci(entry, "LaunchOptions")
            if current == value:
                continue
            entry[key or "LaunchOptions"] = (TYPE_STR, value)
            changed = True
        if changed:
            new = dumps(root)
            shutil.copy2(f, f.with_suffix(".vdf.gfm-bak"))
            f.write_bytes(new)
            updated += 1
    return updated
