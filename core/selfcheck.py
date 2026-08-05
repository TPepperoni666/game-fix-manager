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
