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
                block[key] = parse_block(tok_iter, "}")
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


def get_launch_options(steam_root: Path, appid: int) -> dict[str, str]:
    """Current LaunchOptions per localconfig file (path -> value)."""
    result = {}
    for cfg in _localconfigs(steam_root):
        tree = vdf_loads(cfg.read_text(encoding="utf-8", errors="replace"))
        apps = _apps_node(tree)
        app = apps.get(str(appid)) if apps else None
        if isinstance(app, dict):
            result[str(cfg)] = app.get("LaunchOptions", "")
    return result


def set_launch_options(steam_root: Path, appid: int, value: str) -> int:
    """Write LaunchOptions for appid in every user's localconfig.vdf.
    Caller must ensure Steam is closed. Returns number of files updated."""
    updated = 0
    for cfg in _localconfigs(steam_root):
        text = cfg.read_text(encoding="utf-8", errors="replace")
        tree = vdf_loads(text)
        apps = _apps_node(tree, create=True)
        if apps is None:
            continue
        app = apps.setdefault(str(appid), {})
        if not isinstance(app, dict):
            continue
        if app.get("LaunchOptions", "") == value:
            continue
        app["LaunchOptions"] = value
        shutil.copy2(cfg, cfg.with_suffix(".vdf.gfm-bak"))
        cfg.write_text(vdf_dumps(tree) + "\n", encoding="utf-8")
        updated += 1
    return updated


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
