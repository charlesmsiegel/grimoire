"""The app's adapters (108): one module per Wave 7 sprint, all imported here once so no sprint edits a shared
list. Add ops to your own sprint's module; see ``grimoire.app.adapter``."""

from __future__ import annotations

from . import campaign, imports, inbox, media, play, providers, shell, sources

#: Every adapter the app serves, in name order.
ADAPTERS = tuple(module.ADAPTER for module in (campaign, imports, inbox, media, play, providers, shell, sources))

from . import shelf  # noqa: E402 -- tags, pins and saved filters (docs/SHELF.md)

ADAPTERS = tuple(sorted((*ADAPTERS, shelf.ADAPTER), key=lambda adapter: adapter.name))

__all__ = ["ADAPTERS"]
