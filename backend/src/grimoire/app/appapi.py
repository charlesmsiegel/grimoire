"""What the app's server calls for ``POST /api/<op>`` (108): the text API (044) plus every sprint's adapter ops.

A dotted op (``sources.list``) goes to its adapter, run through ``TextApi.serialized``; anything else goes to the
text API itself. ``library`` lists every op, core and adapter, so a page can tell which of its actions exist.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

from grimoire.textapi import ApiRefused, TextApi, refusal

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
        handlers: dict[str, tuple[Adapter, Handler]] = {}
        names: set[str] = set()
        for adapter in adapters:
            if adapter.name in names:
                raise ValueError(f"two adapters are named {adapter.name!r}")
            names.add(adapter.name)
            for op, handler in adapter.ops.items():
                handlers[f"{adapter.name}.{op}"] = (adapter, handler)
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
        entry = self._handlers.get(op)
        if entry is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        adapter, handler = entry
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        context = self.context

        def run(given: Mapping[str, object]) -> dict:
            try:
                return handler(context, given)
            except ApiRefused:
                raise
            except Exception as error:
                # The text API's own mapping comes first: an adapter can add reasons, never rename "changed".
                reason = adapter.refusal(error) if refusal(error) is None else None
                if reason is None:
                    raise
                raise ApiRefused(reason, str(error)) from None

        result = self.api.serialized(run, args)
        _check_answer(op, result)
        return result

    def image(self, ref: str) -> tuple[str, bytes]:
        return self.api.image(ref)


def _check_answer(op: str, result: object) -> None:
    """An op answers one JSON object; anything else is the adapter's bug, raised here (the server logs it and
    answers 500) rather than found while the answer is half written."""
    if not isinstance(result, dict):
        raise TypeError(f"op {op} answered {type(result).__name__}, not a JSON object")
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise TypeError(f"op {op} answered something that is not JSON ({error})") from None


__all__ = ["AppApi"]
