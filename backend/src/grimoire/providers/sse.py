"""Read an OpenAI-compatible chat-completions SSE stream, one line at a time.

Pure parsing, no I/O. ``feed_line`` raises only ``ProviderError``, so a shape
nobody expected fails the attempt instead of crashing the caller. Usage is
normalized to the Wave 1 ``TokenUsage`` meaning: ``input_tokens`` is the whole
prompt (this wire shape already counts cached tokens inside ``prompt_tokens``),
cache reads and reasoning are slices, and anything unreported is ``None``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from grimoire.core.inference import TokenUsage

from .errors import ProviderError

SSE_FIELDS = (b"data:", b":", b"event:", b"id:", b"retry:")
DIAGNOSTIC_LIMIT = 300


@dataclass
class StreamState:
    """What one attempt's stream has said so far. Holds reply text: never log it."""

    deltas: list[str] = field(default_factory=list, repr=False)
    finish_reason: str | None = None
    served_model: str | None = None
    usage_block: dict | None = None
    done: bool = False
    saw_sse: bool = False

    @property
    def text(self) -> str:
        return "".join(self.deltas)


def finished(state: StreamState) -> bool:
    """The provider said why it stopped. A ``null`` finish reason says nothing."""
    return bool(state.finish_reason)


def _bad(error_type: str) -> ProviderError:
    return ProviderError("bad_response", error_type=error_type)


def error_from_object(error: object) -> ProviderError:
    """Classify an ``{"error": {...}}`` object by its code, keeping its text out of str/repr."""
    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    kind_of = error.get("type") if isinstance(error, dict) else None
    number = code if isinstance(code, int) and not isinstance(code, bool) else (
        int(code) if isinstance(code, str) and code.isdigit() and len(code) <= 3 else None)
    if number == 429:
        kind = "rate_limit"
    elif number is not None and 500 <= number <= 599:
        kind = "server_error"
    else:
        kind = "provider_error"
    return ProviderError(
        kind,
        error_type=kind_of if isinstance(kind_of, str) else None,
        error_code=str(code)[:64] if isinstance(code, (int, str)) and not isinstance(
            code, bool) else None,
        diagnostic=message[:DIAGNOSTIC_LIMIT] if isinstance(message, str) else None,
    )


def classify_non_sse(body: bytes) -> ProviderError:
    """A 200 that is not an event stream: a JSON error object, or garbage."""
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _bad("not_sse")
    if isinstance(value, dict) and "error" in value:
        return error_from_object(value["error"])
    return _bad("not_sse")


def feed_line(state: StreamState, line: bytes) -> str | None:
    """Fold one stream line into ``state``; return its text delta, if any."""
    line = line.rstrip(b"\r\n")
    if not line or state.done:
        return None
    if not line.startswith(SSE_FIELDS):
        if not state.saw_sse:
            raise _bad("not_sse")
        return None  # an unknown field inside a stream is ignored, as SSE requires
    state.saw_sse = True
    if not line.startswith(b"data:"):
        return None
    payload = line[5:]
    if payload.startswith(b" "):
        payload = payload[1:]
    if payload == b"[DONE]":
        state.done = True
        return None
    try:
        chunk = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _bad("invalid_json") from None
    if not isinstance(chunk, dict):
        raise _bad("chunk_not_object")
    if "error" in chunk:
        raise error_from_object(chunk["error"])
    model = chunk.get("model")
    if isinstance(model, str) and model:
        state.served_model = model
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        state.usage_block = usage
    choices = chunk.get("choices", [])
    if not isinstance(choices, list) or not all(isinstance(c, dict) for c in choices):
        raise _bad("bad_choices")
    if not choices:
        return None
    choice = choices[0]
    reason = choice.get("finish_reason")
    if isinstance(reason, str) and reason:
        state.finish_reason = reason
    delta = choice.get("delta")
    if delta is None:
        return None
    if not isinstance(delta, dict):
        raise _bad("bad_delta")
    content = delta.get("content")
    if content is None:
        return None
    if not isinstance(content, str):
        raise _bad("bad_content")
    if not content:
        return None
    state.deltas.append(content)
    return content


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _detail(block: dict, outer: str, inner: str) -> int | None:
    details = block.get(outer)
    return _count(details.get(inner)) if isinstance(details, dict) else None


def normalize_usage(block: object) -> tuple[TokenUsage, str | None]:
    """Map the documented OpenAI usage fields onto ``TokenUsage``; never raises.

    Inconsistent slices (cache larger than the prompt, reasoning larger than
    the output) are dropped to ``None`` and noted; the whole counts are kept.
    """
    if not isinstance(block, dict):
        return TokenUsage(None, None), None
    prompt = _count(block.get("prompt_tokens"))
    completion = _count(block.get("completion_tokens"))
    cached = _detail(block, "prompt_tokens_details", "cached_tokens")
    reasoning = _detail(block, "completion_tokens_details", "reasoning_tokens")
    notes = []
    if cached is not None and prompt is not None and cached > prompt:
        cached = None
        notes.append("cache slice exceeded prompt_tokens")
    if reasoning is not None and completion is not None and reasoning > completion:
        reasoning = None
        notes.append("reasoning slice exceeded completion_tokens")
    usage = TokenUsage(prompt, completion, cache_read_tokens=cached, reasoning_tokens=reasoning)
    return usage, "; ".join(notes) or None
