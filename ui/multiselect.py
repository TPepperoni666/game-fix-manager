"""Raw-TTY multi-select: navigate with arrows, TOGGLE with left/right,
Enter confirms, Esc cancels.

Rationale: the Steam Deck's default desktop controller layout binds the
D-pad and A/B but leaves SPACE, TAB and other keys unmapped. Gum's built-in
multi-select requires SPACE, which forces users into the on-screen keyboard
or custom bindings. This picker uses ONLY keys that the default layout
already sends (arrows + Enter/Esc), so multi-select works out of the box.

Falls back with NotImplementedError on platforms without termios (Windows
dev box, non-TTY stdin), so callers can drop back to their existing pickers.
"""
from __future__ import annotations

import os
import sys

from .textwidth import display_width, rows_used, truncate as _truncate  # noqa: F401

# Colors matched to gum's palette (99 = purple, 212 = pink-ish, 240 = dim).
_HEADER = "\x1b[38;5;99m"
_CURSOR = "\x1b[38;5;212m"
_CHECK = "\x1b[38;5;46m"
_DIM = "\x1b[38;5;240m"
_RESET = "\x1b[0m"

MAX_WINDOW = 15   # cap the viewport like gum's single-select does


def _window_height(total: int, rows: int, header_lines: int) -> int:
    """How many option rows fit, leaving room for header, a blank, the
    position line, a blank and the controls hint — and never taller than the
    terminal (which is what made the whole thing overflow before)."""
    avail = rows - header_lines - 5          # blank+position+blank+hint+margin
    return max(1, min(total, avail, MAX_WINDOW))


def _scroll_top(cursor: int, top: int, total: int, height: int) -> int:
    """New top-of-window index that keeps the cursor visible without scrolling
    past either end. Pure so the scroll logic is testable off a TTY."""
    if cursor < top:
        top = cursor
    elif cursor >= top + height:
        top = cursor - height + 1
    return max(0, min(top, max(0, total - height)))


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape processing for the Windows console so the cursor
    moves and colors render instead of printing literal escape codes. Win10+
    supports it; it just isn't on by default in cmd.exe. No-op elsewhere."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)            # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        k.SetConsoleMode(h, mode.value | 0x0004)  # VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False


def _read_key_windows() -> str:
    """One keypress on Windows via msvcrt. Arrow keys arrive as a 0x00/0xE0
    prefix then a scan code — this is what lets the SAME D-pad picker work on
    an HTPC (Steam Input maps the pad to arrows) as on the Deck."""
    import msvcrt
    ch = msvcrt.getch()
    if ch in (b"\x00", b"\xe0"):           # special-key prefix
        code = msvcrt.getch()
        return {b"H": "up", b"P": "down",
                b"K": "left", b"M": "right"}.get(code, "other")
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x1b":
        return "esc"
    if ch == b"\x03":
        return "ctrl-c"
    if ch == b" ":
        return "space"
    try:
        return ch.decode("utf-8", errors="ignore")
    except Exception:
        return "other"


def _read_key() -> str:
    """One keypress from stdin, decoded to a symbolic name."""
    if os.name == "nt":
        return _read_key_windows()
    fd = sys.stdin.fileno()
    import termios
    import tty
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if first == b"\x1b":  # escape sequence — arrow keys are 3 bytes total
        # Read up to 2 more bytes non-blocking so a bare Esc doesn't wait
        try:
            import select
            more = b""
            if select.select([fd], [], [], 0.05)[0]:
                more = os.read(fd, 2)
        except Exception:
            more = b""
        seq = first + more
        return {
            b"\x1b[A": "up", b"\x1b[B": "down",
            b"\x1b[C": "right", b"\x1b[D": "left",
        }.get(seq, "esc")
    if first in (b"\r", b"\n"):
        return "enter"
    if first == b"\x03":
        return "ctrl-c"
    if first == b" ":
        return "space"  # accept SPACE too — some users muscle-memory this
    return first.decode("utf-8", errors="ignore")


def multiselect_arrows(header: str, options: list[str],
                       multi: bool = True) -> list[str]:
    """Interactive arrow-key picker with a scrolling viewport, so long lists
    (Deploy, Apply, Back Up Prefixes) and long labels stay readable instead of
    overflowing.

    multi=True  — tick several items, Enter confirms the ticked set.
    multi=False — plain menu: Enter picks whatever's highlighted. This is what
                  makes the MENUS controller-drivable; the numbered fallback
                  needs a keyboard (or an on-screen one), which is useless on a
                  couch with a pad.

    Controls:
      Up / Down (D-pad)     — move highlight (window scrolls to follow)
      Left / Right or SPACE — toggle current item        (multi only)
      a                     — toggle ALL on/off          (multi only)
      Enter (A on Deck)     — confirm
      Esc (B on Deck)       — cancel, return []
    """
    try:
        import msvcrt  # noqa: F401  (Windows key reader)
    except ImportError:
        msvcrt = None
    if os.name == "nt":
        if msvcrt is None or not _enable_windows_ansi():
            raise NotImplementedError("no interactive Windows console")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise NotImplementedError("stdin/stdout is not a TTY")
    if not options:
        return []

    selected = [False] * len(options)
    cursor = 0
    top = 0
    drawn = 0
    header_lines = len(header.splitlines())

    def draw() -> None:
        nonlocal drawn, top
        try:
            cols, rows = os.get_terminal_size()
        except OSError:
            cols, rows = 80, 24
        height = _window_height(len(options), rows, header_lines)
        top = _scroll_top(cursor, top, len(options), height)
        scrolling = len(options) > height
        label_w = max(10, cols - 9)   # cursor + "[x]" + spaces + margin

        # Every emitted line is clipped to this. A line wider than the
        # terminal wraps, and a wrapped line makes the cursor-up count below
        # too small — which is what walked the menu down the screen on every
        # keypress. One column spare: some terminals wrap at exactly `cols`.
        line_w = max(10, cols - 1)

        buf = []
        if drawn:
            buf.append(f"\x1b[{drawn}A\x1b[J")   # redraw in place
        for line in header.splitlines():
            buf.append(f"{_HEADER}{_truncate(line, line_w)}{_RESET}\n")
        buf.append("\n")
        end = min(top + height, len(options))
        for i in range(top, end):
            opt = _truncate(options[i], label_w)
            if not multi:                        # plain menu — no checkboxes
                if i == cursor:
                    buf.append(f" {_CURSOR}▶ {opt}{_RESET}\n")
                else:
                    buf.append(f"   {opt}\n")
                continue
            mark = f"{_CHECK}✔{_RESET}" if selected[i] else " "
            if i == cursor:
                buf.append(f" {_CURSOR}▶ [{mark}{_CURSOR}] {opt}{_RESET}\n")
            else:
                buf.append(f"   [{mark}]  {opt}\n")
        if scrolling:                            # position line only when it scrolls
            more = []
            if top > 0:
                more.append(f"▲ {top} above")
            if end < len(options):
                more.append(f"▼ {len(options) - end} below")
            pos = (f"   ({end - top} of {len(options)}"
                   + ("  " + "  ".join(more) if more else "") + ")")
            buf.append(f"{_DIM}{_truncate(pos, line_w)}{_RESET}\n")
        hint = ("↑↓ move  •  ←→ toggle  •  a all  •  Enter confirm  •  Esc cancel"
                if multi else "↑↓ move  •  Enter select  •  Esc back")
        buf.append(f"\n{_DIM}{_truncate(hint, line_w)}{_RESET}\n")
        text = "".join(buf)
        # Rows, not newlines: this is what the cursor-up above has to undo.
        drawn = rows_used(text, cols)
        sys.stdout.write(text)
        sys.stdout.flush()

    draw()
    try:
        while True:
            key = _read_key()
            if key == "up":
                cursor = (cursor - 1) % len(options)
            elif key == "down":
                cursor = (cursor + 1) % len(options)
            elif key in ("left", "right", "space"):
                if not multi:
                    continue                     # nothing to toggle in a menu
                selected[cursor] = not selected[cursor]
            elif key in ("a", "A"):              # toggle all on/off
                if not multi:
                    continue
                new = not all(selected)
                selected = [new] * len(options)
            elif key == "enter":
                if not multi:
                    return [options[cursor]]     # pick what's highlighted
                return [opt for opt, sel in zip(options, selected) if sel]
            elif key in ("esc", "ctrl-c"):
                return []
            else:
                continue
            draw()
    finally:
        sys.stdout.write("\n")
        sys.stdout.flush()
