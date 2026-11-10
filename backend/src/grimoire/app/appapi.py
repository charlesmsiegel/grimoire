"""What the app's server calls for ``POST /api/<op>`` (108): the text API (044) plus every sprint's adapter ops.

A dotted op (``sources.list``) goes to its adapter, run through ``TextApi.serialized``; anything else goes to the
text API itself. ``library`` lists every op, core and adapter, so a page can tell which of its actions exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from grimoire.textapi import ApiRefused, TextApi

from .adapter import Adapter, AdapterContext, Handler
from .adapters import ADAPTERS


class AppApi:
    """The text API plus the app's adapters: what the server calls for ``POST /api/<op>``. A dotted op goes to
    its adapter, anything else to the text API. Two adapters may not share a name."""

    def __init__(self, api: TextApi, adapters: tuple[Adapter, ...] | None = None) -> None:
        if adapters is None:
            adapters = ADAPTERS
        self.api = api
        self.context = AdapterContext(api)
        handlers: dict[str, Handler] = {}
        names: set[str] = set()
        for adapter in adapters:
            if adapter.name in names:
                raise ValueError(f"two adapters are named {adapter.name!r}")
            names.add(adapter.name)
            for op, handler in adapter.ops.items():
                handlers[f"{adapter.name}.{op}"] = handler
        self._handlers = MappingProxyType(handlers)

    @property
    def ops(self) -> tuple[str, ...]:
        """Every op a client may call: the text API's and each adapter's (``name.op``), sorted."""
        return tuple(sorted((*self.api.ops, *self._handlers)))

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        if not isinstance(op, str) or "." not in op:
            result = self.api.call(op, args)
            if op == "library":  # the client learns adapter ops from the same answer as the core ones
                result = {**result, "ops": list(self.ops)}
            return result
        handler = self._handlers.get(op)
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        context = self.context
        return self.api.serialized(lambda given: handler(context, given), args)

    def image(self, ref: str) -> tuple[str, bytes]:
        return self.api.image(ref)


__all__ = ["AppApi"]
