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
    """Interactive multi-select with arrow-key toggling.

    Controls:
      Up / Down (D-pad)   — move highlight
      Left / Right or SPACE — toggle current item
      Enter (A on Deck)   — confirm selected items
      Esc (B on Deck)     — cancel, return []
    """
    if os.name == "nt":
        raise NotImplementedError("raw TTY unsupported on Windows")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise NotImplementedError("stdin/stdout is not a TTY")
    if not options:
        return []

    selected = [False] * len(options)
    cursor = 0
    drawn = 0

    def draw() -> None:
        nonlocal drawn
        buf = []
        if drawn:
            # Move up and clear from there so we redraw in place
            buf.append(f"\x1b[{drawn}A\x1b[J")
        for line in header.splitlines():
            buf.append(f"{_HEADER}{line}{_RESET}\n")
        buf.append("\n")
        for i, opt in enumerate(options):
            mark = f"{_CHECK}✔{_RESET}" if selected[i] else " "
            if i == cursor:
                buf.append(f" {_CURSOR}▶ [{mark}{_CURSOR}] {opt}{_RESET}\n")
            else:
                buf.append(f"   [{mark}]  {opt}\n")
        buf.append(f"\n{_DIM}↑↓ move  •  ←→ toggle  •  Enter confirm  "
                   f"•  Esc cancel{_RESET}\n")
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
