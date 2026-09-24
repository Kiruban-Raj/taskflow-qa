"""Selectors, generated from contracts/ui-contract.yaml.

Nothing here is hand-written. The UI contract declares every data-testid the
automation may depend on, and this module turns it into attribute access:

    from testkit.selectors import UI
    UI.login.submit          -> 'login-submit'
    UI.login.submit_css      -> '[data-testid="login-submit"]'

Why generate rather than hardcode: a renamed testid becomes an AttributeError
at import time, naming the exact attribute, instead of a timeout thirty
seconds into a browser run.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from .config import repo_root


class _Group(SimpleNamespace):
    """A section of the UI contract. Exposes both the raw id and a CSS form."""

    def __init__(self, name: str, entries: dict[str, str]):
        self._name = name
        self._entries = dict(entries)
        super().__init__(**entries)
        for key, value in entries.items():
            setattr(self, f"{key}_css", f'[data-testid="{value}"]')

    def __getattr__(self, item: str) -> str:
        known = ", ".join(sorted(self._entries)) if hasattr(self, "_entries") else ""
        raise AttributeError(
            f"'{item}' is not declared in the '{self._name}' section of "
            f"contracts/ui-contract.yaml. Declared: {known}"
        )

    def css(self, key: str) -> str:
        return f'[data-testid="{getattr(self, key)}"]'


def _load() -> SimpleNamespace:
    path = repo_root() / "contracts" / "ui-contract.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SimpleNamespace(
        **{section: _Group(section, entries) for section, entries in raw.items()}
    )


UI = _load()


def testid(value: str) -> str:
    """CSS selector for an arbitrary testid. Prefer UI.<section>.<name>_css."""
    return f'[data-testid="{value}"]'
