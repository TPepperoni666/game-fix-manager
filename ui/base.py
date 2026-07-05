"""The UI contract. Core never imports a concrete frontend — a GUI later
just implements this same interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class UI(ABC):
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
    def input(self, prompt: str, default: str = "") -> str: ...
