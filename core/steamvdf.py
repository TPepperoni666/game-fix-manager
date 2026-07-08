"""Steam localconfig.vdf editing: read/write per-game launch options.

Launch options live in userdata/<accountid>/config/localconfig.vdf under
UserLocalConfigStore > Software > Valve > Steam > apps > <appid>. Steam
rewrites this file on exit, so writes are only safe while Steam is CLOSED —
callers batch writes and use close_steam()/start_steam() around them (the
same dance the Backup Manager's safe_vdf_edit uses).

Text-VDF only (Steam games). Non-Steam shortcuts live in the binary
shortcuts.vdf — not handled here yet.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

APPS_PATH = ("Software", "Valve", "Steam", "apps")


class VdfError(Exception):
    pass


# --- minimal text-VDF parser / writer -------------------------------------

def _lex(text: str):
    """Yield tokens: quoted strings (unescaped), '{' and '}'."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text[i:i + 2] == "//":
            i = text.find("\n", i)
            i = n if i == -1 else i
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            out, i = [], i + 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
                               .get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                raise VdfError("unterminated string")
            yield "".join(out)
            i += 1
        elif c == "[":  # platform conditional like [$WIN32] — skip
            i = text.find("]", i)
            if i == -1:
                raise VdfError("unterminated conditional")
            i += 1
        else:
            raise VdfError(f"unexpected character {c!r} at offset {i}")
    yield None


def vdf_loads(text: str) -> dict:
    tokens = _lex(text)

    def parse_block(tok_iter, terminator):
        block: dict = {}
        while True:
            tok = next(tok_iter)
            if tok == terminator:
                return block
            if tok in ("{", "}") or tok is None:
                raise VdfError(f"unexpected token {tok!r}")
            key = tok
            val = next(tok_iter)
            if val == "{":
                sub = parse_block(tok_iter, "}")
                # KeyValues allows duplicate keys; merge rather than clobber
                if isinstance(block.get(key), dict):
                    block[key].update(sub)
                else:
                    block[key] = sub
            elif isinstance(val, str):
                block[key] = val
            else:
                raise VdfError(f"key {key!r} has no value")

    return parse_block(tokens, None)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def vdf_dumps(tree: dict, indent: int = 0) -> str:
    pad = "\t" * indent
    lines = []
    for key, val in tree.items():
        if isinstance(val, dict):
            lines.append(f'{pad}"{_esc(key)}"')
            lines.append(f"{pad}{{")
            lines.append(vdf_dumps(val, indent + 1))
            lines.append(f"{pad}}}")
        else:
            lines.append(f'{pad}"{_esc(key)}"\t\t"{_esc(val)}"')
    return "\n".join(line for line in lines if line != "")


# --- launch options --------------------------------------------------------

def _child_ci(node: dict, name: str, create: bool = False) -> dict | None:
    """Case-insensitive child lookup (Steam's key casing varies)."""
    for k, v in node.items():
        if k.lower() == name.lower() and isinstance(v, dict):
            return v
    if create:
        node[name] = {}
        return node[name]
    return None


def _localconfigs(steam_root: Path) -> list[Path]:
    userdata = steam_root / "userdata"
    if not userdata.is_dir():
        return []
    out = []
    for d in userdata.iterdir():
        if d.name.isdigit() and d.name != "0":
            cfg = d / "config" / "localconfig.vdf"
            if cfg.is_file():
                out.append(cfg)
    return out


def _apps_node(tree: dict, create: bool = False) -> dict | None:
    root = next(iter(tree.values()), None)  # UserLocalConfigStore
    if not isinstance(root, dict):
        return None
    node = root
    for name in APPS_PATH:
        node = _child_ci(node, name, create=create)
        if node is None:
            return None
    return node


def _get_lo_ci(app: dict) -> tuple[str, str]:
    """(actual key name, value) for LaunchOptions, case-insensitive."""
    for k, v in app.items():
        if k.lower() == "launchoptions" and isinstance(v, str):
            return k, v
    return "LaunchOptions", ""


def get_launch_options(steam_root: Path, appid: int) -> dict[str, str]:
    """Current LaunchOptions per localconfig file (path -> value)."""
    result = {}
    for cfg in _localconfigs(steam_root):
        tree = vdf_loads(cfg.read_text(encoding="utf-8", errors="surrogateescape"))
        apps = _apps_node(tree)
        app = apps.get(str(appid)) if apps else None
        if isinstance(app, dict):
            result[str(cfg)] = _get_lo_ci(app)[1]
    return result


def set_launch_options(steam_root: Path, appid: int, value: str) -> int:
    """Write LaunchOptions for appid in every user's localconfig.vdf.
    Caller must ensure Steam is closed. Returns number of files updated."""
    updated = 0
    for cfg in _localconfigs(steam_root):
        text = cfg.read_text(encoding="utf-8", errors="surrogateescape")
        tree = vdf_loads(text)
        apps = _apps_node(tree, create=True)
        if apps is None:
            continue
        app = apps.setdefault(str(appid), {})
        if not isinstance(app, dict):
            continue
        key, current = _get_lo_ci(app)
        if current == value:
            continue
        app[key] = value
        shutil.copy2(cfg, cfg.with_suffix(".vdf.gfm-bak"))
        cfg.write_text(vdf_dumps(tree) + "\n", encoding="utf-8",
                       errors="surrogateescape")
        updated += 1
    return updated


# --- config.vdf: CompatToolMapping ----------------------------------------

_COMPAT_PATH = ("Software", "Valve", "Steam", "CompatToolMapping")


def _config_vdf(steam_root: Path) -> Path:
    return steam_root / "config" / "config.vdf"


def _compat_node(tree: dict, create: bool = False) -> dict | None:
    root = next(iter(tree.values()), None)  # InstallConfigStore
    if not isinstance(root, dict):
        return None
    node = root
    for name in _COMPAT_PATH:
        node = _child_ci(node, name, create=create)
        if node is None:
            return None
    return node


def get_compat_tool(steam_root: Path, appid: int) -> dict | None:
    """Read CompatToolMapping[appid] from config.vdf, or None."""
    cfg = _config_vdf(steam_root)
    if not cfg.is_file():
        return None
    tree = vdf_loads(cfg.read_text(encoding="utf-8", errors="surrogateescape"))
    node = _compat_node(tree)
    if not node:
        return None
    entry = node.get(str(appid))
    return entry if isinstance(entry, dict) else None


def set_compat_tool(steam_root: Path, appid: int, tool_name: str,
                    priority: str = "250") -> bool:
    """Force a Proton/compat tool for appid in config.vdf (Steam closed).
    tool_name="" removes the mapping (back to Steam's default). Works for
    both Steam appids and non-Steam shortcut appids — CompatToolMapping is
    keyed by appid regardless. Returns True if the file changed."""
    cfg = _config_vdf(steam_root)
    if not cfg.is_file():
        return False
    text = cfg.read_text(encoding="utf-8", errors="surrogateescape")
    tree = vdf_loads(text)
    node = _compat_node(tree, create=bool(tool_name))
    if node is None:
        return False
    key = str(appid)
    if not tool_name:
        if node.pop(key, None) is None:
            return False
    else:
        entry = node.get(key)
        if isinstance(entry, dict) and entry.get("name") == tool_name:
            return False  # already set
        node[key] = {"name": tool_name, "config": "", "priority": str(priority)}
    shutil.copy2(cfg, cfg.with_suffix(".vdf.gfm-bak"))
    cfg.write_text(vdf_dumps(tree) + "\n", encoding="utf-8",
                   errors="surrogateescape")
    return True


def remap_compat_tool(steam_root: Path, old_appid: int, new_appid: int) -> bool:
    """Move CompatToolMapping[old_appid] to [new_appid] in config.vdf.
    Steam must be closed. Returns True if a mapping moved."""
    cfg = _config_vdf(steam_root)
    if not cfg.is_file():
        return False
    text = cfg.read_text(encoding="utf-8", errors="surrogateescape")
    tree = vdf_loads(text)
    node = _compat_node(tree, create=False)
    if node is None:
        return False
    mapping = node.pop(str(old_appid), None)
    if mapping is None:
        return False
    node[str(new_appid)] = mapping
    shutil.copy2(cfg, cfg.with_suffix(".vdf.gfm-bak"))
    cfg.write_text(vdf_dumps(tree) + "\n", encoding="utf-8",
                   errors="surrogateescape")
    return True


# --- Steam process control (Linux; the Deck path) ---------------------------

def steam_running() -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                             capture_output=True, text=True).stdout
        return "steam.exe" in out
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


def close_steam(log) -> None:
    log("  🔴 Closing Steam (controller will drop out — this is normal)...")
    subprocess.run(["steam", "-shutdown"], capture_output=True)
    for _ in range(20):
        if not steam_running():
            return
        time.sleep(1)
    subprocess.run(["killall", "-TERM", "steam"], capture_output=True)
    time.sleep(3)


def start_steam(log) -> None:
    log("  🟢 Restarting Steam...")
    subprocess.Popen(["steam"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
