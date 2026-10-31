"""Provider adapter: issue only planned, authorized model calls and book every attempt.

An adapter package (network, clock, environment), not a pure one. See
``docs/PROVIDERS.md`` for the contract.
"""

from .cancel import CancelToken
from .config import (
    AdapterLimits, Credentials, EnvironmentCredentials, ProviderEndpoint, ProviderRegistry,
    RouteChoice, Secret, StaticCredentials, Timeouts, provider_resource, route_label,
)
from .errors import KINDS, RETRYABLE, CallRefused, ProviderError, TransportError
from .request import (
    ChatMessage, ModelRequest, envelope, open_envelope, request_body, request_digest,
)

__all__ = [
    "AdapterLimits", "CallRefused", "CancelToken", "ChatMessage", "Credentials",
    "EnvironmentCredentials", "KINDS", "ModelRequest", "ProviderEndpoint", "ProviderError",
    "ProviderRegistry", "RETRYABLE", "RouteChoice", "Secret", "StaticCredentials", "Timeouts",
    "TransportError", "envelope", "open_envelope", "provider_resource", "request_body",
    "request_digest", "route_label",
]
