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

# Colors matched to gum's palette (99 = purple, 212 = pink-ish, 240 = dim).
_HEADER = "\x1b[38;5;99m"
_CURSOR = "\x1b[38;5;212m"
_CHECK = "\x1b[38;5;46m"
_DIM = "\x1b[38;5;240m"
_RESET = "\x1b[0m"

MAX_WINDOW = 15   # cap the viewport like gum's single-select does


def _truncate(text: str, width: int) -> str:
    """Clip a label to width columns with an ellipsis, so it never wraps to a
    second terminal row (wrapping is what threw off the old redraw math)."""
    if width <= 1:
        return text[:max(0, width)]
    return text if len(text) <= width else text[:width - 1] + "…"


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


def _read_key() -> str:
    """One keypress from stdin, decoded to a symbolic name."""
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


def multiselect_arrows(header: str, options: list[str]) -> list[str]:
    """Interactive multi-select with arrow-key toggling and a scrolling
    viewport, so long lists (Deploy, Apply, Back Up Prefixes) and long labels
    stay readable on the Deck instead of overflowing.

    Controls:
      Up / Down (D-pad)     — move highlight (window scrolls to follow)
      Left / Right or SPACE — toggle current item
      a                     — toggle ALL on/off
      Enter (A on Deck)     — confirm selected items
      Esc (B on Deck)       — cancel, return []
    """
    if os.name == "nt":
        raise NotImplementedError("raw TTY unsupported on Windows")
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

        buf = []
        if drawn:
            buf.append(f"\x1b[{drawn}A\x1b[J")   # redraw in place
        for line in header.splitlines():
            buf.append(f"{_HEADER}{line}{_RESET}\n")
        buf.append("\n")
        end = min(top + height, len(options))
        for i in range(top, end):
            opt = _truncate(options[i], label_w)
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
            buf.append(f"   {_DIM}({end - top} of {len(options)}"
                       + ("  " + "  ".join(more) if more else "") + f"){_RESET}\n")
        buf.append(f"\n{_DIM}↑↓ move  •  ←→ toggle  •  a all  •  Enter "
                   f"confirm  •  Esc cancel{_RESET}\n")
        text = "".join(buf)
        drawn = text.count("\n")
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
                selected[cursor] = not selected[cursor]
            elif key in ("a", "A"):              # toggle all on/off
                new = not all(selected)
                selected = [new] * len(options)
            elif key == "enter":
                return [opt for opt, sel in zip(options, selected) if sel]
            elif key in ("esc", "ctrl-c"):
                return []
            else:
                continue
            draw()
    finally:
        sys.stdout.write("\n")
        sys.stdout.flush()
