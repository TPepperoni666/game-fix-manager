"""UI frontends. get_ui() picks the best available one."""
from __future__ import annotations

from .base import UI


def get_ui() -> UI:
    from .tui_gum import GumUI
    from .tui_plain import PlainUI
    return GumUI() if GumUI.available() else PlainUI()
