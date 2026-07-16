"""Zero-dependency fallback TUI (numbered menus over stdin). Works anywhere,
including Windows dev boxes — gum frontend is preferred on the Deck."""
from __future__ import annotations

import sys

from .base import UI

_STYLES = {"info": "", "success": "[OK] ", "warn": "[!] ", "error": "[X] ", "dim": "  "}


def _print(text: str) -> None:
    """Print, surviving consoles that can't render emoji (e.g. cp1252)."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc))


class PlainUI(UI):
    def header(self, title: str) -> None:
        _print(f"\n=== {title} ===")

    def msg(self, text: str, style: str = "info") -> None:
        _print(f"{_STYLES.get(style, '')}{text}")

    def choose(self, header: str, options: list[str], multi: bool = False) -> list[str]:
        if multi:
            try:
                from .multiselect import multiselect_arrows
                return multiselect_arrows(header, options)
            except NotImplementedError:
                pass  # fall through to numbered input on Windows / non-TTY
        _print(f"\n{header}")
        for i, opt in enumerate(options, 1):
            _print(f"  {i}) {opt}")   # _print, not print: options carry emoji
        hint = "numbers comma-separated, or 'a' for all" if multi else "number"
        raw = input(f"Select ({hint}, blank to cancel): ").strip()
        if not raw:
            return []
        if multi and raw.lower() == "a":
            return list(options)
        picks = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(options):
                picks.append(options[int(part) - 1])
        return picks if multi else picks[:1]

    def confirm(self, question: str, danger: bool = False) -> bool:
        tag = "[DANGER] " if danger else ""
        _print(f"{tag}{question}")   # questions can carry emoji; prompt can't
        return input("[y/N]: ").strip().lower() in ("y", "yes")

    def input(self, prompt: str, default: str = "",
              password: bool = False) -> str:
        if password:
            import getpass
            return getpass.getpass(f"{prompt}: ")
        suffix = f" [{default}]" if default else ""
        return input(f"{prompt}{suffix}: ").strip() or default
