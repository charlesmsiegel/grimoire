"""The exact bytes a call sends, and the envelope a human or app authorizes.

The authorized payload binds the destination URL and the body together, so an
approval for one endpoint cannot be spent on another.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from .config import ProviderEndpoint

ROLES = ("system", "user", "assistant")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if type(self.content) is not str:
            raise ValueError("message content must be a string")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """An opaque prompt with a required output bound, so every call is capped."""

    messages: tuple[ChatMessage, ...] = field(repr=False)
    max_output_tokens: int = 0
    temperature: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.messages, (str, bytes)) or not isinstance(self.messages, Iterable):
            raise ValueError("messages must be a collection of ChatMessage")
        messages = tuple(self.messages)
        if not messages or not all(isinstance(m, ChatMessage) for m in messages):
            raise ValueError("a request needs at least one ChatMessage")
        object.__setattr__(self, "messages", messages)
        tokens = self.max_output_tokens
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            raise ValueError("max_output_tokens must be an int >= 1")
        temperature = self.temperature
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(
                temperature, (int, float)) or not math.isfinite(temperature)):
            raise ValueError("temperature must be a finite number or None")


def _dumps(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def request_body(request: ModelRequest, endpoint: ProviderEndpoint, model: str) -> bytes:
    """Compact, deterministic, ASCII-only JSON for one streaming completion."""
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        endpoint.max_tokens_field: request.max_output_tokens,
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return _dumps(body)


def request_digest(request: ModelRequest) -> str:
    """Identifies a request's content without holding its text."""
    return hashlib.sha256(_dumps([
        [[m.role, m.content] for m in request.messages],
        request.max_output_tokens, request.temperature,
    ])).hexdigest()


def envelope(url: str, body: bytes) -> bytes:
    """The authorized payload: destination and exact body, canonically encoded."""
    return json.dumps({"body": body.decode("ascii"), "url": url}, ensure_ascii=True,
                      separators=(",", ":"), sort_keys=True).encode("ascii")


def open_envelope(payload: bytes) -> tuple[str, bytes]:
    value = json.loads(payload)
    return value["url"], value["body"].encode("ascii")
