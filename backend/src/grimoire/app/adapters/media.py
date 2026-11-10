"""Sprint 114's adapter ops: art, sync, conflicts and resilient presentation. Called as ``media.<op>``.

Only Sprint 114 edits this module (or a ``media`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.

Every op is one of ``grimoire.mediaapi.MediaSyncApi``'s (docs/MEDIA_SYNC_UI.md), which writes only through 038's
image library and 019/028's sync, under the text API's one writer lock. One ``MediaSyncApi`` serves each open
library for as long as it is open, because it remembers the devices paired while the app runs.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.mediaapi import MediaSyncApi
from grimoire.textapi import TextApi

#: ``media.<op>`` and the adapter op it runs: pictures as ``media.*``, devices and sync as ``sync.*``.
OPS = {
    "scopes": "media.scopes", "gallery": "media.gallery", "art": "media.art", "upload": "media.upload",
    "place": "media.place",
    "devices": "sync.devices", "identify": "sync.identify", "pair": "sync.pair", "sync": "sync.run",
    "conflicts": "sync.conflicts", "resolve": "sync.resolve", "forks": "sync.forks", "choose": "sync.choose",
}

_served: weakref.WeakKeyDictionary[TextApi, MediaSyncApi] = weakref.WeakKeyDictionary()
_guard = threading.Lock()


def media_of(api: TextApi) -> MediaSyncApi:
    """The one media and sync adapter over this open library. It reaches the library through a weak proxy, so
    the adapter goes when the library does instead of keeping it alive from this table."""
    with _guard:
        found = _served.get(api)
        if found is None:
            found = _served[api] = MediaSyncApi(weakref.proxy(api))
        return found


def _op(name: str) -> Handler:
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        return media_of(ctx.api).call(name, args)
    handler.__name__ = handler.__qualname__ = name.replace(".", "_")
    return handler


ADAPTER = Adapter("media", owner="114", ops={op: _op(name) for op, name in OPS.items()})

__all__ = ["ADAPTER", "OPS", "media_of"]
