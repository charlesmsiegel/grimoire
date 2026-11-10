"""The app's adapters (108): one module per Wave 7 sprint, all imported here once so no sprint edits a shared
list. Add ops to your own sprint's module; see ``grimoire.app.adapter``."""

from __future__ import annotations

from . import campaign, imports, media, play, providers, shell, sources

#: Every adapter the app serves, in name order.
ADAPTERS = tuple(module.ADAPTER for module in (campaign, imports, media, play, providers, shell, sources))

__all__ = ["ADAPTERS"]
