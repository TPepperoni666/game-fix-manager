"""How wide is this text, really, and how many terminal rows will it eat?

Both in-place redraws in the TUI — the arrow picker's cursor-up and the copy
progress line's \\r — assume ONE LINE IS ONE ROW. That assumption breaks the
moment a line is wider than the terminal: the terminal wraps it, the picker's
cursor-up count comes out short and the menu walks down the screen, while
progress()'s \\r returns only to the start of the LAST wrapped row and leaves
the first one behind as litter. Both look like "it keeps adding lines".

len() is not the answer: menu options carry emoji (⬇️ Deploy, 🔧 Apply) and
those occupy two columns, so len() undercounts and a "clipped" line still
wraps. So: measure display columns, clip on display columns, and count rows
by dividing through by the real terminal width.
"""
from __future__ import annotations

import re
import unicodedata

# Colour codes are invisible — they must not count toward the width.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Blocks that terminals render double-width even though east_asian_width
# reports them Neutral (emoji proper, dingbats-turned-emoji, symbols).
_WIDE_RANGES = (
    (0x1F000, 0x1FAFF),   # mahjong … symbols & pictographs extended-A
    (0x1F900, 0x1F9FF),   # supplemental symbols and pictographs
    (0x2600, 0x27BF),     # misc symbols + dingbats (✔ ✅ ➤ …)
)


def display_width(text: str) -> int:
    """Visible columns `text` occupies, ignoring ANSI colour sequences."""
    text = ANSI_RE.sub("", text)
    width = 0
    for ch in text:
        if ord(ch) == 0xFE0F:
            # VS16: zero-width itself, but it promotes the PRECEDING base
            # character from 1 column to 2 (U+2B07 ⬇ -> ⬇️). Counting 1 here
            # makes the pair total 2 without tracking the previous char.
            width += 1
            continue
        if ord(ch) == 0xFE0E or unicodedata.combining(ch):   # VS15 = text style
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
            continue
        cp = ord(ch)
        width += 2 if any(lo <= cp <= hi for lo, hi in _WIDE_RANGES) else 1
    return width


def truncate(text: str, width: int) -> str:
    """Clip to `width` DISPLAY columns, adding an ellipsis when clipped.

    Operates on plain text — call it before wrapping anything in colour, or
    the escape codes get cut in half and leak into the terminal.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    if width == 1:
        return "…"
    out, used = [], 0
    for ch in text:
        w = display_width(ch)
        if used + w > width - 1:          # leave a column for the ellipsis
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def rows_used(text: str, cols: int) -> int:
    """Terminal rows a written block occupies once the terminal has wrapped it.

    This is what the picker must move the cursor up by — not the newline
    count. A line of exactly `cols` columns still occupies one row; anything
    past that spills into the next.
    """
    if cols <= 0:
        return text.count("\n")
    rows = 0
    for line in text.split("\n")[:-1]:    # trailing element is after the last \n
        w = display_width(line)
        rows += max(1, -(-w // cols))     # ceil division
    return rows
