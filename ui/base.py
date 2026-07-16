"""The UI contract. Core never imports a concrete frontend — a GUI later
just implements this same interface."""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class UI(ABC):
    def progress(self, text: str) -> None:
        """Overwrite the current line with a live progress update.

        Concrete, not abstract: every frontend inherits it for free, and a
        GUI can override with a real progress bar. Goes straight to stdout
        rather than through msg() because msg() appends a line — a multi-GB
        copy would scroll thousands of them.
        """
        try:
            sys.stdout.write("\r\033[K" + text)
            sys.stdout.flush()
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            sys.stdout.write("\r" + text.encode(enc, "replace").decode(enc))
            sys.stdout.flush()
        except OSError:
            pass  # not a terminal — progress is cosmetic, never fatal

    def progress_done(self) -> None:
        """End a progress line so normal output resumes cleanly."""
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except OSError:
            pass

    @abstractmethod
    def header(self, title: str) -> None: ...

    @abstractmethod
    def msg(self, text: str, style: str = "info") -> None:
        """style: info | success | warn | error | dim"""

    @abstractmethod
    def choose(self, header: str, options: list[str],
               multi: bool = False) -> list[str]:
        """Return selected option(s); empty list means cancelled."""

    @abstractmethod
    def confirm(self, question: str, danger: bool = False) -> bool: ...

    @abstractmethod
    def input(self, prompt: str, default: str = "",
              password: bool = False) -> str: ...
