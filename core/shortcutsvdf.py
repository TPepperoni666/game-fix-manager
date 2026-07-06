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
