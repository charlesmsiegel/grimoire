"""Trusted provider configuration: endpoints, routes, timeouts and credentials.

Endpoints (where a key may be sent) are fixed by trusted application code when
the adapter is built. Callers only choose a registered provider and a model.
"""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from grimoire.security import InvalidResourcePath, ResourcePath

MAX_TOKENS_FIELDS = ("max_tokens", "max_completion_tokens")


class Secret:
    """An API key. Printable ASCII only, so it is safe in an HTTP header, and
    never shown by ``repr`` or ``str``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if (type(value) is not str or not value
                or not all(0x21 <= ord(char) <= 0x7E for char in value)):
            raise ValueError("an API key must be nonempty printable ASCII without spaces")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a Secret cannot be serialized")


def _positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(
            value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number, not {value!r}")
    return float(value)


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Seconds: to connect, per blocking read, and for one whole attempt."""

    connect: float = 10.0
    read: float = 60.0
    total: float = 300.0

    def __post_init__(self) -> None:
        for name in ("connect", "read", "total"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class AdapterLimits:
    max_stream_bytes: int = 8 * 2**20
    max_line_bytes: int = 2**20
    max_retry_wait: float = 30.0
    usage_grace: float = 5.0

    def __post_init__(self) -> None:
        for name in ("max_stream_bytes", "max_line_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int")
        for name in ("max_retry_wait", "usage_grace"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))


def _loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def provider_resource(provider_id: object) -> ResourcePath:
    """The security address ``providers/<id>``; the id is one plain ASCII segment."""
    if type(provider_id) is not str or not provider_id.isascii():
        raise ValueError(f"provider id must be an ASCII string, not {provider_id!r}")
    try:
        return ResourcePath(("providers", provider_id))
    except InvalidResourcePath as error:
        raise ValueError(f"invalid provider id {provider_id!r}: {error}") from None


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    """Where one provider lives. Trusted configuration, never caller input."""

    provider_id: str
    base_url: str
    needs_key: bool = True
    max_tokens_field: str = "max_tokens"
    timeouts: Timeouts = field(default_factory=Timeouts)

    def __post_init__(self) -> None:
        provider_resource(self.provider_id)
        if type(self.base_url) is not str or not self.base_url.isascii():
            raise ValueError("base_url must be an ASCII string")
        parts = urlsplit(self.base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("base_url must be an https (or loopback http) URL")
        if parts.username is not None or parts.password is not None or parts.query or \
                parts.fragment or "@" in parts.netloc:
            raise ValueError("base_url must not carry credentials, a query or a fragment")
        parts.port  # raises ValueError for a malformed port
        if parts.scheme == "http" and not _loopback_host(parts.hostname):
            raise ValueError("plain http is allowed only for a loopback endpoint")
        if self.max_tokens_field not in MAX_TOKENS_FIELDS:
            raise ValueError(f"max_tokens_field must be one of {MAX_TOKENS_FIELDS}")
        if type(self.needs_key) is not bool:
            raise ValueError("needs_key must be a bool")
        if not isinstance(self.timeouts, Timeouts):
            raise ValueError("timeouts must be Timeouts")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def scheme(self) -> str:
        return urlsplit(self.base_url).scheme

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def port(self) -> int:
        parts = urlsplit(self.base_url)
        return parts.port or (443 if parts.scheme == "https" else 80)

    @property
    def host_label(self) -> str:
        parts = urlsplit(self.base_url)
        host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
        return host if parts.port is None else f"{host}:{parts.port}"

    @property
    def loopback_literal(self) -> bool:
        return _loopback_host(self.host)

    @property
    def completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"


class ProviderRegistry:
    """The trusted, fixed set of endpoints an adapter may reach."""

    def __init__(self, endpoints: Iterable[ProviderEndpoint]) -> None:
        self._endpoints: dict[str, ProviderEndpoint] = {}
        for endpoint in endpoints:
            if not isinstance(endpoint, ProviderEndpoint):
                raise ValueError(f"not a provider endpoint: {endpoint!r}")
            if endpoint.provider_id in self._endpoints:
                raise ValueError(f"duplicate provider id {endpoint.provider_id!r}")
            self._endpoints[endpoint.provider_id] = endpoint

    def get(self, provider_id: str) -> ProviderEndpoint:
        return self._endpoints[provider_id]

    def __contains__(self, provider_id: object) -> bool:
        return provider_id in self._endpoints

    def __repr__(self) -> str:
        return f"ProviderRegistry({sorted(self._endpoints)!r})"


@dataclass(frozen=True, slots=True)
class RouteChoice:
    """What a caller may choose: a registered provider and one of its models."""

    provider_id: str
    model: str

    def __post_init__(self) -> None:
        provider_resource(self.provider_id)
        if (type(self.model) is not str or not self.model
                or not all(0x21 <= ord(char) <= 0x7E for char in self.model)):
            raise ValueError("model must be nonempty printable ASCII without spaces")


def route_label(endpoint: ProviderEndpoint, model: str) -> str:
    """The ledger ``route``: provider, host (so loopback is visible) and model."""
    return f"{endpoint.provider_id}@{endpoint.host_label}/{model}"


class Credentials(Protocol):
    """Trusted code that knows provider keys. Never reachable from a worker handle."""

    def api_key(self, provider_id: str) -> Secret | None: ...


class StaticCredentials:
    """Keys held in memory (tests, or an app that loaded them itself)."""

    def __init__(self, keys: Mapping[str, str]) -> None:
        self._keys = {provider: Secret(key) for provider, key in keys.items()}

    def api_key(self, provider_id: str) -> Secret | None:
        return self._keys.get(provider_id)

    def __repr__(self) -> str:
        return f"StaticCredentials({sorted(self._keys)!r})"


class EnvironmentCredentials:
    """Keys read from environment variables when a call needs them.

    Stands in for keys under the reserved ``secrets/`` root until the native
    file store (010) exists.
    """

    def __init__(self, variables: Mapping[str, str]) -> None:
        self._variables = dict(variables)

    def api_key(self, provider_id: str) -> Secret | None:
        variable = self._variables.get(provider_id)
        value = os.environ.get(variable) if variable else None
        return Secret(value) if value else None

    def __repr__(self) -> str:
        return f"EnvironmentCredentials({self._variables!r})"
