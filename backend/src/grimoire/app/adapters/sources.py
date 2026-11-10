"""Sprint 109's adapter ops: the Library's sources, entities, portrayals and attaching. Called as ``sources.<op>``.

Only Sprint 109 edits this module (or a ``sources`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.

Every op is ``grimoire.sourceapi``'s own (docs/SOURCE_AUTHORING.md), built over the text API's open library, its
owner's trusted handle and its chat library, so a source edit and an attach run under the same writer lock as a post
and answer the same refusals. Nothing here reads or writes the store itself.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.sourceapi import SourceApi

#: The ops, by their own name (``catalog`` is called as ``sources.catalog``).
OPS = ("catalog", "read", "create_source", "rename_source", "create", "save", "save_raw", "derive", "portrayal",
       "chats", "attach")


def _op(name: str) -> Handler:
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        api = SourceApi.over(ctx.store, ctx.handle, ctx.chats, owner=ctx.owner)
        return api.call("sources." + name, args)

    handler.__name__ = "sources_" + name
    return handler


ADAPTER = Adapter("sources", owner="109", ops={name: _op(name) for name in OPS})

__all__ = ["ADAPTER", "OPS"]
