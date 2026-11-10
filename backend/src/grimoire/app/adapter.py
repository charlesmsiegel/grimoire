"""The app's adapter contract (108): how a Wave 7 sprint adds the JSON ops its screens need.

The text API (044) is the core: ``library``, ``chats``, ``open``, ``post``... Each UI sprint that needs more
(source CRUD, an import preview, provider settings) adds an **adapter**: a named set of ops over the existing
domain services, never a second store. Adapter ops are called as ``<adapter>.<op>`` (``sources.list``) on the
same ``POST /api/<op>`` route, with the same token, and run through ``TextApi.serialized``: under the library's
one writer lock, with every service's refusal answered as ``ApiRefused`` exactly as a core op's would be.
``appapi.AppApi`` does that dispatch.

Each sprint owns one module of ``grimoire.app.adapters`` (``sources`` 109, ``imports`` 110, ``play`` 111,
``providers`` 112, ``campaign`` 113, ``media`` 114, ``shell`` 108). The registry imports all seven, so a sprint
only ever edits its own module and nobody edits a shared list. See docs/APP_SHELL.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.textapi import TextApi

#: An adapter's name and each op's own name: lower case, ``_`` between words. The full op is ``name.op``.
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


@dataclass(frozen=True)
class AdapterContext:
    """What an adapter op works with: the open library and the services the text API already holds, acting as
    the library owner through the app's one trusted handle (044). ``core(op, args)`` runs a core text API op
    (``open``, to answer with the scene view, say) inside the same serialized call."""

    api: TextApi

    @property
    def store(self):  # type: ignore[no-untyped-def]
        return self.api.store

    @property
    def authority(self):  # type: ignore[no-untyped-def]
        return self.api.authority

    @property
    def owner(self) -> str:
        return self.api.owner

    @property
    def handle(self):  # type: ignore[no-untyped-def]
        """The owner's trusted handle: the library security principal every adapter write must use."""
        return self.api.owner_handle

    @property
    def chats(self):  # type: ignore[no-untyped-def]
        return self.api.chats

    @property
    def runner(self):  # type: ignore[no-untyped-def]
        return self.api.runner

    @property
    def quick(self):  # type: ignore[no-untyped-def]
        return self.api.quick

    @property
    def images(self):  # type: ignore[no-untyped-def]
        return self.api.images

    @property
    def runtime(self):  # type: ignore[no-untyped-def]
        return self.api.runtime

    def core(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        return self.api.call(op, dict(args or {}))


#: An adapter op: the context and the request's JSON object in, a JSON object out; ``ApiRefused`` to refuse.
Handler = Callable[[AdapterContext, Mapping[str, object]], dict]


@dataclass(frozen=True)
class Adapter:
    """One sprint's ops. ``owner`` names the sprint ("109"); ``ops`` maps each op's own name to its handler."""

    name: str
    owner: str
    ops: Mapping[str, Handler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise ValueError(f"adapter name {self.name!r} must match {NAME.pattern}")
        if not isinstance(self.owner, str) or not self.owner:
            raise ValueError(f"adapter {self.name!r} must name its owning sprint")
        for op, handler in self.ops.items():
            if not isinstance(op, str) or not NAME.fullmatch(op):
                raise ValueError(f"op {op!r} of adapter {self.name!r} must match {NAME.pattern}")
            if not callable(handler):
                raise ValueError(f"op {self.name}.{op} is not callable")
        object.__setattr__(self, "ops", MappingProxyType(dict(self.ops)))


__all__ = ["NAME", "Adapter", "AdapterContext", "Handler"]
