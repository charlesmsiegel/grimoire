"""The app's adapter contract (108): how a Wave 7 sprint adds the JSON ops its screens need.

The text API (044) is the core: ``library``, ``chats``, ``open``, ``post``... Each UI sprint that needs more
(source CRUD, an import preview, provider settings) adds an **adapter**: a named set of ops over the existing
domain services, never a second store. Adapter ops are called as ``<adapter>.<op>`` (``sources.list``) on the
same ``POST /api/<op>`` route, with the same token, and run through ``TextApi.serialized``: under the library's
one writer lock, with every service's refusal answered as ``ApiRefused`` exactly as a core op's would be.
``appapi.AppApi`` does that dispatch.

Each sprint owns one module of ``grimoire.app.adapters`` (``sources`` 109, ``imports`` 110, ``play`` 111,
``providers`` 112, ``campaign`` 113, ``media`` 114, ``shell`` 108), and ``inbox`` gathers what waits on the person
from the others' services. The registry imports them all, so a sprint only ever edits its own module and nobody
edits a shared list. See docs/APP_SHELL.md.

An adapter module imports this module and domain packages only, never ``grimoire.app`` itself, its server or
``appapi``: the registry imports every adapter while ``grimoire.app`` is still loading, so such an import would
close a cycle.
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
    (``open``, to answer with the scene view, say) inside the same serialized call.

    ``store`` and ``authority`` are here so an adapter can build its owning domain service over the same open
    library (a source service, an import planner). Every write goes through such a service with ``handle``: an
    adapter never asks the authority for a handle of its own or writes the store directly."""

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
        # The lock is re-entrant, so the core op runs inside this op's serialized call, not after it.
        return self.api.call(op, dict(args or {}))


#: An adapter op: the context and the request's JSON object in, a JSON object out; ``ApiRefused`` to refuse.
Handler = Callable[[AdapterContext, Mapping[str, object]], dict]


@dataclass(frozen=True)
class Adapter:
    """One sprint's ops. ``owner`` names the sprint ("109"); ``ops`` maps each op's own name to its handler.

    ``refusals`` maps a refusal its services raise that the text API does not know (``CardError``,
    ``ImportRefused``, a provider's ``CallRefused``) to the machine-readable reason the client is answered with,
    as a 409 unless the reason is one of the text API's not-found or bad-request reasons. A subclass matches its
    base. Any other exception is a bug: logged by the server and answered 500."""

    name: str
    owner: str
    ops: Mapping[str, Handler] = field(default_factory=dict)
    refusals: Mapping[type[BaseException], str] = field(default_factory=dict)

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
        for kind, reason in self.refusals.items():
            if not (isinstance(kind, type) and issubclass(kind, Exception)):
                raise ValueError(f"adapter {self.name!r}: a refusal is an exception class, not {kind!r}")
            if not isinstance(reason, str) or not NAME.fullmatch(reason):
                raise ValueError(f"adapter {self.name!r}: refusal reason {reason!r} must match {NAME.pattern}")
        object.__setattr__(self, "ops", MappingProxyType(dict(self.ops)))
        object.__setattr__(self, "refusals", MappingProxyType(dict(self.refusals)))

    def refusal(self, error: BaseException) -> str | None:
        """The reason ``error`` is answered with, or None when it is not one of this adapter's refusals."""
        for kind in type(error).__mro__:
            if kind in self.refusals:
                return self.refusals[kind]
        return None


__all__ = ["NAME", "Adapter", "AdapterContext", "Handler"]
