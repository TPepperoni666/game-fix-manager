"""Gum-powered TUI — matches the look of the existing Backup Manager and
reuses the gum binary it installs to ~/scripts/bin."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import UI

_GUM_OPTS = ["--cursor.foreground=212", "--item.foreground=250",
             "--selected.foreground=212", "--header.foreground=99"]
_COLORS = {"info": "250", "success": "46", "warn": "212", "error": "196", "dim": "240"}


def _find_gum() -> str | None:
    path = shutil.which("gum")
    if path:
        return path
    candidate = Path.home() / "scripts" / "bin" / ("gum.exe" if os.name == "nt" else "gum")
    return str(candidate) if candidate.is_file() else None


class GumUI(UI):
    @staticmethod
    def available() -> bool:
        return _find_gum() is not None

    def __init__(self):
        self.gum = _find_gum()

    def _run(self, args: list[str], capture: bool = True) -> str:
        result = subprocess.run([self.gum, *args], text=True,
                                capture_output=capture)
        return (result.stdout or "").rstrip("\n") if capture else ""

    def header(self, title: str) -> None:
        subprocess.run("clear" if os.name != "nt" else "cls", shell=True)
        self._run(["style", "--foreground", "99", "--border-foreground", "99",
                   "--border", "double", "--align", "center", "--width", "60",
                   "--margin", "1 1", "--padding", "0 2", title], capture=False)

    def msg(self, text: str, style: str = "info") -> None:
        self._run(["style", "--foreground", _COLORS.get(style, "250"), f"  {text}"],
                  capture=False)

    def choose(self, header: str, options: list[str], multi: bool = False) -> list[str]:
        args = ["choose", *_GUM_OPTS, "--header", header]
        if multi:
            args.append("--no-limit")
        out = self._run([*args, *options])
        return [line for line in out.splitlines() if line]

    def confirm(self, question: str, danger: bool = False) -> bool:
        color = "196" if danger else "212"
        result = subprocess.run(
            [self.gum, "confirm", question,
             "--affirmative", "Yes", "--negative", "No",
             f"--selected.background={color}", "--selected.foreground=0"])
        return result.returncode == 0

    def input(self, prompt: str, default: str = "") -> str:
        return self._run(["input", "--placeholder", prompt, "--width", "50",
                          "--value", default])
