#!/usr/bin/env python3
"""
wl_save_sync.py - Ghost Recon Wildlands cross-save helper (Windows <-> SteamOS).

Ubisoft Connect's cloud save does not work for Wildlands under Proton, so we make
Syncthing the single source of truth for the save folder and turn Connect's cloud
sync off on both machines. This script:

  setup       find the save folder + Connect settings on THIS machine, create the
              save folder if missing, turn Connect cloud sync off, print the exact
              path to hand to Syncthing.
  status      show whether the synced save folder is up to date, and whether the
              other machine has everything.
  push        force a rescan and wait until the peer has received the save.
  add-folder  create the Syncthing folder via its REST API (saves fighting the web
              UI on a handheld). Opt-in - it edits your Syncthing config.
  guard       Steam launch wrapper: wait until this machine has the newest save,
              then run the game. Nothing runs afterwards on purpose - Syncthing's
              watcher already pushes a finished save within seconds.

Stdlib only. Runs on SteamOS and Windows.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

WILDLANDS_APPID = "460930"
WILDLANDS_SAVE_ID = "3559"  # Ubisoft product id for the Steam release of Wildlands
DEFAULT_FOLDER_ID = "wildlands-save"

IS_WINDOWS = os.name == "nt"
HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wl-save-sync.log")


# --------------------------------------------------------------------------- log


def log(msg, quiet=False):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    if not quiet:
        print(msg, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ save folders


def _steam_roots():
    """Candidate Steam library roots that could hold compatdata/460930."""
    roots = []
    if IS_WINDOWS:
        return roots
    for p in (
        os.path.join(HOME, ".local", "share", "Steam"),
        os.path.join(HOME, ".steam", "steam"),
        os.path.join(HOME, ".steam", "root"),
    ):
        if os.path.isdir(p):
            roots.append(p)
    for media in ("/run/media", os.path.join("/run/media", os.environ.get("USER", "deck"))):
        if not os.path.isdir(media):
            continue
        try:
            for entry in os.listdir(media):
                cand = os.path.join(media, entry)
                if os.path.isdir(os.path.join(cand, "steamapps")):
                    roots.append(cand)
        except OSError:
            pass
    return roots


def find_prefix():
    """Locate the Wildlands Proton prefix (Linux only)."""
    override = os.environ.get("WL_PREFIX")
    if override:
        return override if os.path.isdir(override) else None
    for root in _steam_roots():
        pfx = os.path.join(root, "steamapps", "compatdata", WILDLANDS_APPID, "pfx")
        if os.path.isdir(pfx):
            return pfx
    return None


def ubisoft_root():
    """Root of the Ubisoft Game Launcher install that owns the savegames tree."""
    if IS_WINDOWS:
        base = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        return os.path.join(base, "Ubisoft", "Ubisoft Game Launcher")
    pfx = find_prefix()
    if not pfx:
        return None
    return os.path.join(
        pfx, "drive_c", "Program Files (x86)", "Ubisoft", "Ubisoft Game Launcher"
    )


def connect_settings_path():
    """Ubisoft Connect settings.yaml, where the cloud-sync toggle lives."""
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
        return os.path.join(local, "Ubisoft Game Launcher", "settings.yaml")
    pfx = find_prefix()
    if not pfx:
        return None
    return os.path.join(
        pfx, "drive_c", "users", "steamuser", "AppData", "Local",
        "Ubisoft Game Launcher", "settings.yaml",
    )


def find_account_ids(savegames_dir):
    """Ubisoft account folders under savegames/ (normally exactly one)."""
    try:
        return sorted(
            d for d in os.listdir(savegames_dir)
            if os.path.isdir(os.path.join(savegames_dir, d))
        )
    except OSError:
        return []


def find_save_dir(create=False):
    """Return (save_dir, note). save_dir is .../savegames/<account>/3559."""
    root = ubisoft_root()
    if not root:
        return None, "Ubisoft Game Launcher not found (Wildlands prefix missing?)"
    savegames = os.path.join(root, "savegames")
    if not os.path.isdir(savegames):
        if not create:
            return None, "no savegames folder yet at " + savegames
        os.makedirs(savegames, exist_ok=True)

    accounts = find_account_ids(savegames)
    forced = os.environ.get("WL_ACCOUNT_ID")
    if forced:
        accounts = [forced]
    if not accounts:
        return None, (
            "no Ubisoft account folder under " + savegames +
            " - launch Ubisoft Connect once and sign in, then re-run"
        )
    if len(accounts) > 1:
        return None, (
            "several account folders found (" + ", ".join(accounts) +
            ") - pick one and re-run with WL_ACCOUNT_ID=<id>"
        )

    save_dir = os.path.join(savegames, accounts[0], WILDLANDS_SAVE_ID)
    if not os.path.isdir(save_dir):
        if not create:
            return None, (
                "Wildlands save folder " + WILDLANDS_SAVE_ID + " does not exist yet under "
                + os.path.join(savegames, accounts[0])
            )
        os.makedirs(save_dir, exist_ok=True)
        return save_dir, "created (empty - it will fill from the other machine)"
    n = len([f for f in os.listdir(save_dir) if f.endswith(".save")])
    return save_dir, str(n) + " .save file(s)"


# ---------------------------------------------------------- Connect cloud toggle


def set_cloud_sync(enabled, quiet=False):
    """Set user.syncsavegames in Ubisoft Connect's settings.yaml."""
    path = connect_settings_path()
    if not path or not os.path.isfile(path):
        log("! Connect settings.yaml not found" + (" at " + path if path else ""), quiet)
        return False

    want = "true" if enabled else "false"
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        lines = fh.read().splitlines()

    out, found, changed = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("syncsavegames:"):
            found = True
            indent = line[: len(line) - len(line.lstrip())]
            new = indent + "syncsavegames: " + want
            changed = changed or new != line
            out.append(new)
        else:
            out.append(line)

    if not found:
        for i, line in enumerate(out):
            if line.rstrip() == "user:":
                out.insert(i + 1, "  syncsavegames: " + want)
                changed = True
                break
        else:
            out.append("user:")
            out.append("  syncsavegames: " + want)
            changed = True

    if not changed:
        log("  Connect cloud sync already " + ("on" if enabled else "off"), quiet)
        return True

    backup = path + ".wlsync-bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    log("  Connect cloud sync set to " + ("on" if enabled else "off") + " in " + path, quiet)
    return True


# ------------------------------------------------------------------- syncthing


class SyncthingError(Exception):
    pass


def _syncthing_config_candidates():
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
        return [os.path.join(local, "Syncthing", "config.xml")]
    return [
        os.path.join(HOME, ".local", "state", "syncthing", "config.xml"),
        os.path.join(HOME, ".config", "syncthing", "config.xml"),
        os.path.join(HOME, ".var", "app", "com.github.zocker_160.SyncThingy",
                     "config", "syncthing", "config.xml"),
        os.path.join(HOME, ".var", "app", "me.kozec.syncthingtk",
                     "config", "syncthing", "config.xml"),
    ]


class Syncthing(object):
    def __init__(self):
        cfg = os.environ.get("WL_ST_CONFIG")
        paths = [cfg] if cfg else _syncthing_config_candidates()
        self.config_path = next((p for p in paths if p and os.path.isfile(p)), None)
        if not self.config_path:
            raise SyncthingError(
                "Syncthing config.xml not found (looked in: " + ", ".join(paths) +
                ") - set WL_ST_CONFIG to its path"
            )
        root = ET.parse(self.config_path).getroot()
        gui = root.find("gui")
        if gui is None:
            raise SyncthingError("no <gui> section in " + self.config_path)
        self.apikey = (gui.findtext("apikey") or "").strip()
        address = (gui.findtext("address") or "127.0.0.1:8384").strip()
        scheme = "https" if (gui.get("tls", "false").lower() == "true") else "http"
        if address.startswith("0.0.0.0") or address.startswith(":"):
            address = "127.0.0.1" + address[address.rfind(":"):]
        self.base = scheme + "://" + address
        if not self.apikey:
            raise SyncthingError("no API key in " + self.config_path)

    def call(self, path, params=None, method="GET"):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        req.add_header("X-API-Key", self.apikey)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise SyncthingError("Syncthing API " + path + " failed: " + str(exc))
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body}

    # -- helpers -----------------------------------------------------------
    def folders(self):
        return self.call("/rest/config/folders")

    def devices(self):
        return self.call("/rest/config/devices")

    def my_id(self):
        return self.call("/rest/system/status").get("myID", "")

    def find_folder(self, wanted):
        for f in self.folders():
            if f.get("id") == wanted or f.get("label") == wanted:
                return f
        return None

    def peers(self, folder):
        mine = self.my_id()
        return [d["deviceID"] for d in folder.get("devices", [])
                if d.get("deviceID") and d["deviceID"] != mine]

    def device_name(self, device_id):
        for d in self.devices():
            if d.get("deviceID") == device_id:
                return d.get("name") or device_id[:7]
        return device_id[:7]

    def connected(self, device_id):
        conns = self.call("/rest/system/connections").get("connections", {})
        return bool(conns.get(device_id, {}).get("connected"))

    def status(self, folder_id):
        return self.call("/rest/db/status", {"folder": folder_id})

    def completion(self, folder_id, device_id):
        return self.call("/rest/db/completion",
                         {"folder": folder_id, "device": device_id})

    def scan(self, folder_id):
        return self.call("/rest/db/scan", {"folder": folder_id}, method="POST")


def conflicts_in(path):
    found = []
    for root, _dirs, files in os.walk(path):
        for f in files:
            if ".sync-conflict-" in f:
                found.append(os.path.join(root, f))
    return found


def resolve_folder(st, folder_ref):
    folder = st.find_folder(folder_ref)
    if not folder:
        have = ", ".join(f.get("id", "?") for f in st.folders()) or "(none)"
        raise SyncthingError(
            "no Syncthing folder with id/label '" + folder_ref + "'. Existing: " + have
        )
    return folder


# ------------------------------------------------------------------- commands


def cmd_setup(args):
    log("== Wildlands save sync - setup on " + ("Windows" if IS_WINDOWS else "Linux"))

    save_dir, note = find_save_dir(create=True)
    if not save_dir:
        log("! " + note)
        return 1
    log("  save folder : " + save_dir)
    log("  contents    : " + note)

    set_cloud_sync(False)

    log("")
    log("Add this path to Syncthing on this machine:")
    log("    Folder ID    : " + args.get("folder", DEFAULT_FOLDER_ID))
    log("    Folder Path  : " + save_dir)
    log("  Use the SAME Folder ID on both machines. Enable file versioning")
    log("  (Simple, keep 10) so a bad sync is always recoverable.")
    return 0


def cmd_status(args):
    st = Syncthing()
    folder = resolve_folder(st, args.get("folder", DEFAULT_FOLDER_ID))
    fid = folder["id"]
    s = st.status(fid)
    log("folder      : " + fid + "  (" + folder.get("path", "?") + ")")
    log("state       : " + str(s.get("state")) +
        "   need " + str(s.get("needFiles", 0)) + " file(s) / " +
        str(s.get("needBytes", 0)) + " bytes")
    if s.get("errors"):
        log("errors      : " + str(s.get("errors")))
    for dev in st.peers(folder):
        name = st.device_name(dev)
        conn = "connected" if st.connected(dev) else "OFFLINE"
        c = st.completion(fid, dev)
        log("peer        : " + name + " [" + conn + "] has " +
            str(round(c.get("completion", 0), 1)) + "% (needs " +
            str(c.get("needBytes", 0)) + " bytes)")
    bad = conflicts_in(folder.get("path", ""))
    if bad:
        log("! CONFLICTS - both machines edited the save:")
        for f in bad:
            log("    " + f)
    return 0


def _wait_local_in_sync(st, fid, timeout, quiet=False):
    """Wait until this machine has pulled everything the cluster knows about."""
    deadline = time.time() + timeout
    while True:
        s = st.status(fid)
        if s.get("state") == "idle" and not s.get("needBytes") and not s.get("needFiles"):
            return True
        if time.time() >= deadline:
            log("! still " + str(s.get("needBytes", 0)) + " bytes behind after " +
                str(timeout) + "s - launching anyway", quiet)
            return False
        log("  waiting for incoming save data (" + str(s.get("needBytes", 0)) +
            " bytes, state=" + str(s.get("state")) + ")", quiet)
        time.sleep(2)


def _wait_peers_have_it(st, fid, timeout, quiet=False):
    """Wait until every connected peer has our latest save."""
    folder = st.find_folder(fid)
    peers = st.peers(folder) if folder else []
    if not peers:
        log("  no peers on this folder - nothing to push to", quiet)
        return True
    deadline = time.time() + timeout
    while True:
        # Recomputed every pass: after a cold boot Syncthing may still be dialling
        # out, and "every connected peer" is trivially true when none are connected.
        connected = [d for d in peers if st.connected(d)]
        pending, confirmed = [], []
        for dev in connected:
            c = st.completion(fid, dev)
            if c.get("needBytes") or round(c.get("completion", 0)) < 100:
                pending.append(st.device_name(dev) + " " +
                               str(round(c.get("completion", 0), 1)) + "%")
            else:
                confirmed.append(st.device_name(dev))

        if confirmed and not pending:
            log("  save confirmed on: " + ", ".join(confirmed), quiet)
            return True

        if time.time() >= deadline:
            if not connected:
                log("! NO peers connected after " + str(timeout) +
                    "s - the save is NOT anywhere else yet", quiet)
            else:
                log("! peers still behind after " + str(timeout) + "s: " +
                    ", ".join(pending), quiet)
            return False

        if not connected:
            log("  no peers connected yet - waiting for one to come up", quiet)
        else:
            log("  pushing to " + ", ".join(pending), quiet)
        time.sleep(2)


def cmd_push(args):
    st = Syncthing()
    folder = resolve_folder(st, args.get("folder", DEFAULT_FOLDER_ID))
    fid = folder["id"]
    st.scan(fid)
    time.sleep(1)
    _wait_local_in_sync(st, fid, int(args.get("timeout", 120)))
    ok = _wait_peers_have_it(st, fid, int(args.get("timeout", 120)))
    return 0 if ok else 2


def cmd_add_folder(args):
    """Create the Syncthing folder via REST so you don't have to use the web UI."""
    st = Syncthing()
    fid = args.get("folder", DEFAULT_FOLDER_ID)
    ftype = args.get("type", "sendreceive")
    if ftype not in ("sendreceive", "receiveonly", "sendonly"):
        log("! --type must be sendreceive, receiveonly or sendonly")
        return 1

    existing = st.find_folder(fid)
    if existing:
        if existing.get("type") == ftype:
            log("folder '" + fid + "' already exists as " + ftype + " - nothing to do")
            return 0
        url = st.base + "/rest/config/folders/" + urllib.parse.quote(existing["id"])
        req = urllib.request.Request(url, method="PATCH",
                                     data=json.dumps({"type": ftype}).encode("utf-8"))
        req.add_header("X-API-Key", st.apikey)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            log("! could not change folder type: " + str(exc))
            return 1
        log("folder '" + fid + "' switched from " + str(existing.get("type")) +
            " to " + ftype)
        return 0

    save_dir, note = find_save_dir(create=True)
    if not save_dir:
        log("! " + note)
        return 1

    devices = [{"deviceID": st.my_id()}]
    peer_ref = args.get("peer")
    if peer_ref:
        for want in [p.strip() for p in peer_ref.split(",") if p.strip()]:
            match = None
            for d in st.devices():
                if d.get("name") == want or d.get("deviceID", "").startswith(want):
                    match = d
                    break
            if not match:
                names = ", ".join(d.get("name", "?") for d in st.devices())
                log("! no Syncthing device named '" + want + "'. Known devices: " + names)
                return 1
            devices.append({"deviceID": match["deviceID"]})
            log("  sharing with " + match.get("name", "?"))
    else:
        log("  no --peer given - folder will be created unshared; share it in the UI")

    body = {
        "id": fid,
        "label": "Wildlands Save",
        "path": save_dir,
        "type": ftype,
        "devices": devices,
        "fsWatcherEnabled": True,
        "fsWatcherDelayS": 5,
        "rescanIntervalS": 3600,
        "versioning": {
            "type": "simple",
            "params": {"keep": "10"},
            "cleanupIntervalS": 3600,
        },
    }
    url = st.base + "/rest/config/folders"
    req = urllib.request.Request(url, method="POST",
                                 data=json.dumps(body).encode("utf-8"))
    req.add_header("X-API-Key", st.apikey)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        log("! could not create folder: " + str(exc))
        return 1
    log("  created Syncthing folder '" + fid + "' (" + ftype + ") -> " + save_dir +
        " (" + note + ")")
    log("  versioning: simple, keep 10")
    return 0


def cmd_guard(args, command):
    """Wait until this machine has the newest save, THEN launch the game.

    Deliberately does nothing after the game exits. Syncthing's filesystem watcher
    already pushes a finished save within seconds, and the guard cannot stop you
    suspending the machine anyway - so a post-exit push bought nothing and only
    added a hand-off race to get wrong. Pre-launch is the part Syncthing can't do
    for itself: it is the only thing sitting between "press Play" and "the game
    opens the save file".
    """
    fid = args.get("folder", DEFAULT_FOLDER_ID)
    pre = int(args.get("pre-timeout", 90))
    quiet = True  # keep Steam's stdout clean; everything lands in the log

    log("=== guard: launching Wildlands ===", quiet)

    if args.get("no-cloud-guard") is None:
        try:
            set_cloud_sync(False, quiet=quiet)
        except Exception as exc:  # never block the game
            log("! cloud-sync re-assert failed: " + str(exc), quiet)

    st = None
    try:
        st = Syncthing()
        folder = resolve_folder(st, fid)
        fid = folder["id"]
        bad = conflicts_in(folder.get("path", ""))
        if bad:
            log("! CONFLICT FILES PRESENT - both machines wrote this save:", quiet)
            for f in bad:
                log("    " + f, quiet)
            log("! resolve these before trusting your progress", quiet)
        st.scan(fid)
        time.sleep(1)
        _wait_local_in_sync(st, fid, pre, quiet=quiet)
    except Exception as exc:
        log("! pre-launch sync check skipped: " + str(exc), quiet)

    if not command:
        log("! guard called with no command to run", quiet)
        return 1

    log("running: " + " ".join(command), quiet)
    try:
        rc = subprocess.call(command)
    except OSError as exc:
        log("! could not run the launch command: " + str(exc), quiet)
        return 1
    log("launch command returned code " + str(rc), quiet)
    log("=== guard: done (Syncthing handles the push from here) ===", quiet)
    return rc


# ----------------------------------------------------------------------- main

USAGE = """usage: wl_save_sync.py <command> [options] [-- <game command>]

commands:
  setup                     prepare this machine (find/create save dir, cloud sync off)
  status                    show sync state for the save folder
  push                      force a scan and wait until the peer has the save
  add-folder --peer NAME    create the Syncthing folder via the REST API
                            (if it exists, --type switches its send/receive mode)
  guard -- <command>        wait for the newest save, then run <command>

options:
  --folder ID               Syncthing folder id (default: """ + DEFAULT_FOLDER_ID + """)
  --peer NAME[,NAME]        Syncthing device name(s) to share with
  --type MODE               sendreceive (default) | receiveonly | sendonly
  --timeout N               seconds to wait in push (default 120)
  --pre-timeout N           seconds to wait for incoming saves in guard (default 90)
  --no-cloud-guard          don't re-assert Connect cloud sync = off in guard

env overrides:
  WL_PREFIX, WL_ACCOUNT_ID, WL_ST_CONFIG
"""


def parse(argv):
    if not argv:
        return None, {}, []
    cmd, rest = argv[0], argv[1:]
    opts, command = {}, []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            command = rest[i + 1:]
            break
        if tok.startswith("--"):
            key = tok[2:]
            if key in ("no-cloud-guard",):
                opts[key] = True
            elif i + 1 < len(rest):
                opts[key] = rest[i + 1]
                i += 1
        i += 1
    return cmd, opts, command


def main(argv):
    cmd, opts, command = parse(argv)
    if cmd in (None, "-h", "--help", "help"):
        print(USAGE)
        return 0
    try:
        if cmd == "setup":
            return cmd_setup(opts)
        if cmd == "status":
            return cmd_status(opts)
        if cmd == "push":
            return cmd_push(opts)
        if cmd == "add-folder":
            return cmd_add_folder(opts)
        if cmd == "guard":
            return cmd_guard(opts, command)
    except SyncthingError as exc:
        log("! " + str(exc))
        return 1
    print("unknown command: " + str(cmd))
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
