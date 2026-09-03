"""Answer "what is this machine actually running, and will the menus fit?"

Written 2026-07-31 after the same redraw bug got reported three times. The
fix went into the arrow picker — but get_ui() prefers the gum frontend when
the gum binary exists, and GumUI only uses the arrow picker for MULTI-select.
Every single-select menu was being drawn by an external `gum choose`, so a
real fix looked like no fix at all from the outside.

Nothing about that was visible without reading the source. The checks here
exist so the tool can say, on the machine that has the problem: which
frontend is live, which renderer draws which menu, how big the terminal
actually is, and whether what we're about to draw fits inside it. Output goes
through ui.msg, so it lands in gfm.log and Syncthings to the workstation —
the answer travels without anyone transcribing a terminal.

Everything here is a pure function of values passed in, so the awkward cases
(80x24 vs a Deck-sized Konsole, gum present vs absent) are testable on one
machine that only has one of them.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"

# What the arrow picker draws below the options. The longest fixed string the
# menu emits, so it's the natural canary for "is the terminal too narrow".
HINT_MULTI = "↑↓ move  •  ←→ toggle  •  a all  •  Enter confirm  •  Esc cancel"
HINT_SINGLE = "↑↓ move  •  Enter select  •  Esc back"

# tui_gum.choose: height = min(len(options) + 1, 15)
GUM_MAX_HEIGHT = 15
# What the main menu has already drawn before the chooser appears: gum's
# bordered header box (margin + border + text + border + margin = 5) plus the
# Store/Steam/Games status lines and a blank (4). Counting only the chooser
# says "fits" on a terminal where it plainly doesn't.
MENU_CHROME_ROWS = 9
# multiselect.MAX_WINDOW, plus the chrome _window_height reserves
PICKER_CHROME_ROWS = 5


@dataclass
class Row:
    label: str
    value: str
    verdict: str = INFO
    note: str = ""


def _fmt_bool(b: bool) -> str:
    return "yes" if b else "no"


def find_gum() -> str | None:
    """Same resolution order as tui_gum._find_gum, duplicated deliberately:
    this must report what the app WOULD pick even if importing the frontend
    fails for an unrelated reason."""
    path = shutil.which("gum")
    if path:
        return path
    cand = Path.home() / "scripts" / "bin" / ("gum.exe" if os.name == "nt"
                                              else "gum")
    return str(cand) if cand.is_file() else None


def terminal_size(default=(80, 24)) -> tuple[int, int]:
    try:
        s = os.get_terminal_size()
        return s.columns, s.lines
    except OSError:
        return default


def frontend_rows(gum: str | None) -> list[Row]:
    """Which frontend is live and — the bit that actually mattered — which
    renderer handles each kind of menu."""
    using_gum = gum is not None
    rows = [Row("UI frontend", "GumUI" if using_gum else "PlainUI",
                INFO, f"gum at {gum}" if using_gum else "gum not found")]
    # Both frontends hand multi-select to the arrow picker; only PlainUI
    # uses it for single-select too.
    rows.append(Row("menus (single-select)",
                    "gum choose (external binary)" if using_gum
                    else "arrow picker (built in)",
                    WARN if using_gum else OK,
                    "not width-aware: a long header or a list taller than the "
                    "terminal can leave artifacts on every keypress"
                    if using_gum else "clips to width, counts real rows"))
    rows.append(Row("pickers (multi-select)", "arrow picker (built in)", OK,
                    "Deploy's game list, Apply, Back Up Prefixes"))
    return rows


def terminal_rows(cols: int, rows_: int, stdin_tty: bool,
                  stdout_tty: bool, term: str) -> list[Row]:
    out = [Row("terminal size", f"{cols} x {rows_}",
               BAD if cols < 50 or rows_ < 15 else
               (WARN if cols < 70 or rows_ < 20 else OK),
               "menus need roughly 70x20 to draw comfortably")]
    out.append(Row("stdin / stdout a TTY", f"{_fmt_bool(stdin_tty)} / "
                   f"{_fmt_bool(stdout_tty)}",
                   OK if (stdin_tty and stdout_tty) else WARN,
                   "" if (stdin_tty and stdout_tty)
                   else "not a terminal — the arrow picker falls back to "
                        "numbered input, which needs a keyboard"))
    out.append(Row("TERM", term or "(unset)",
                   WARN if not term else INFO))
    return out


def fit_rows(cols: int, rows_: int, gum: str | None,
             longest_menu_label: int, option_count: int) -> list[Row]:
    """Will what we're about to draw actually fit? This is the check that
    would have caught the bug directly."""
    from ui.textwidth import display_width

    out = []
    hint_w = max(display_width(HINT_MULTI), display_width(HINT_SINGLE))
    out.append(Row("widest fixed hint line", f"{hint_w} cols",
                   BAD if hint_w > cols else OK,
                   f"wraps at this width ({cols}) — the picker would drift"
                   if hint_w > cols else "fits"))

    label_w = longest_menu_label + 9      # picker chrome: cursor + [x] + pad
    out.append(Row("widest menu entry", f"{label_w} cols (incl. chrome)",
                   WARN if label_w > cols else OK,
                   "clipped with an ellipsis" if label_w > cols else "fits"))

    if gum is not None:
        want = min(option_count + 1, GUM_MAX_HEIGHT)
        needed = want + MENU_CHROME_ROWS
        out.append(Row("gum chooser height",
                       f"{want} rows + {MENU_CHROME_ROWS} chrome = "
                       f"{needed} of {rows_}",
                       BAD if needed > rows_ else OK,
                       "taller than the terminal — this is what leaves "
                       "leftovers behind as you move the selector"
                       if needed > rows_
                       else "fits, but gum still ignores terminal WIDTH"))
    usable = rows_ - PICKER_CHROME_ROWS
    out.append(Row("arrow-picker viewport", f"{max(1, usable)} option rows",
                   WARN if usable < 3 else OK,
                   "very short terminal — the list will scroll a lot"
                   if usable < 3 else ""))
    return out


def build_rows() -> list[Row]:
    """Is this checkout new enough to have the fixes we think it has?
    Answers 'did you actually pull?' without anyone guessing."""
    out = []
    root = Path(__file__).resolve().parent.parent
    frozen = getattr(sys, "frozen", False)
    out.append(Row("running from", "frozen exe" if frozen else str(root),
                   INFO))
    has_tw = (root / "ui" / "textwidth.py").is_file()
    out.append(Row("narrow-terminal redraw fix", _fmt_bool(has_tw),
                   OK if has_tw else BAD,
                   "" if has_tw
                   else "ui/textwidth.py missing — this checkout predates the "
                        "fix; git pull"))
    head, dirty = "", []
    try:
        import subprocess
        r = subprocess.run(["git", "-C", str(root), "log", "-1",
                            "--format=%h %cs %s"], capture_output=True,
                           text=True, timeout=5)
        head = (r.stdout or "").strip()
        d = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
        dirty = [ln[3:] for ln in (d.stdout or "").splitlines()
                 if ln[:2].strip() and not ln.startswith("??")]
    except (OSError, subprocess.SubprocessError):
        pass
    out.append(Row("checkout HEAD", head or "(not a git checkout)", INFO))
    # THE missing signal. Adoption used to write into the tracked store, which
    # silently blocked every later `git pull --ff-only` — a Deck sat six
    # commits behind for a day and the only symptom was an update that failed
    # if you happened to try one. Offline, cheap, and would have caught it.
    if head:
        out.append(Row("local modifications", str(len(dirty)) or "0",
                       BAD if dirty else OK,
                       "these BLOCK every update until resolved — "
                       f"{', '.join(dirty[:4])}. Settings > 🩹 Repair fixes it"
                       if dirty else "nothing blocking an update"))
    return out


TIMERS = {
    "gfm-backup.timer": "full backup (Sundays 19:00)",
    "gfm-reclaim.timer": "reclaim (weekly)",
}


def timer_rows() -> list[Row]:
    """Are the scheduled jobs installed, enabled, and actually firing?

    The weekly prefix backup had an --auto path whose docstring called it
    "the weekly timer" while no timer existed to run it. Nothing surfaced
    that, so it silently never ran and the backup count sat unchanged for
    over a week. A job you can't see the status of is a job you can't trust."""
    import subprocess
    if os.name == "nt":
        return [Row("scheduled jobs", "n/a on Windows", INFO)]
    out = []
    for unit, what in TIMERS.items():
        try:
            en = subprocess.run(["systemctl", "--user", "is-enabled", unit],
                                capture_output=True, text=True, timeout=5)
            state = (en.stdout or en.stderr or "").strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            state = "unknown"
        last = ""
        try:
            lt = subprocess.run(
                ["systemctl", "--user", "show", unit,
                 "--property=LastTriggerUSec", "--value"],
                capture_output=True, text=True, timeout=5)
            last = (lt.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            pass
        never = last in ("", "0", "n/a")
        out.append(Row(unit, state,
                       OK if state == "enabled" and not never
                       else (WARN if state == "enabled" else BAD),
                       f"{what} — "
                       + ("NEVER fired yet" if never else f"last ran {last}")
                       + ("" if state == "enabled"
                          else "; not enabled, so it will not run")))
    return out


def nas_rows(payloads) -> list[Row]:
    """Is the NAS share actually mounted, and will it come back by itself?

    Three sessions have been lost to this. The share is a systemd .mount, and
    a .mount ALONE is 'static' — no [Install], so nothing starts it at boot.
    Without the .automount companion it is up until the next reboot and then
    silently gone. Worse, the mountpoint then reads as an ordinary empty
    directory, so anything that writes there lands on the internal disk and
    shadows the mount forever after.

    On SteamOS main, where OS updates land weekly, this needs to be visible
    rather than rediscovered."""
    import subprocess
    if os.name == "nt" or payloads is None:
        return [Row("NAS share", "n/a", INFO)]
    p = Path(payloads)
    mounted = False
    try:
        mounted = os.path.ismount(p)
    except OSError:
        pass
    # Shadowed: not a mountpoint, but something is in it. Content is then
    # coming off the internal disk, and "is it up?" by content would lie.
    shadowed = False
    if not mounted:
        try:
            shadowed = any(True for _ in p.iterdir())
        except OSError:
            pass
    out = [Row("NAS mounted", "yes" if mounted else "NO",
               OK if mounted else BAD,
               "" if mounted else
               ("the mountpoint has files in it but nothing is mounted — "
                "those are local, and they shadow the share" if shadowed
                else "nothing is mounted at the mountpoint"))]

    units = sorted(Path("/etc/systemd/system").glob("*game*fixes*"))
    kinds = {u.suffix for u in units}
    has_auto = ".automount" in kinds
    out.append(Row("NAS units", ", ".join(u.suffix.lstrip(".") for u in units)
                   or "none",
                   OK if has_auto else BAD,
                   "" if has_auto else
                   "no .automount — a .mount alone is 'static' and will NOT "
                   "start at boot; re-run Connect NAS Payloads"))
    if has_auto:
        auto = next(u.name for u in units if u.suffix == ".automount")
        try:
            r = subprocess.run(["systemctl", "is-enabled", auto],
                               capture_output=True, text=True, timeout=5)
            state = (r.stdout or r.stderr or "").strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            state = "unknown"
        out.append(Row("NAS automount", state,
                       OK if state == "enabled" else BAD,
                       "" if state == "enabled"
                       else "not enabled, so it won't fire at boot"))
    return out


# Removable media lands under /run/media on SteamOS (udisks) and /media on
# most desktop distros. A Steam library under either is a card or a USB disk,
# not internal storage — which is what makes "declared but not mounted" a
# fault rather than a preference.
REMOVABLE_ROOTS = ("/run/media", "/media")


def _unit_name_for(path: str) -> str:
    r"""systemd-escape --path, enough of it to look a unit up by name.

    /run/media/deck/SD_Card    -> run-media-deck-SD_Card
    /home/deck/mnt/game-fixes  -> home-deck-mnt-game\x2dfixes
    """
    out = []
    for ch in path.strip("/"):
        if ch == "/":
            out.append("-")
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") \
                or ch in "_.":
            out.append(ch)
        else:
            out.append("\\x%02x" % ord(ch))
    return "".join(out)


def _unescape_mount(s: str) -> str:
    """/proc/self/mounts octal-escapes the awkward characters in a path."""
    return (s.replace("\\040", " ").replace("\\011", "\t")
             .replace("\\012", "\n").replace("\\134", "\\"))


def _mount_fstype(path: str) -> str | None:
    """Filesystem type mounted exactly AT path, or None if nothing is.

    Deliberately not os.path.ismount(): the fstype is the interesting half.
    Stock SteamOS automounts ext4 and refuses everything else, so knowing the
    card is btrfs is most of the explanation for why it isn't here."""
    try:
        with open("/proc/self/mounts", encoding="utf-8",
                  errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and _unescape_mount(parts[1]) == path:
                    return parts[2]
    except OSError:
        pass
    return None


def boot_plan(path: str, unit_dir: Path, fstab: Path) -> tuple[str, str]:
    """How — if at all — does `path` get mounted at boot?

    Returns (kind, detail): ("mount"|"automount", unit name), ("fstab", line),
    or ("", "") when nothing anywhere will bring it back. Pure enough to point
    at a temp directory in tests, which is the only way to exercise the
    'nothing will mount this' branch on a machine where something does."""
    base = _unit_name_for(path)
    for suffix in (".mount", ".automount"):
        try:
            if (unit_dir / (base + suffix)).is_file():
                return suffix.lstrip("."), base + suffix
        except OSError:
            pass
    try:
        for ln in fstab.read_text(encoding="utf-8",
                                  errors="replace").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            fields = ln.split()
            if len(fields) >= 2 and fields[1] == path:
                return "fstab", ln
    except OSError:
        pass
    return "", ""


def _unit_enabled(unit: str) -> str:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-enabled", unit],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or r.stderr or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def sdcard_rows(steam_root, declared=None,
                unit_dir=Path("/etc/systemd/system"),
                fstab=Path("/etc/fstab"), app_counts=None) -> list[Row]:
    """Is the SD card here, and will it still be here after a reboot?

    Written 2026-09-03, after a SteamOS update quietly stopped mounting the
    card at boot. Stock SteamOS only automounts ext4 — holo-automount.sh
    rejects btrfs with 'wrong fstype' and exits — and the steamos-btrfs patch
    that fixes that had been silently REJECTED, because the same update
    renamed the script it patches and left the old name as a symlink, which
    GNU patch refuses to write through. Nothing in the system said any of
    this out loud.

    What made it expensive is that the card mounts fine by hand, so the fault
    only ever showed up downstream: 'the tool only sees the internal SSD',
    then 'I can't launch any games'. Two sessions on symptoms.

    Steam's own libraryfolders.vdf is the source of truth for 'there is meant
    to be a card here'. If it names a library under /run/media and nothing is
    mounted at it, every game on that card has vanished — worth reporting
    whatever the cause. The second row is the one that would actually have
    saved the time: mounted right now is not the same as mounted next boot."""
    # Windows has no /run/media convention, so there is nothing to look for —
    # but only when we're the ones doing the looking. A caller that hands us a
    # library list is driving deliberately (the tests do), and the logic below
    # is platform-independent once the paths are given.
    if os.name == "nt" and declared is None:
        return [Row("SD card", "n/a on Windows", INFO)]
    if declared is None:
        if steam_root is None:
            return [Row("SD card", "no Steam root — can't tell", INFO)]
        from . import detect
        declared = detect.declared_library_folders(Path(steam_root))
        if app_counts is None:
            app_counts = detect.declared_library_apps(Path(steam_root))
    # as_posix(), not str(): identical on Linux for the absolute paths we get
    # here, but it keeps the separators forward-facing so this is exercisable
    # from the Windows dev box, where str(Path("/run/media/…")) hands back
    # backslashes and every match below would quietly miss.
    cards = [q for q in (p.as_posix() if isinstance(p, Path) else str(p)
                         for p in declared)
             if q.startswith(REMOVABLE_ROOTS)]
    if not cards:
        return [Row("SD card", "none in Steam's library list", INFO,
                    "no library under " + " or ".join(REMOVABLE_ROOTS))]

    out: list[Row] = []
    for path in cards:
        fstype = _mount_fstype(path)
        mounted = fstype is not None
        games = (app_counts or {}).get(path)
        # A listed-but-absent library with NO games on it is cruft, not a
        # fault: Steam remembers every path a card was ever mounted at, so a
        # card that once came up as SD_Card1, or a Windows card plugged in
        # once, lingers forever. Calling those BAD would leave this section
        # permanently red, and a check that is always red gets ignored —
        # which is exactly how the real failure went unnoticed for two
        # sessions.
        stale = not mounted and games == 0
        if mounted:
            verdict, note = OK, fstype
        elif stale:
            verdict, note = WARN, (
                "Steam lists this library but records no games in it — a "
                "leftover from a card mounted here once. Harmless, but worth "
                "removing in Steam > Settings > Storage")
        else:
            verdict, note = BAD, (
                (f"{games} game(s) Steam records here are missing"
                 if games else "Steam still lists this library but nothing "
                               "is mounted there")
                + " — anything that launches from it will fail")
        out.append(Row("SD card mounted", path if mounted else f"{path} — NO",
                       verdict, note))
        if stale:
            # No point asking how a path we want GONE comes back at boot.
            continue
        kind, detail = boot_plan(path, unit_dir, fstab)
        if not kind:
            out.append(Row("SD card at boot", "nothing will mount it", BAD,
                           "no systemd unit and no fstab entry for this path. "
                           "Stock SteamOS automounts ext4 ONLY, so a btrfs "
                           "card stays unmounted until you mount it by hand"))
        elif kind == "fstab":
            out.append(Row("SD card at boot", "fstab", OK, detail))
        else:
            state = _unit_enabled(detail)
            out.append(Row("SD card at boot", f"{detail} ({state})",
                           OK if state == "enabled" else BAD,
                           "" if state == "enabled"
                           else "the unit exists but is not enabled, so "
                                "nothing starts it at boot"))
    return out


def env_rows(steam_root, store_root, payloads, payloads_up: bool) -> list[Row]:
    return [
        Row("host", socket.gethostname(), INFO,
            "this is the name values_by_host matches on"),
        Row("platform", f"{sys.platform}  python "
                        f"{sys.version_info.major}.{sys.version_info.minor}"),
        Row("Steam root", str(steam_root) if steam_root else "NOT FOUND",
            OK if steam_root else BAD),
        Row("recipe store", str(store_root), INFO),
        Row("local payloads (NAS)",
            str(payloads) if payloads else "not configured",
            OK if payloads_up else (WARN if payloads else INFO),
            # Only a problem if it's configured AND down. Not configured is a
            # choice (a Windows box with no NAS), not a fault.
            "unreachable — payloads and staged games won't resolve"
            if (payloads and not payloads_up) else ""),
    ]
