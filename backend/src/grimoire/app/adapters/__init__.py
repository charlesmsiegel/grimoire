"""The app's adapters (108): one module per Wave 7 sprint, all imported here once so no sprint edits a shared
list. Add ops to your own sprint's module; see ``grimoire.app.adapter``."""

from __future__ import annotations

from . import campaign, imports, inbox, media, play, providers, shell, sources

#: Every adapter the app serves, in name order.
ADAPTERS = tuple(module.ADAPTER for module in (campaign, imports, inbox, media, play, providers, shell, sources))

from . import shelf  # noqa: E402 -- tags, pins and saved filters (docs/SHELF.md)
from . import speech  # noqa: E402 -- voice status, one listening window, captions, read-aloud (067, docs/SPEECH.md)

ADAPTERS = tuple(sorted((*ADAPTERS, shelf.ADAPTER, speech.ADAPTER), key=lambda adapter: adapter.name))

from . import voice  # noqa: E402 -- 061's private local voice input (docs/SPEECH.md)

ADAPTERS = tuple(sorted((*ADAPTERS, voice.ADAPTER), key=lambda adapter: adapter.name))

from . import chronicle  # noqa: E402 -- 115's campaign clock and sourced memory (docs/CHRONICLE.md)

ADAPTERS = tuple(sorted((*ADAPTERS, chronicle.ADAPTER), key=lambda adapter: adapter.name))

from . import companions, ensemble, table  # noqa: E402 -- 115's play at the table: 058, 051, 050 (docs/TABLE.md)

ADAPTERS = tuple(sorted((*ADAPTERS, companions.ADAPTER, ensemble.ADAPTER, table.ADAPTER),
                        key=lambda adapter: adapter.name))

__all__ = ["ADAPTERS"]
